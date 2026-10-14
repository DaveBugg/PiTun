"""Operator error log: detail on disk, short safe message for the UI."""
import httpx
import pytest

from app.core import error_log


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "errors.log"
    monkeypatch.setattr(error_log, "log_path", lambda: path)
    monkeypatch.setattr(error_log, "_handler_ready", False)
    for h in list(error_log._log.handlers):
        error_log._log.removeHandler(h)
        h.close()
    yield path
    for h in list(error_log._log.handlers):
        error_log._log.removeHandler(h)
        h.close()


def test_message_is_generic_and_detail_goes_to_the_log(log_file):
    exc = RuntimeError("boom: token=SECRET123 at /srv/x")
    msg = error_log.record("subscription.fetch", exc, subscription_id=7)
    assert "SECRET123" not in msg and "/srv/x" not in msg
    text = log_file.read_text(encoding="utf-8")
    assert "SECRET123" in text and "subscription.fetch" in text and "subscription_id=7" in text
    assert "Traceback" not in msg


def test_http_status_and_timeout_are_readable():
    req = httpx.Request("GET", "https://sub.example.test/x?t=1")
    resp = httpx.Response(403, request=req)
    assert error_log.public_message(httpx.HTTPStatusError("x", request=req, response=resp)) == "HTTP 403"
    assert error_log.public_message(httpx.ReadTimeout("x")) == "timed out"
    assert error_log.public_message(httpx.ConnectError("x")) == "could not connect"


def test_redact_url_drops_credentials_path_and_query():
    assert error_log.redact_url("https://u:pw@sub.example.test:8443/a/b?token=zzz") == "https://sub.example.test/…"
    assert error_log.redact_url("not a url") == "<url>"


def test_tail_returns_the_last_lines(log_file):
    for i in range(5):
        error_log.record("t", ValueError(f"e{i}"))
    got = error_log.tail(3)
    assert len(got) == 3
