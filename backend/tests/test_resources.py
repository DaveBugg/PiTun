"""Resource view and limits: what PiTun costs the box, and the optional caps."""
import asyncio
from types import SimpleNamespace

import pytest

from app.core import proc_stats, resource_limits as rl

HOST_MEM = 4 * 1024 ** 3


# ── reading Docker's numbers like `docker stats` does ────────────────────────

def _stats(cpu_total, cpu_pre, sys_total, sys_pre, usage, inactive, limit, online=4):
    return {
        "cpu_stats": {"cpu_usage": {"total_usage": cpu_total}, "system_cpu_usage": sys_total, "online_cpus": online},
        "precpu_stats": {"cpu_usage": {"total_usage": cpu_pre}, "system_cpu_usage": sys_pre},
        "memory_stats": {"usage": usage, "limit": limit, "stats": {"inactive_file": inactive}},
    }


def test_cpu_is_percent_of_one_core_and_cache_is_not_counted():
    n = proc_stats.container_numbers(
        _stats(2_000_000, 1_000_000, 40_000_000, 30_000_000, 300 * 1024 ** 2, 100 * 1024 ** 2, HOST_MEM), HOST_MEM)
    assert n["cpu_percent"] == 40.0                  # 10% of the machine's 4 cores
    assert n["mem_mb"] == 200.0
    assert n["mem_limit_mb"] is None                 # the limit is the whole host: not limited


def test_a_real_limit_is_reported():
    n = proc_stats.container_numbers(_stats(0, 0, 0, 0, 10 * 1024 ** 2, 0, 512 * 1024 ** 2), HOST_MEM)
    assert n["mem_limit_mb"] == 512.0 and n["cpu_percent"] == 0.0     # first sample: no delta yet


def test_cgroup_v1_style_cache_key_is_understood():
    s = _stats(0, 0, 0, 0, 100 * 1024 ** 2, 0, HOST_MEM)
    s["memory_stats"]["stats"] = {"total_inactive_file": 60 * 1024 ** 2}
    assert proc_stats.container_numbers(s, HOST_MEM)["mem_mb"] == 40.0


# ── validation ───────────────────────────────────────────────────────────────

def test_empty_means_no_limits():
    e = rl.empty()
    assert e["xray_mem_mb"] is None
    assert all(e[t] == {"mem_mb": None, "cpus": None} for t in rl.TARGETS)


def test_values_are_normalised(monkeypatch):
    monkeypatch.setattr(rl, "host_limits", lambda: {"mem_mb": 4096, "cpus": 4.0})
    out = rl.validate({"backend": {"mem_mb": "1024", "cpus": "1.5"}, "xray_mem_mb": 512, "nginx": {"mem_mb": ""}})
    assert out["backend"] == {"mem_mb": 1024, "cpus": 1.5}
    assert out["nginx"] == {"mem_mb": None, "cpus": None} and out["xray_mem_mb"] == 512


@pytest.mark.parametrize("bad,fragment", [
    ({"backend": {"mem_mb": 100}}, "at least 256"),             # a backend with xray in it needs room
    ({"backend": {"mem_mb": 999999}}, "more than this box has"),
    ({"backend": {"cpus": 0.01}}, "at least 0.1"),
    ({"backend": {"cpus": 64}}, "more than this box has"),
    ({"xray_mem_mb": 10}, "at least 64"),
    ({"nginx": {"mem_mb": "lots"}}, "must be a number"),
])
def test_unsafe_or_nonsense_limits_are_refused(monkeypatch, bad, fragment):
    monkeypatch.setattr(rl, "host_limits", lambda: {"mem_mb": 4096, "cpus": 4.0})
    with pytest.raises(rl.LimitError) as e:
        rl.validate(bad)
    assert fragment in str(e.value)


# ── what is sent to Docker ───────────────────────────────────────────────────

def test_update_kwargs_for_a_limit():
    k = rl._update_kwargs(1024, 1.5, 4096)
    assert k == {"mem_limit": "1024m", "memswap_limit": "1024m", "cpu_period": 100000, "cpu_quota": 150000}


def test_no_limit_is_the_whole_host_because_docker_cannot_clear_one():
    k = rl._update_kwargs(None, None, 4096)
    # swap room above memory in the same call: runc refuses `swap=-1` with a rise
    assert k["mem_limit"] == "4096m" and k["memswap_limit"] == "8192m" and k["cpu_quota"] == -1


