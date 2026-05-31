"""API tests for RoutingSet CRUD + bulk device assignment (v1.4 Phase 2)."""
from unittest.mock import patch, AsyncMock, PropertyMock

from app.models import Device, RoutingRule, RoutingSet


# ── CRUD basics ───────────────────────────────────────────────────────────────


class TestRoutingSetCreate:
    def test_create_first_set_gets_port_65500(self, client, auth_headers):
        r = client.post(
            "/api/routing-sets",
            json={"name": "Kids", "description": "no gambling"},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["name"] == "Kids"
        assert data["description"] == "no gambling"
        assert data["tproxy_port"] == 65500
        assert data["order"] == 0
        assert "id" in data
        assert "created_at" in data

    def test_create_second_set_gets_next_port(self, client, auth_headers):
        client.post("/api/routing-sets", json={"name": "Kids"}, headers=auth_headers)
        r = client.post(
            "/api/routing-sets", json={"name": "Work"}, headers=auth_headers
        )
        assert r.status_code == 201
        assert r.json()["tproxy_port"] == 65501

    def test_create_fills_gap_after_delete(self, client, auth_headers):
        """Allocator scans for lowest-free port → reuses gaps from deletes."""
        a = client.post(
            "/api/routing-sets", json={"name": "A"}, headers=auth_headers
        ).json()
        b = client.post(
            "/api/routing-sets", json={"name": "B"}, headers=auth_headers
        ).json()
        assert a["tproxy_port"] == 65500
        assert b["tproxy_port"] == 65501
        # Delete A — leaves a gap at 65500
        client.delete(f"/api/routing-sets/{a['id']}", headers=auth_headers)
        # Next create should reclaim 65500, not jump to 65502
        c = client.post(
            "/api/routing-sets", json={"name": "C"}, headers=auth_headers
        ).json()
        assert c["tproxy_port"] == 65500

    def test_create_rejected_when_limit_reached(self, client, auth_headers):
        """36 sets max. The 37th create must 409 with a clear message.

        Mock `_host_port_free` to always True so the test is
        deterministic across machines — otherwise a dev host that
        has ANY service listening in 65500..65535 (stray test runner,
        another project) causes the allocator to legitimately skip a
        port and the 36th create fails one slot early. The real
        bind-probe is exercised separately by the live device smoke
        flow.
        """
        from app.api.routing_sets import MAX_ROUTING_SETS
        with patch("app.api.routing_sets._host_port_free", return_value=True):
            for i in range(MAX_ROUTING_SETS):
                r = client.post(
                    "/api/routing-sets",
                    json={"name": f"set-{i}"},
                    headers=auth_headers,
                )
                assert r.status_code == 201, f"create #{i+1} unexpectedly failed: {r.text}"
            # 37th
            r = client.post(
                "/api/routing-sets",
                json={"name": "overflow"},
                headers=auth_headers,
            )
            assert r.status_code == 409
            assert "limit reached" in r.json()["detail"].lower()

    def test_create_duplicate_name_rejected(self, client, auth_headers):
        client.post("/api/routing-sets", json={"name": "Kids"}, headers=auth_headers)
        r = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        )
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"]

    def test_create_empty_name_rejected(self, client, auth_headers):
        r = client.post(
            "/api/routing-sets", json={"name": "   "}, headers=auth_headers
        )
        assert r.status_code == 422

    def test_create_too_long_name_rejected(self, client, auth_headers):
        r = client.post(
            "/api/routing-sets", json={"name": "x" * 65}, headers=auth_headers
        )
        assert r.status_code == 422


