"""Port availability check on an x-ui VPS: parsers, verdicts, endpoint."""
from unittest import mock
from unittest.mock import AsyncMock

import pytest

from app.core import port_check
from app.core.ssh import DeployResult
from app.models import ChainChannel, ProxyChain, Server, XuiClient as XuiClientModel, XuiServer

SS_OUT = """\
tcp   LISTEN 0 511    0.0.0.0:443       0.0.0.0:* users:(("nginx",pid=685,fd=6),("nginx",pid=686,fd=6))
tcp   LISTEN 0 4096         *:8443            *:* users:(("xray-linux-amd6",pid=702,fd=9))
tcp   LISTEN 0 4096      [::]:443          [::]:* users:(("nginx",pid=685,fd=7))
tcp   LISTEN 0 128  127.0.0.1:62789     0.0.0.0:* users:(("xray-linux-amd6",pid=702,fd=3))
udp   UNCONN 0 0      0.0.0.0:35871     0.0.0.0:* users:(("warp-plus",pid=9,fd=3))
tcp   LISTEN 0 128    0.0.0.0:22        0.0.0.0:*
"""

UFW_ACTIVE = """\
Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
20000:20010/tcp            ALLOW       Anywhere
OpenSSH                    ALLOW       Anywhere
8443/tcp (v6)              ALLOW       Anywhere (v6)
"""


class TestParsers:
    def test_candidates_default_and_dedupe(self):
        assert port_check.parse_candidates(None) == [443, 8443, 2443]
        assert port_check.parse_candidates(" 8443, 443,8443 ") == [8443, 443]

    @pytest.mark.parametrize("raw", ["abc", "0", "70000", "443,-1"])
    def test_candidates_reject_junk(self, raw):
        with pytest.raises(ValueError):
            port_check.parse_candidates(raw)

    def test_candidates_cap(self):
        with pytest.raises(ValueError):
            port_check.parse_candidates(",".join(str(p) for p in range(1000, 1030)))

    def test_ss_listeners(self):
        got = port_check.parse_ss_listeners(SS_OUT)
        assert got[443] == {"nginx"}
        assert got[8443] == {"xray-linux-amd6"}
        assert got[35871] == {"warp-plus"}
        # Non-root ss hides the process — still counts as taken.
        assert got[22] == {"?"}

    def test_ufw_active_rules_and_ranges(self):
        st = port_check.parse_ufw(UFW_ACTIVE)
        assert st.port_state(443) == "allow"
        assert st.port_state(8443) == "allow"
        assert st.port_state(20005) == "allow"
        assert st.port_state(2443) == "closed"

    @pytest.mark.parametrize("text,expected", [
        ("Status: inactive", "inactive"),
        ("Status: absent", "absent"),
        ("", "unknown"),
    ])
    def test_ufw_other_states(self, text, expected):
        assert port_check.parse_ufw(text).port_state(443) == expected

    def test_split_sections(self):
        out = port_check.split_sections("##SS\nline1\nline2\n##UFW\nStatus: active\n")
        assert out == {"SS": "line1\nline2", "UFW": "Status: active"}


class TestClassify:
    def _run(self, **kw):
        base = dict(
            candidates=[443, 8443, 2443, 34011],
            inbounds=[{"id": 4, "port": 8443, "remark": "vless-xhttp-reality", "protocol": "vless", "enable": True}],
            known_inbound_ids=set(),
            mode="bare",
            panel_port=34011,
            listeners=port_check.parse_ss_listeners(SS_OUT),
            ufw=port_check.parse_ufw(UFW_ACTIVE),
        )
        base.update(kw)
        return {r["port"]: r for r in port_check.classify_ports(**base)}

    def test_verdicts(self):
        rows = self._run()
        assert rows[8443]["status"] == "taken"
        assert rows[8443]["reason"].startswith("inbound #4")
        assert rows[8443]["inbounds"][0]["known_here"] is False
        assert rows[443]["status"] == "taken"  # nginx on a bare box
        assert rows[443]["listeners"] == ["nginx"]
        assert rows[2443]["status"] == "free"
        assert rows[2443]["ufw"] == "closed"
        assert rows[34011]["status"] == "reserved"

    def test_xui_pro_reserves_443(self):
        rows = self._run(mode="xui-pro")
        assert rows[443]["status"] == "reserved"

    def test_known_here(self):
        rows = self._run(known_inbound_ids={4})
        assert rows[8443]["inbounds"][0]["known_here"] is True

    def test_panel_only_when_ssh_missing(self):
        rows = self._run(listeners=None, ufw=None)
        assert rows[443]["status"] == "free"
        assert rows[443]["listeners"] is None
        assert rows[443]["ufw"] is None