class FakeContainer:
    def __init__(self, name, labels=None, fail=None, host_config=None):
        self.name, self.labels, self.id = name, labels or {}, name
        self.fail, self.calls = fail, []
        self.attrs = {"HostConfig": host_config or {}}

    def reload(self):
        return None

    def update(self, **kw):
        if self.fail:
            raise self.fail
        self.calls.append(kw)


class FakeClient:
    def __init__(self, containers):
        self._c = containers
        self.containers = SimpleNamespace(list=lambda: self._c)


def test_apply_reaches_each_container_of_the_target(monkeypatch):
    monkeypatch.setattr(rl, "host_limits", lambda: {"mem_mb": 4096, "cpus": 4.0})
    cs = [FakeContainer("pitun-backend"), FakeContainer("pitun-nginx"),
          FakeContainer("pitun-naive-1", {"pitun": "naive"}), FakeContainer("pitun-naive-2", {"pitun": "naive"}),
          FakeContainer("somebody-elses")]
    monkeypatch.setattr(rl, "docker_client", lambda: FakeClient(cs))
    limits = rl.validate({"backend": {"mem_mb": 1024}, "naive": {"mem_mb": 128, "cpus": 0.5}})
    res = asyncio.run(rl.apply(limits))
    assert {r["container"] for r in res} == {"pitun-backend", "pitun-nginx", "pitun-naive-1", "pitun-naive-2"}
    assert cs[0].calls[0]["mem_limit"] == "1024m"
    assert cs[2].calls[0]["cpu_quota"] == 50000 and cs[3].calls[0]["mem_limit"] == "128m"
    assert cs[1].calls == []                            # nothing to remove: left exactly as it was
    assert cs[4].calls == []                            # never someone else's container


def test_a_limit_that_was_set_is_taken_off_when_the_target_is_emptied(monkeypatch):
    monkeypatch.setattr(rl, "host_limits", lambda: {"mem_mb": 4096, "cpus": 4.0})
    limited = FakeContainer("pitun-nginx", host_config={"Memory": 96 * 1024 ** 2, "CpuQuota": 50000, "CpuPeriod": 100000})
    monkeypatch.setattr(rl, "docker_client", lambda: FakeClient([limited]))
    asyncio.run(rl.apply(rl.empty()))
    first, second = limited.calls
    assert first["mem_limit"] == "4096m" and first["memswap_limit"] == "8192m" and first["cpu_quota"] == -1
    assert second == {"memswap_limit": -1}                # then the swap ceiling is lifted


def test_one_refusal_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setattr(rl, "host_limits", lambda: {"mem_mb": 4096, "cpus": 4.0})
    boom = FakeContainer("pitun-backend", fail=RuntimeError("403 Client Error: Forbidden"))
    ok = FakeContainer("pitun-nginx")
    monkeypatch.setattr(rl, "docker_client", lambda: FakeClient([boom, ok]))
    res = {r["container"]: r for r in asyncio.run(rl.apply(rl.validate({"backend": {"mem_mb": 512}})))}
    assert res["pitun-backend"]["ok"] is False and "socket proxy" in res["pitun-backend"]["error"]
    assert res["pitun-nginx"]["ok"] is True


def test_effective_limits_are_read_back(monkeypatch):
    hc = {"Memory": 512 * 1024 ** 2, "CpuQuota": 150000, "CpuPeriod": 100000}
    free = {"Memory": 0}
    monkeypatch.setattr(rl, "docker_client", lambda: FakeClient([
        FakeContainer("pitun-backend", host_config=hc), FakeContainer("pitun-nginx", host_config=free)]))
    cur = asyncio.run(rl.current())
    assert cur["backend"] == [{"container": "pitun-backend", "mem_mb": 512, "cpus": 1.5}]
    assert cur["nginx"] == [{"container": "pitun-nginx", "mem_mb": None, "cpus": None}]


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_docker(monkeypatch):
    monkeypatch.setattr(rl, "host_limits", lambda: {"mem_mb": 4096, "cpus": 4.0})
    cs = [FakeContainer("pitun-backend"), FakeContainer("pitun-nginx")]
    monkeypatch.setattr(rl, "docker_client", lambda: FakeClient(cs))
    return cs


def test_limits_default_to_none(client, admin_user, auth_headers, fake_docker):
    d = client.get("/api/resources/limits", headers=auth_headers).json()
    assert d["limits"]["backend"] == {"mem_mb": None, "cpus": None} and d["limits"]["xray_mem_mb"] is None
    assert d["host"]["mem_mb"] == 4096 and "backend" in d["targets"]


