"""Tests for /health endpoint.

Regression tests for the "always returns 200 even when xray is down" bug.
Docker healthcheck relies on this endpoint to detect a silently-dead proxy,
so the degraded path must flip both the status field AND the HTTP code."""
from __future__ import annotations

from unittest.mock import patch, PropertyMock

import pytest


class TestHealth:
    def test_200_when_no_active_node(self, client, default_settings):
        """No active_node_id in settings → xray is not expected to run,
        so xray_running=False is a valid state, not a failure."""
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["xray_expected"] is False
        assert body["xray_running"] is False

    def test_503_when_expected_but_not_running(self, client, sample_node, default_settings, session):
        """active_node_id set + auto_restart=true + xray down → 503 degraded."""
        from app.models import Settings as DBSettings

        # Mark an active node so xray is "expected" to run
        row = session.query(DBSettings).filter(DBSettings.key == "active_node_id").first()
        row.value = str(sample_node.id)
        session.add(row)
        session.commit()

        with patch("app.core.xray.XrayManager.is_running", new_callable=PropertyMock, return_value=False):
            r = client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["xray_expected"] is True
        assert body["xray_running"] is False

    def test_200_when_expected_and_running(self, client, sample_node, default_settings, session):
        """active_node_id set + xray up → 200 ok."""
        from app.models import Settings as DBSettings

        row = session.query(DBSettings).filter(DBSettings.key == "active_node_id").first()
        row.value = str(sample_node.id)
        session.add(row)
        session.commit()

        with patch("app.core.xray.XrayManager.is_running", new_callable=PropertyMock, return_value=True):
            r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["xray_expected"] is True
        assert body["xray_running"] is True

    def test_200_when_auto_restart_disabled(self, client, sample_node, default_settings, session):
        """If auto_restart_xray=false, xray is not expected even with active
        node — user has opted out of auto-management."""
        from app.models import Settings as DBSettings

        active = session.query(DBSettings).filter(DBSettings.key == "active_node_id").first()
        active.value = str(sample_node.id)
        auto = session.query(DBSettings).filter(DBSettings.key == "auto_restart_xray").first()
        auto.value = "false"
        session.add_all([active, auto])
        session.commit()

        with patch("app.core.xray.XrayManager.is_running", new_callable=PropertyMock, return_value=False):
            r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["xray_expected"] is False