def _seed(session, *, with_creds=True):
    srv = Server(
        name="p", host="192.0.2.10", port=22, user="root",
        auth_type="password", password="pw" if with_creds else None,
    )
    session.add(srv)
    session.commit()
    session.refresh(srv)
    xs = XuiServer(
        server_id=srv.id, api_token="tok", panel_user="u", panel_pass="p",
        panel_port=34011, panel_basepath="/t", mode="bare",
    )
    session.add(xs)
    session.commit()
    session.refresh(xs)
    return xs


def _panel(inbounds):
    inst = mock.MagicMock()
    inst.list_inbounds = AsyncMock(return_value=inbounds)
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    return inst


class TestEndpoint:
    INBOUNDS = [
        {"id": 2, "port": 8443, "remark": "mine", "protocol": "vless", "enable": True},
        {"id": 4, "port": 443, "remark": "foreign", "protocol": "vless", "enable": True},
        {"id": 5, "port": 25000, "remark": "VPN-X", "protocol": "vless", "enable": True},
    ]

    def test_full_check(self, client, session, admin_user, auth_headers):
        xs = _seed(session)
        session.add(XuiClientModel(
            xui_server_id=xs.id, inbound_remote_id=2, client_uuid="u", label="pi-1",
            inbound_protocol="vless", inbound_port=8443, inbound_remark="mine", config_json="{}",
        ))
        chain = ProxyChain(name="c", exit_xui_server_id=xs.id, relay_xui_server_id=xs.id, exit_sni="example.com", status="deployed")
        session.add(chain)
        session.commit()
        session.refresh(chain)
        session.add(ChainChannel(
            chain_id=chain.id, name="x", order=0, exit_port=1, relay_port=25000,
            client_sni="example.com", relay_inbound_remote_id=5,
        ))
        session.commit()

        ssh = AsyncMock(return_value=DeployResult(
            ok=True, exit_code=0, stdout="##SS\n" + SS_OUT + "##UFW\n" + UFW_ACTIVE,
        ))
        with (
            mock.patch("app.api.xui.XuiClient", return_value=_panel(self.INBOUNDS)),
            mock.patch("app.core.ssh.exec_remote_script", ssh),
        ):
            resp = client.get(
                f"/api/xui/servers/{xs.id}/ports?candidates=443,8443,2443,25000",
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ssh_error"] is None and body["panel_error"] is None
        rows = {r["port"]: r for r in body["ports"]}
        assert rows[443]["status"] == "taken"
        assert rows[443]["inbounds"][0]["known_here"] is False
        assert rows[8443]["inbounds"][0]["known_here"] is True
        assert rows[25000]["inbounds"][0]["known_here"] is True  # chain relay
        assert rows[2443]["status"] == "free"
        assert rows[2443]["ufw"] == "closed"
        # Read-only probe: exactly one SSH round-trip.
        assert ssh.await_count == 1

    def test_no_ssh_creds_falls_back_to_panel(self, client, session, admin_user, auth_headers):
        xs = _seed(session, with_creds=False)
        with mock.patch("app.api.xui.XuiClient", return_value=_panel(self.INBOUNDS)):
            resp = client.get(f"/api/xui/servers/{xs.id}/ports", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ssh_error"]
        rows = {r["port"]: r for r in body["ports"]}
        assert rows[443]["status"] == "taken"
        assert rows[2443]["status"] == "free"
        assert rows[2443]["listeners"] is None

    def test_bad_candidates_400(self, client, session, admin_user, auth_headers):
        xs = _seed(session)
        resp = client.get(f"/api/xui/servers/{xs.id}/ports?candidates=abc", headers=auth_headers)
        assert resp.status_code == 400

    def test_unknown_server_404(self, client, admin_user, auth_headers):
        resp = client.get("/api/xui/servers/999/ports", headers=auth_headers)
        assert resp.status_code == 404