def test_put_saves_applies_and_flags_the_xray_restart(client, admin_user, auth_headers, fake_docker):
    r = client.put("/api/resources/limits", headers=auth_headers,
                   json={"backend": {"mem_mb": 1024, "cpus": 2}, "xray_mem_mb": 512})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["limits"]["backend"] == {"mem_mb": 1024, "cpus": 2.0}
    assert body["xray_restart_needed"] is True
    assert any(x["container"] == "pitun-backend" and x["ok"] for x in body["results"])
    again = client.get("/api/resources/limits", headers=auth_headers).json()["limits"]
    assert again["backend"]["mem_mb"] == 1024 and again["xray_mem_mb"] == 512
    # saving the same xray cap again does not ask for another restart
    r2 = client.put("/api/resources/limits", headers=auth_headers,
                    json={"backend": {"mem_mb": 1024}, "xray_mem_mb": 512}).json()
    assert r2["xray_restart_needed"] is False


def test_put_zero_or_empty_clears_a_limit(client, admin_user, auth_headers, fake_docker):
    client.put("/api/resources/limits", headers=auth_headers, json={"backend": {"mem_mb": 1024}, "xray_mem_mb": 300})
    r = client.put("/api/resources/limits", headers=auth_headers, json={"backend": {"mem_mb": 0, "cpus": 0}, "xray_mem_mb": 0})
    assert r.json()["limits"]["backend"] == {"mem_mb": None, "cpus": None}
    assert r.json()["limits"]["xray_mem_mb"] is None


def test_put_rejects_a_dangerous_value_and_saves_nothing(client, admin_user, auth_headers, fake_docker):
    r = client.put("/api/resources/limits", headers=auth_headers, json={"backend": {"mem_mb": 64}})
    assert r.status_code == 422 and "at least 256" in r.json()["detail"]
    assert client.get("/api/resources/limits", headers=auth_headers).json()["limits"]["backend"]["mem_mb"] is None
    assert fake_docker[0].calls == []


def test_xray_gets_its_soft_cap_from_the_saved_limit(client, admin_user, auth_headers, fake_docker):
    assert asyncio.run(rl.xray_env()) == {}
    client.put("/api/resources/limits", headers=auth_headers, json={"xray_mem_mb": 384})
    assert asyncio.run(rl.xray_env()) == {"GOMEMLIMIT": "384MiB"}


def test_reapply_at_boot_is_a_noop_when_nothing_was_set(client, fake_docker):
    asyncio.run(rl.reapply_on_boot())
    assert all(c.calls == [] for c in fake_docker)


def test_reapply_at_boot_puts_saved_limits_back(client, admin_user, auth_headers, fake_docker):
    client.put("/api/resources/limits", headers=auth_headers, json={"nginx": {"mem_mb": 64}})
    for c in fake_docker:
        c.calls.clear()
    asyncio.run(rl.reapply_on_boot())
    assert fake_docker[1].calls and fake_docker[1].calls[0]["mem_limit"] == "64m"


def test_processes_endpoint_lists_xray_backend_and_containers(client, admin_user, auth_headers, monkeypatch):
    monkeypatch.setattr(proc_stats, "_docker_rows", lambda: [
        {"name": "pitun-nginx", "kind": "container", "cpu_percent": 0.4, "mem_mb": 9.0, "mem_limit_mb": None, "note": "running"}])
    proc_stats._cache.update(ts=0.0, data=None)
    d = client.get("/api/resources/processes?refresh=true", headers=auth_headers).json()
    names = [p["name"] for p in d["processes"]]
    assert "pitun backend" in names and "pitun-nginx" in names
    assert d["host"]["cpu_count"] >= 1 and d["docker_error"] is None
    mems = [p["mem_mb"] for p in d["processes"]]
    assert mems == sorted(mems, reverse=True)


def test_processes_survive_an_unreachable_docker(client, admin_user, auth_headers, monkeypatch):
    def boom():
        raise ConnectionError("no docker")
    monkeypatch.setattr(proc_stats, "_docker_rows", boom)
    proc_stats._cache.update(ts=0.0, data=None)
    d = client.get("/api/resources/processes?refresh=true", headers=auth_headers).json()
    assert d["docker_error"] and any(p["name"] == "pitun backend" for p in d["processes"])


def test_endpoints_require_login(client):
    assert client.get("/api/resources/processes").status_code in (401, 403)
    assert client.put("/api/resources/limits", json={}).status_code in (401, 403)
