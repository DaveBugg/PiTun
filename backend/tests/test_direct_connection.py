"""The operator's "Direct connection" toggle: force a panel operation OFF the
active node's tunnel via SO_MARK bypass, instead of the default (through the
node). Covers the socket-option core, XuiClient wiring, and that the xui
endpoints thread the flag."""
from unittest import mock
from unittest.mock import AsyncMock

from app.core import net
from app.core.xui_api import XuiClient
from app.models import Server, XuiServer


class TestBypassSocketOptions:
    def test_carries_the_universal_bypass_mark(self):
        opts = net.bypass_socket_options()
        assert len(opts) == 1
        level, optname, value = opts[0]
        # value is the mark PiTun's nft rules `return` early on.
        assert value == net.SO_MARK_BYPASS == 0xFF

    def test_direct_transport_has_socket_options(self):
        t = net.httpx_direct_transport()
        assert t is not None


class TestXuiClientDirectFlag:
    def test_default_is_through_the_node(self):
        c = XuiClient(base_url="https://h:8443/p", api_token="t")
        assert c.direct is False
        client = c._ensure_client()
        # No custom transport → normal client → TPROXY → active node.
        # (httpx always has a default transport; the direct one is only set
        # when direct=True — assert the direct path differs below.)
        assert client is not None

    def test_direct_builds_a_bypass_transport(self):
        c = XuiClient(base_url="https://h:8443/p", api_token="t", direct=True)
        client = c._ensure_client()
        # The SO_MARK transport is installed (default client would use httpx's
        # own default transport instance instead).
        default = XuiClient(base_url="https://h:8443/p", api_token="t")._ensure_client()
        assert client._transport is not default._transport


class TestEndpointThreadsDirect:
    def _seed(self, session):
        srv = Server(name="p", host="1.2.3.4", port=22, user="root", auth_type="key")
        session.add(srv)
        session.commit()
        session.refresh(srv)
        xs = XuiServer(
            server_id=srv.id, api_token="tok", panel_user="u", panel_pass="p",
            panel_port=12345, panel_basepath="/t", mode="bare",
        )
        session.add(xs)
        session.commit()
        session.refresh(xs)
        return xs

    def _patched_client(self):
        """Capture the kwargs XuiClient was constructed with."""
        captured = {}

        class _Fake:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            probe = AsyncMock(return_value=None)
            list_inbounds = AsyncMock(return_value=[])

        return _Fake, captured

    def test_probe_default_goes_through_node(
        self, client, session, admin_user, auth_headers,
    ):
        xs = self._seed(session)
        Fake, captured = self._patched_client()
        with mock.patch("app.api.xui.XuiClient", Fake):
            resp = client.post(
                f"/api/xui/servers/{xs.id}/probe", headers=auth_headers,
            )
        assert resp.status_code == 200
        assert captured.get("direct") is False

    def test_probe_direct_true_bypasses(
        self, client, session, admin_user, auth_headers,
    ):
        xs = self._seed(session)
        Fake, captured = self._patched_client()
        with mock.patch("app.api.xui.XuiClient", Fake):
            resp = client.post(
                f"/api/xui/servers/{xs.id}/probe?direct=true", headers=auth_headers,
            )
        assert resp.status_code == 200
        assert captured.get("direct") is True

    def test_sync_threads_direct(
        self, client, session, admin_user, auth_headers,
    ):
        xs = self._seed(session)
        Fake, captured = self._patched_client()
        with mock.patch("app.api.xui.XuiClient", Fake):
            client.post(
                f"/api/xui/servers/{xs.id}/sync?direct=true", headers=auth_headers,
            )
        assert captured.get("direct") is True


class TestSshDirectFlag:
    """SSH default is now THROUGH the active node (unmarked socket → TPROXY);
    `direct=True` restores the SO_MARK bypass. `_maybe_marked_sock` is the
    switch."""

    def test_not_direct_returns_no_socket(self):
        import asyncio
        from app.core import ssh

        sock, rtt = asyncio.run(ssh._maybe_marked_sock("h", 22, 5.0, False))
        assert sock is None and rtt is None

    def test_direct_opens_a_marked_socket(self):
        import asyncio
        from app.core import ssh

        fake_sock = mock.MagicMock()
        with (
            mock.patch.object(ssh, "_resolve_direct", new=AsyncMock(return_value="1.2.3.4")),
            mock.patch.object(ssh, "_connect_marked", return_value=(fake_sock, 12)),
        ):
            sock, rtt = asyncio.run(ssh._maybe_marked_sock("h", 22, 5.0, True))
        assert sock is fake_sock and rtt == 12


class TestServerEndpointsThreadDirect:
    def _seed(self, session):
        srv = Server(name="p", host="1.2.3.4", port=22, user="root", auth_type="key")
        session.add(srv)
        session.commit()
        session.refresh(srv)
        return srv

    def test_test_endpoint_default_through_node(
        self, client, session, admin_user, auth_headers,
    ):
        srv = self._seed(session)
        captured = {}

        async def fake_test(**kwargs):
            captured.update(kwargs)
            from app.core.ssh import SSHTestResult
            return SSHTestResult(ok=True, latency_ms=5)

        with mock.patch("app.api.servers.test_ssh_connection", side_effect=fake_test):
            resp = client.post(f"/api/servers/{srv.id}/test", headers=auth_headers)
        assert resp.status_code == 200
        assert captured.get("direct") is False

    def test_test_endpoint_direct_true(
        self, client, session, admin_user, auth_headers,
    ):
        srv = self._seed(session)
        captured = {}

        async def fake_test(**kwargs):
            captured.update(kwargs)
            from app.core.ssh import SSHTestResult
            return SSHTestResult(ok=True, latency_ms=5)

        with mock.patch("app.api.servers.test_ssh_connection", side_effect=fake_test):
            resp = client.post(
                f"/api/servers/{srv.id}/test?direct=true", headers=auth_headers,
            )
        assert resp.status_code == 200
        assert captured.get("direct") is True


class TestContextvarAutoCoverage:
    """The router dependency latches `?direct=` into a contextvar; XuiClient
    and the SSH helper read it as their default, so an endpoint that never
    threads `direct` still bypasses when asked. This is what covers chains,
    healthcheck, WG, fakesite, etc. without touching every signature."""

    def _reset(self):
        from app.core import net
        net._direct_ctx.set(False)

    def test_xuiclient_default_follows_contextvar(self):
        import asyncio
        from app.core import net

        async def run():
            await net.read_direct(True)
            # No direct= passed → default_factory reads the contextvar.
            return XuiClient(base_url="https://h/p", api_token="t").direct

        try:
            assert asyncio.run(run()) is True
        finally:
            self._reset()

    def test_xuiclient_default_false_off_request(self):
        # Outside a request (no read_direct call) → through the node.
        self._reset()
        assert XuiClient(base_url="https://h/p", api_token="t").direct is False

    def test_ssh_none_follows_contextvar(self):
        import asyncio
        from app.core import net, ssh

        fake = mock.MagicMock()
        async def run():
            await net.read_direct(True)
            with (
                mock.patch.object(ssh, "_resolve_direct", new=AsyncMock(return_value="1.2.3.4")),
                mock.patch.object(ssh, "_connect_marked", return_value=(fake, 9)),
            ):
                # direct=None → falls back to the contextvar (True) → marked.
                return await ssh._maybe_marked_sock("h", 22, 5.0, None)

        try:
            sock, rtt = asyncio.run(run())
            assert sock is fake
        finally:
            self._reset()