class TestRoutingSetCapacity:
    def test_capacity_empty(self, client, auth_headers):
        r = client.get("/api/routing-sets/capacity", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["used"] == 0
        assert data["maximum"] == 36
        assert data["available"] == 36

    def test_capacity_decrements_on_create(self, client, auth_headers):
        client.post("/api/routing-sets", json={"name": "Kids"}, headers=auth_headers)
        client.post("/api/routing-sets", json={"name": "Work"}, headers=auth_headers)
        r = client.get("/api/routing-sets/capacity", headers=auth_headers)
        assert r.json() == {"used": 2, "maximum": 36, "available": 34}

    def test_capacity_path_not_treated_as_set_id(self, client, auth_headers):
        """Regression: `/capacity` must route to the capacity endpoint,
        NOT to `GET /{set_id}` with set_id="capacity". FastAPI matches
        in registration order, so /capacity must be declared first."""
        r = client.get("/api/routing-sets/capacity", headers=auth_headers)
        assert r.status_code == 200
        # The response must look like RoutingSetCapacity, not a 422
        # (validation error from int parsing) and not a 404 (set-by-id miss).
        assert "maximum" in r.json()


class TestRoutingSetList:
    def test_list_empty(self, client, auth_headers):
        r = client.get("/api/routing-sets", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_list_ordered_by_order_then_id(self, client, auth_headers):
        # Create out of order to verify the sort
        client.post(
            "/api/routing-sets",
            json={"name": "Z-last", "order": 99},
            headers=auth_headers,
        )
        client.post(
            "/api/routing-sets",
            json={"name": "A-first", "order": 1},
            headers=auth_headers,
        )
        r = client.get("/api/routing-sets", headers=auth_headers)
        names = [row["name"] for row in r.json()]
        assert names == ["A-first", "Z-last"]


class TestRoutingSetUpdate:
    def test_patch_rename(self, client, auth_headers):
        rs = client.post(
            "/api/routing-sets", json={"name": "Old"}, headers=auth_headers
        ).json()
        r = client.patch(
            f"/api/routing-sets/{rs['id']}",
            json={"name": "New"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["name"] == "New"
        # tproxy_port MUST stay the same after rename — the operator's name
        # choice is purely cosmetic, the port is the actual identity in
        # nftables + xray.
        assert r.json()["tproxy_port"] == rs["tproxy_port"]

    def test_patch_rename_clash_rejected(self, client, auth_headers):
        client.post("/api/routing-sets", json={"name": "Kids"}, headers=auth_headers)
        rs2 = client.post(
            "/api/routing-sets", json={"name": "Work"}, headers=auth_headers
        ).json()
        r = client.patch(
            f"/api/routing-sets/{rs2['id']}",
            json={"name": "Kids"},
            headers=auth_headers,
        )
        assert r.status_code == 400

    def test_patch_404(self, client, auth_headers):
        r = client.patch(
            "/api/routing-sets/9999", json={"name": "X"}, headers=auth_headers
        )
        assert r.status_code == 404


class TestRoutingSetDelete:
    def test_delete_empty_set(self, client, auth_headers):
        rs = client.post(
            "/api/routing-sets", json={"name": "Tmp"}, headers=auth_headers
        ).json()
        r = client.delete(
            f"/api/routing-sets/{rs['id']}", headers=auth_headers
        )
        assert r.status_code == 204
        # Verify gone
        r = client.get(f"/api/routing-sets/{rs['id']}", headers=auth_headers)
        assert r.status_code == 404

    def test_delete_with_devices_refused_without_cascade(
        self, client, session, auth_headers, sample_device
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # Assign device to the set
        client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        # Refuse delete
        r = client.delete(
            f"/api/routing-sets/{rs['id']}", headers=auth_headers
        )
        assert r.status_code == 409
        assert "1 device(s)" in r.json()["detail"]

    def test_delete_with_rules_refused_without_cascade(
        self, client, session, auth_headers, sample_rule
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # Move rule into the set
        client.patch(
            f"/api/routing/rules/{sample_rule.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        r = client.delete(
            f"/api/routing-sets/{rs['id']}", headers=auth_headers
        )
        assert r.status_code == 409
        assert "1 rule(s)" in r.json()["detail"]

    def test_delete_cascade_move_to_global_nulls_fks(
        self, client, session, auth_headers, sample_device, sample_rule
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        client.patch(
            f"/api/routing/rules/{sample_rule.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        r = client.delete(
            f"/api/routing-sets/{rs['id']}?cascade=move-to-global",
            headers=auth_headers,
        )
        assert r.status_code == 204

        session.expire_all()
        dev = session.get(Device, sample_device.id)
        rule = session.get(RoutingRule, sample_rule.id)
        assert dev.routing_set_id is None
        assert rule.routing_set_id is None


# ── Bulk device assignment ────────────────────────────────────────────────────


class TestBulkAssign:
    def test_bulk_assign_devices_to_set(
        self, client, session, auth_headers, multiple_devices
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # multiple_devices[2] has routing_policy='exclude' from the
        # fixture — exclude-policy devices reject set assignment by
        # design (covered in TestExcludePolicyConflict), so pick only
        # the first two for this generic bulk-assign happy-path test.
        ids = [d.id for d in multiple_devices[:2]]
        r = client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": ids, "routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        assert r.status_code == 204, r.text

        session.expire_all()
        for did in ids:
            assert session.get(Device, did).routing_set_id == rs["id"]

    def test_bulk_assign_null_unassigns(
        self, client, session, auth_headers, multiple_devices
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # Skip the excluded-policy device (multiple_devices[2]).
        ids = [d.id for d in multiple_devices[:2]]
        # First assign
        client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": ids, "routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        # Now unassign
        r = client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": ids, "routing_set_id": None},
            headers=auth_headers,
        )
        assert r.status_code == 204

        session.expire_all()
        for did in ids:
            assert session.get(Device, did).routing_set_id is None

    def test_bulk_assign_unknown_device_rolls_back(
        self, client, session, auth_headers, multiple_devices
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        good_id = multiple_devices[0].id
        ids = [good_id, 9999]  # second one is bogus
        r = client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": ids, "routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        assert r.status_code == 404
        assert "9999" in r.json()["detail"]

        # Critical: the good one MUST NOT have been touched. Half-applied
        # bulk actions are confusing and we explicitly roll them back.
        session.expire_all()
        assert session.get(Device, good_id).routing_set_id is None

    def test_bulk_assign_unknown_set_rejected(
        self, client, auth_headers, multiple_devices
    ):
        ids = [d.id for d in multiple_devices]
        r = client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": ids, "routing_set_id": 9999},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_bulk_assign_empty_list_rejected(self, client, auth_headers):
        r = client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": [], "routing_set_id": None},
            headers=auth_headers,
        )
        assert r.status_code == 400


# ── Rule-side wiring ─────────────────────────────────────────────────────────


class TestRoutingRuleSetId:
    def test_create_rule_in_set(self, client, session, auth_headers):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        r = client.post(
            "/api/routing/rules",
            json={
                "name": "block ads",
                "rule_type": "domain",
                "match_value": "doubleclick.net",
                "action": "block",
                "routing_set_id": rs["id"],
            },
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["routing_set_id"] == rs["id"]

    def test_create_rule_with_unknown_set_rejected(self, client, auth_headers):
        r = client.post(
            "/api/routing/rules",
            json={
                "name": "orphan",
                "rule_type": "domain",
                "match_value": "x.com",
                "action": "block",
                "routing_set_id": 9999,
            },
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert "9999" in r.json()["detail"]

    def test_list_rules_filter_by_set_id(
        self, client, session, auth_headers, sample_rule
    ):
        # sample_rule has routing_set_id=NULL (global). Add an in-set rule.
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        client.post(
            "/api/routing/rules",
            json={
                "name": "in-set",
                "rule_type": "domain",
                "match_value": "y.com",
                "action": "block",
                "routing_set_id": rs["id"],
            },
            headers=auth_headers,
        )
        # Global filter
        r = client.get(
            "/api/routing/rules?routing_set_id=null", headers=auth_headers
        )
        assert r.status_code == 200
        names = [x["name"] for x in r.json()]
        assert "Test rule" in names  # sample_rule is global
        assert "in-set" not in names

        # Set filter
        r = client.get(
            f"/api/routing/rules?routing_set_id={rs['id']}", headers=auth_headers
        )
        names = [x["name"] for x in r.json()]
        assert names == ["in-set"]


# ── Device-side wiring ───────────────────────────────────────────────────────


class TestDeviceSetId:
    def test_patch_device_assigns_to_set(
        self, client, session, auth_headers, sample_device
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        r = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["routing_set_id"] == rs["id"]

    def test_patch_device_with_unknown_set_rejected(
        self, client, auth_headers, sample_device
    ):
        r = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": 9999},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_list_devices_filter_by_set_id(
        self, client, session, auth_headers, multiple_devices
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        target_id = multiple_devices[0].id
        client.patch(
            f"/api/devices/{target_id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )

        # Filter by set
        r = client.get(
            f"/api/devices?routing_set_id={rs['id']}", headers=auth_headers
        )
        ids = [d["id"] for d in r.json()]
        assert ids == [target_id]

        # Filter by null
        r = client.get(
            "/api/devices?routing_set_id=null", headers=auth_headers
        )
        ids = [d["id"] for d in r.json()]
        assert target_id not in ids
        assert len(ids) == len(multiple_devices) - 1


# ── Dataplane reload (regression for the v1.4.0-dev nftables-desync bug) ──────


class TestDataplaneReload:
    """Assigning a device to a set must re-apply BOTH xray AND nftables.

    The original Phase 2 wiring only regenerated the xray config on a
    membership change — nftables stayed stale, so the per-set MAC
    redirect never materialised and the device silently kept hitting
    the default inbound. Caught during live smoke-testing on the 1.4
    box. These tests pin that both layers fire.

    We patch xray_manager.is_running=True (otherwise the reload helper
    early-returns), plus the two heavy helpers, and assert that
    `_apply_nftables` is awaited — that's the layer the bug skipped.
    """

    def _patches(self):
        return (
            patch("app.core.xray.XrayManager.is_running",
                  new_callable=PropertyMock, return_value=True),
            patch("app.api.system._regenerate_and_write", new_callable=AsyncMock),
            patch("app.api.system._apply_nftables", new_callable=AsyncMock),
            patch("app.core.xray.xray_manager.reload", new_callable=AsyncMock),
        )

    def test_bulk_assign_reapplies_nftables(
        self, client, session, auth_headers, multiple_devices
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # Skip the excluded-policy device (multiple_devices[2]) — it's
        # rejected from set assignment by the conflict guard.
        ids = [d.id for d in multiple_devices[:2]]

        p_run, p_regen, p_nft, p_reload = self._patches()
        with p_run, p_regen as regen, p_nft as nft, p_reload as reload_:
            r = client.post(
                "/api/routing-sets/devices/bulk",
                json={"device_ids": ids, "routing_set_id": rs["id"]},
                headers=auth_headers,
            )
        assert r.status_code == 204
        # The bug: only regen+reload fired, nft never did.
        assert regen.await_count >= 1, "xray config must regenerate"
        assert nft.await_count >= 1, "nftables MUST be re-applied on membership change"
        assert reload_.await_count >= 1, "xray must reload"

    def test_device_patch_reapplies_nftables(
        self, client, session, auth_headers, sample_device
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()

        p_run, p_regen, p_nft, p_reload = self._patches()
        with p_run, p_regen, p_nft as nft, p_reload:
            r = client.patch(
                f"/api/devices/{sample_device.id}",
                json={"routing_set_id": rs["id"]},
                headers=auth_headers,
            )
        assert r.status_code == 200
        assert nft.await_count >= 1, "single-device set assignment must re-apply nftables"

    def test_delete_set_cascade_reapplies_nftables(
        self, client, session, auth_headers, sample_device
    ):
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )

        p_run, p_regen, p_nft, p_reload = self._patches()
        with p_run, p_regen, p_nft as nft, p_reload:
            r = client.delete(
                f"/api/routing-sets/{rs['id']}?cascade=move-to-global",
                headers=auth_headers,
            )
        assert r.status_code == 204
        # Removing members must also re-render nftables (drop the MAC set).
        assert nft.await_count >= 1, "set deletion must re-apply nftables"

    def test_no_reload_when_xray_stopped(
        self, client, session, auth_headers, multiple_devices
    ):
        """When xray isn't running, the helper early-returns — nothing
        to reload, /system/start will build both layers fresh."""
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # Skip multiple_devices[2] (exclude policy) — rejected by guard.
        ids = [d.id for d in multiple_devices[:2]]

        with (
            patch("app.core.xray.XrayManager.is_running",
                  new_callable=PropertyMock, return_value=False),
            patch("app.api.system._apply_nftables", new_callable=AsyncMock) as nft,
        ):
            r = client.post(
                "/api/routing-sets/devices/bulk",
                json={"device_ids": ids, "routing_set_id": rs["id"]},
                headers=auth_headers,
            )
        assert r.status_code == 204
        assert nft.await_count == 0, "must not touch nftables when xray is stopped"


# ── Exclude-policy + set conflict guard (fix #5) ──────────────────────────────


class TestExcludePolicyConflict:
    """Excluded devices bypass TPROXY entirely (their MAC goes into
    bypass_mac which returns before any per-set redirect). A set
    assignment on such a device would silently never take effect, so
    the API rejects the combination explicitly. UI already greys out
    the dropdown — these tests pin the backend defence-in-depth.
    """

    def test_patch_rejects_set_assign_on_excluded_device(
        self, client, session, auth_headers, sample_device
    ):
        # Mark the device as excluded
        client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_policy": "exclude"},
            headers=auth_headers,
        )
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        r = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert "exclude" in r.json()["detail"].lower()
        assert "set" in r.json()["detail"].lower()

    def test_patch_rejects_setting_exclude_on_device_already_in_set(
        self, client, session, auth_headers, sample_device
    ):
        # Assign to a set first
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        # Now try to set policy=exclude — should be rejected
        r = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_policy": "exclude"},
            headers=auth_headers,
        )
        assert r.status_code == 400

    def test_patch_allows_unassign_then_exclude_in_two_steps(
        self, client, session, auth_headers, sample_device
    ):
        """The legitimate fix path: unassign from set, then exclude."""
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        # Step 1: unassign
        r1 = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": None},
            headers=auth_headers,
        )
        assert r1.status_code == 200
        # Step 2: exclude — now OK
        r2 = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_policy": "exclude"},
            headers=auth_headers,
        )
        assert r2.status_code == 200

    def test_patch_allows_atomic_unassign_and_exclude(
        self, client, session, auth_headers, sample_device
    ):
        """One PATCH that BOTH unassigns AND excludes — allowed because
        the final state has set_id=None (no conflict)."""
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        r = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_policy": "exclude", "routing_set_id": None},
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_bulk_assign_rejects_excluded_devices(
        self, client, session, auth_headers, multiple_devices
    ):
        # multiple_devices[2] is already exclude-policy from the fixture
        # (the third device gets routing_policy="exclude"). Pair it
        # with [0] (default policy) for a mixed batch.
        good = multiple_devices[0]
        bad = multiple_devices[2]
        assert bad.routing_policy == "exclude", "fixture invariant"
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # Bulk-assign mixed batch (good + excluded)
        ids = [good.id, bad.id]
        r = client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": ids, "routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert str(bad.id) in r.json()["detail"]
        # Rollback: the good device must NOT have been touched
        session.expire_all()
        assert session.get(Device, good.id).routing_set_id is None

    def test_bulk_assign_null_always_allowed_even_for_excluded(
        self, client, session, auth_headers, sample_device
    ):
        """Unassign (set_id=null) must work even for excluded devices —
        it's the way to clear stale state during cleanup."""
        # Make the device excluded but with a stale set_id (via direct
        # DB poke to simulate pre-fix state). The new API would never
        # let us create this combo, so we manually wedge it.
        rs_row = RoutingSet(name="Kids", tproxy_port=65500)
        session.add(rs_row)
        session.commit()
        session.refresh(rs_row)
        sample_device.routing_set_id = rs_row.id
        sample_device.routing_policy = "exclude"
        session.add(sample_device)
        session.commit()

        # Now bulk-assign to null should succeed (unassign clears
        # state, no new conflict introduced).
        r = client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": [sample_device.id], "routing_set_id": None},
            headers=auth_headers,
        )
        assert r.status_code == 204
        session.expire_all()
        assert session.get(Device, sample_device.id).routing_set_id is None

    def test_bulk_policy_to_exclude_auto_nulls_set_id(
        self, client, session, auth_headers, multiple_devices
    ):
        """When bulk-changing devices to policy=exclude, their set
        assignment must auto-clear (UI-level consistency with the
        greyed-out dropdown for excluded rows)."""
        rs = client.post(
            "/api/routing-sets", json={"name": "Kids"}, headers=auth_headers
        ).json()
        # Skip multiple_devices[2] (exclude policy) — the conflict guard
        # would reject the initial bulk-assign. The auto-null path
        # we're testing fires when policy CHANGES to exclude AFTER the
        # device is already in a set.
        ids = [d.id for d in multiple_devices[:2]]
        # Step 1: assign all to the set
        client.post(
            "/api/routing-sets/devices/bulk",
            json={"device_ids": ids, "routing_set_id": rs["id"]},
            headers=auth_headers,
        )
        # Step 2: bulk-change policy to exclude
        r = client.post(
            "/api/devices/bulk-policy",
            json={"device_ids": ids, "routing_policy": "exclude"},
            headers=auth_headers,
        )
        assert r.status_code == 204
        session.expire_all()
        for did in ids:
            d = session.get(Device, did)
            assert d.routing_policy == "exclude"
            assert d.routing_set_id is None, f"device {did} set_id not cleared"
