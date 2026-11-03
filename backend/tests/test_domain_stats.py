"""Domain statistics: counting, flushing, limits, cleaning, search and the
routing rules built from a selection."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.api.domain_stats import parent_domain
from app.core import domain_stats as ds
from app.core.dns_logger import process_log_line
from app.models import DomainStat, RoutingRule

LINE = "2026/11/02 12:00:00 [Info] app/dns: UDP:1.1.1.1:53 got answer: {d}. TypeA -> [{ips}], rtt: 5ms"


@pytest.fixture(autouse=True)
def _fresh_collector():
    ds._pending.clear()
    ds._dropped = 0
    ds._enabled_checked = 0.0
    yield
    ds._pending.clear()


def _seed(session, domain, hits=1, days_ago=0, ips=None):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    row = DomainStat(domain=domain, hits=hits, first_seen=when, last_seen=when, ips=json.dumps(ips or []))
    session.add(row)
    session.commit()
    return row


# ── what gets counted ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Example.COM.", "example.com"),
    ("a.b.example.com", "a.b.example.com"),
    ("1.0.0.127.in-addr.arpa", None), ("x.ip6.arpa", None),
    ("localhost", None), ("router", None), ("printer.lan", None),
    ("8.8.8.8", None), ("2001:db8::1", None), ("", None), ("bad domain.com", None),
    ("a" * 260 + ".com", None),
])
def test_clean_domain(raw, expected):
    assert ds.clean_domain(raw) == expected


def test_only_real_public_ips_are_kept():
    ds.record("example.com", ["198.18.0.5", "10.0.0.1", "127.0.0.1", "93.184.216.34", "93.184.216.34", "2606:2800:220:1::1", "junk"])
    assert ds._pending["example.com"][2] == ["93.184.216.34", "2606:2800:220:1::1"]


def test_hits_accumulate_in_memory():
    for _ in range(3):
        ds.record("example.com", [])
    ds.record("other.org", [])
    assert ds._pending["example.com"][0] == 3 and ds._pending["other.org"][0] == 1


def test_the_buffer_is_bounded(monkeypatch):
    monkeypatch.setattr(ds, "MAX_PENDING", 2)
    assert ds.record("a.com") and ds.record("b.com")
    assert ds.record("c.com") is False
    assert ds.record("a.com") is True          # an already-known domain still counts
    assert ds._dropped == 1


# ── flush ────────────────────────────────────────────────────────────────────

def test_flush_inserts_then_accumulates(client, session):
    ds.record("example.com", ["93.184.216.34"])
    ds.record("example.com", [])
    assert asyncio.run(ds.flush()) == 1
    ds.record("example.com", [])
    asyncio.run(ds.flush())
    session.expire_all()
    [row] = session.exec(select(DomainStat)).all()
    assert row.domain == "example.com" and row.hits == 3
    assert json.loads(row.ips) == ["93.184.216.34"]        # a later empty lookup does not erase them
    assert row.first_seen <= row.last_seen
    assert ds._pending == {}


def test_flush_with_nothing_is_a_noop(client):
    assert asyncio.run(ds.flush()) == 0


# ── feeding from xray's DNS lines ────────────────────────────────────────────

def test_dns_line_feeds_stats_only_when_asked(client, session):
    line = LINE.format(d="video.example.com", ips="93.184.216.34")
    asyncio.run(process_log_line(line, store_log=False, store_stats=True))
    assert "video.example.com" in ds._pending
    ds._pending.clear()
    asyncio.run(process_log_line(line, store_log=False, store_stats=False))
    assert ds._pending == {}


def test_collection_is_off_by_default_and_switchable(client, admin_user, auth_headers):
    assert asyncio.run(ds.collecting()) is False
    r = client.put("/api/domain-stats/config", json={"enabled": True}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert asyncio.run(ds.collecting()) is True


# ── limits ───────────────────────────────────────────────────────────────────

def test_prune_ages_out_old_rows(client, session):
    _seed(session, "old.example.com", days_ago=100)
    _seed(session, "new.example.com", days_ago=1)

    async def run():
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        async with AsyncSession(get_async_engine()) as s:
            return await ds.prune(s, retention_days=30)

    assert asyncio.run(run()) == 1
    session.expire_all()
    assert [r.domain for r in session.exec(select(DomainStat)).all()] == ["new.example.com"]


def test_prune_to_a_row_cap_drops_the_least_used_first(client, session):
    for name, hits in (("a.com", 50), ("b.com", 5), ("c.com", 1), ("d.com", 20)):
        _seed(session, name, hits=hits)

    async def run():
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        async with AsyncSession(get_async_engine()) as s:
            return await ds.prune(s, max_rows=2)

    assert asyncio.run(run()) == 2
    session.expire_all()
    assert sorted(r.domain for r in session.exec(select(DomainStat)).all()) == ["a.com", "d.com"]


# ── API: list / search / clean ───────────────────────────────────────────────

def test_list_search_sort_and_summary(client, admin_user, auth_headers, session):
    _seed(session, "www.youtube.com", hits=40, ips=["203.0.113.46"])
    _seed(session, "i.ytimg.com", hits=10, ips=["203.0.113.1"])
    _seed(session, "example.org", hits=2)
    d = client.get("/api/domain-stats", headers=auth_headers).json()
    assert [i["domain"] for i in d["items"]] == ["www.youtube.com", "i.ytimg.com", "example.org"]
    assert d["total"] == 3 and d["summary"]["rows"] == 3 and d["summary"]["total_hits"] == 52
    assert d["summary"]["approx_bytes"] > 0 and d["config"]["enabled"] is False

    by_name = client.get("/api/domain-stats?q=YOUTUBE", headers=auth_headers).json()
    assert [i["domain"] for i in by_name["items"]] == ["www.youtube.com"]
    by_ip = client.get("/api/domain-stats?q=203.0.113", headers=auth_headers).json()
    assert by_ip["total"] == 2
    asc = client.get("/api/domain-stats?sort=hits&order=asc", headers=auth_headers).json()
    assert asc["items"][0]["domain"] == "example.org"
    big = client.get("/api/domain-stats?min_hits=10", headers=auth_headers).json()
    assert big["total"] == 2


def test_search_treats_wildcards_literally(client, admin_user, auth_headers, session):
    _seed(session, "a_b.example.com")
    _seed(session, "axb.example.com")
    d = client.get("/api/domain-stats?q=a_b", headers=auth_headers).json()
    assert [i["domain"] for i in d["items"]] == ["a_b.example.com"]


def test_log_level_hint(client, admin_user, auth_headers):
    assert client.get("/api/domain-stats", headers=auth_headers).json()["log_level_ok"] is False


def test_prune_endpoint_variants(client, admin_user, auth_headers, session):
    for name, hits, age in (("a.com", 9, 0), ("b.com", 5, 60), ("c.com", 1, 200)):
        _seed(session, name, hits=hits, days_ago=age)
    r = client.post("/api/domain-stats/prune", json={"older_than_days": 90}, headers=auth_headers)
    assert r.json()["removed"] == 1
    r = client.post("/api/domain-stats/prune", json={"domains": ["B.com"]}, headers=auth_headers)
    assert r.json()["removed"] == 1
    r = client.post("/api/domain-stats/prune", json={"everything": True}, headers=auth_headers)
    assert r.json()["removed"] == 1 and r.json()["summary"]["rows"] == 0


def test_prune_needs_exactly_one_criterion(client, admin_user, auth_headers):
    assert client.post("/api/domain-stats/prune", json={}, headers=auth_headers).status_code == 422
    both = {"everything": True, "older_than_days": 5}
    assert client.post("/api/domain-stats/prune", json=both, headers=auth_headers).status_code == 422


def test_shrink_to_a_byte_budget(client, admin_user, auth_headers, session):
    for i in range(300):
        _seed(session, f"host-number-{i:04d}.example.com", hits=i + 1)
    before = client.get("/api/domain-stats", headers=auth_headers).json()["summary"]["approx_bytes"]
    r = client.post("/api/domain-stats/prune", json={"max_bytes": before // 3}, headers=auth_headers).json()
    assert r["removed"] > 0 and r["summary"]["approx_bytes"] <= before // 3 + 200
    left = client.get("/api/domain-stats?limit=500", headers=auth_headers).json()["items"]
    assert all(i["hits"] > 100 for i in left)       # the busiest survive


def test_lowering_the_row_cap_trims_immediately(client, admin_user, auth_headers, session):
    for i in range(150):
        _seed(session, f"d{i}.example.com", hits=i + 1)
    client.put("/api/domain-stats/config", json={"max_rows": 100}, headers=auth_headers)
    assert client.get("/api/domain-stats", headers=auth_headers).json()["total"] == 100


# ── rules from a selection ───────────────────────────────────────────────────

def test_parent_domain_heuristic():
    assert parent_domain("a.b.example.com") == "example.com"
    assert parent_domain("example.com") == "example.com"
    assert parent_domain("shop.example.co.uk") == "example.co.uk"
    assert parent_domain("x.example.com.au") == "example.com.au"


def _rules(session):
    session.expire_all()
    return session.exec(select(RoutingRule)).all()


def test_new_domain_rule_from_a_selection(client, admin_user, auth_headers, session):
    _seed(session, "www.youtube.com")
    _seed(session, "i.ytimg.com")
    r = client.post("/api/domain-stats/rules", headers=auth_headers, json={
        "domains": ["www.youtube.com", "i.ytimg.com"], "action": "proxy", "name": "Video"})
    assert r.status_code == 200, r.text
    [rule] = _rules(session)
    assert rule.rule_type == "domain" and rule.action == "proxy" and rule.name == "Video"
    assert sorted(rule.match_value.split(",")) == ["i.ytimg.com", "www.youtube.com"]


def test_rule_for_everything_matching_a_search_with_parent_domains(client, admin_user, auth_headers, session):
    for d in ("a.video.example.com", "b.video.example.com", "other.org"):
        _seed(session, d)
    r = client.post("/api/domain-stats/rules", headers=auth_headers, json={
        "query": "video.example", "use_parent": True, "action": "direct"})
    assert r.status_code == 200, r.text
    [rule] = _rules(session)
    assert rule.match_value == "example.com" and r.json()["added"] == 1


def test_ips_become_a_dst_ip_rule(client, admin_user, auth_headers, session):
    _seed(session, "a.example.com", ips=["93.184.216.34", "93.184.216.35"])
    _seed(session, "b.example.com", ips=["93.184.216.34"])
    _seed(session, "no-ip.example.com")
    r = client.post("/api/domain-stats/rules", headers=auth_headers, json={
        "domains": ["a.example.com", "b.example.com", "no-ip.example.com"], "use": "ips", "action": "block"})
    assert r.status_code == 200, r.text
    [rule] = _rules(session)
    assert rule.rule_type == "dst_ip" and sorted(rule.match_value.split(",")) == ["93.184.216.34", "93.184.216.35"]


def test_append_to_an_existing_rule_without_duplicates(client, admin_user, auth_headers, session):
    rule = RoutingRule(name="mine", rule_type="domain", match_value="example.com,domain:keep.org", action="proxy", order=10)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    _seed(session, "example.com")
    _seed(session, "keep.org")
    _seed(session, "new.example.net")
    r = client.post("/api/domain-stats/rules", headers=auth_headers, json={
        "domains": ["example.com", "keep.org", "new.example.net"], "mode": "append", "rule_id": rule.id})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1 and r.json()["already_present"] == 2
    session.expire_all()
    assert session.get(RoutingRule, rule.id).match_value.split(",") == ["example.com", "domain:keep.org", "new.example.net"]


def test_append_refuses_a_rule_of_the_wrong_kind(client, admin_user, auth_headers, session):
    rule = RoutingRule(name="ports", rule_type="port", match_value="443", action="proxy", order=10)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    _seed(session, "a.example.com")
    r = client.post("/api/domain-stats/rules", headers=auth_headers, json={
        "domains": ["a.example.com"], "mode": "append", "rule_id": rule.id})
    assert r.status_code == 409


def test_rule_validation(client, admin_user, auth_headers, session):
    _seed(session, "a.example.com")
    assert client.post("/api/domain-stats/rules", headers=auth_headers, json={"action": "proxy"}).status_code == 422
    bad = client.post("/api/domain-stats/rules", headers=auth_headers,
                      json={"domains": ["a.example.com"], "action": "node:9999"})
    assert bad.status_code == 400
    none = client.post("/api/domain-stats/rules", headers=auth_headers,
                       json={"domains": ["unknown.example.com"], "action": "proxy"})
    assert none.status_code == 422
    assert client.post("/api/domain-stats/rules", headers=auth_headers,
                       json={"domains": ["a.example.com"], "mode": "append"}).status_code == 422


def test_requires_login(client):
    assert client.get("/api/domain-stats").status_code in (401, 403)
