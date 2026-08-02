"""Xray renamed the plain "tcp" transport to "raw" (v25.x). Panels now emit
`type=raw` in share links, subscriptions store it verbatim, and one such
node used to 500 the ENTIRE /nodes list on read — the response_model
validated every row and rejected the unknown value. `raw` and `tcp` are the
same transport; PiTun folds one into the other everywhere."""
import pytest

from app.core.config_gen import _stream_settings
from app.core.uri_parser import parse_uri, parse_uri_list
from app.models import Node
from app.schemas import NodeRead, NodeCreate, NodeUpdate


class TestSchemaAcceptsRaw:
    def test_node_read_folds_raw_to_tcp(self):
        # Simulates serializing an ORM row stored with transport="raw".
        node = Node(
            id=1, name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="u", transport="raw", tls="reality", enabled=True,
        )
        read = NodeRead.model_validate(node, from_attributes=True)
        assert read.transport == "tcp"

    def test_node_create_accepts_raw(self):
        n = NodeCreate(
            name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="u", transport="raw", tls="reality",
        )
        assert n.transport == "tcp"

    def test_node_update_accepts_raw(self):
        n = NodeUpdate(transport="raw")
        assert n.transport == "tcp"

    def test_genuinely_invalid_transport_still_rejected(self):
        with pytest.raises(ValueError):
            NodeCreate(
                name="n", protocol="vless", address="1.2.3.4", port=443,
                uuid="u", transport="carrier-pigeon", tls="none",
            )


class TestListEndpointSurvivesRawNode:
    def test_nodes_list_does_not_500_on_a_raw_node(
        self, client, session, admin_user, auth_headers,
    ):
        # The regression: one raw-transport node took down GET /api/nodes.
        session.add(Node(
            name="ok", protocol="vless", address="1.1.1.1", port=443,
            uuid="a", transport="tcp", tls="reality", enabled=True,
        ))
        session.add(Node(
            name="raw-one", protocol="vless", address="2.2.2.2", port=443,
            uuid="b", transport="raw", tls="reality", enabled=True,
        ))
        session.commit()

        resp = client.get("/api/nodes", headers=auth_headers)
        assert resp.status_code == 200
        transports = {n["name"]: n["transport"] for n in resp.json()}
        assert transports["raw-one"] == "tcp"
        assert transports["ok"] == "tcp"


class TestConfigGenHandlesStoredRaw:
    def test_raw_node_emits_tcp_network(self):
        node = Node(
            id=1, name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="u", transport="raw", tls="reality", enabled=True,
        )
        stream = _stream_settings(node)
        # Every bundled xray version accepts "tcp"; older ones reject "raw".
        assert stream["network"] == "tcp"


class TestParserNormalizesRaw:
    def test_vless_share_link_with_type_raw(self):
        uri = (
            "vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443"
            "?type=raw&security=reality&pbk=abc&sni=example.com#n"
        )
        node = parse_uri(uri)
        assert node is not None
        assert node["transport"] == "tcp"

    def test_uri_list_with_raw(self):
        uri = (
            "vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443"
            "?type=raw&security=reality&pbk=abc&sni=example.com#n"
        )
        nodes = parse_uri_list(uri)
        assert nodes and nodes[0]["transport"] == "tcp"
