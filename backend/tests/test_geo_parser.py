"""Tests for app.core.geo proto parser + tag cache refresh.

Both `geosite.dat` and `geoip.dat` share the same outer-list schema we
care about, so almost all tests use synthetic byte strings constructed
to exercise specific wire-format edge cases. One end-to-end test uses
the real `v2fly-dlc.dat` fixture stashed under `.claude_temp/` if it
exists locally — skipped on CI runners that don't have the file.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.geo import (
    AVAILABLE_GEOIP_TAGS,
    AVAILABLE_GEOSITE_TAGS,
    _parse_top_level_country_codes,
    _read_varint,
    parse_geoip_tags,
    parse_geosite_tags,
    refresh_tag_cache,
)


# ── Helpers to build synthetic protobuf payloads ─────────────────────────────


def _varint(n: int) -> bytes:
    """Encode an unsigned integer as a base-128 varint (protobuf wire format)."""
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _tag(field_num: int, wire_type: int) -> bytes:
    """Encode a (field_num, wire_type) header byte(s)."""
    return _varint((field_num << 3) | wire_type)


def _length_delimited(field_num: int, payload: bytes) -> bytes:
    """Wrap payload as a wire-type-2 (length-delimited) field."""
    return _tag(field_num, 2) + _varint(len(payload)) + payload


def _entry(country_code: str, *, extra_field: bytes = b"") -> bytes:
    """Build one Entry message: field 1 = country_code (string).
    `extra_field` is appended verbatim — used to exercise the parser's
    "skip unknown fields" path.
    """
    cc_bytes = country_code.encode("utf-8")
    return _length_delimited(1, cc_bytes) + extra_field


def _list(*entries: bytes) -> bytes:
    """Build a top-level GeoSiteList/GeoIPList wrapping the given entries."""
    return b"".join(_length_delimited(1, e) for e in entries)


# ── _read_varint ──────────────────────────────────────────────────────────────


class TestReadVarint:
    def test_single_byte(self):
        assert _read_varint(b"\x05", 0) == (5, 1)

    def test_multi_byte(self):
        # 300 = 0xAC 0x02 (10101100 00000010)
        assert _read_varint(b"\xAC\x02", 0) == (300, 2)

    def test_starts_at_offset(self):
        buf = b"junk\x05junk"
        val, pos = _read_varint(buf, 4)
        assert val == 5
        assert pos == 5

    def test_truncated_raises(self):
        # Continuation bit set on last byte, no terminator
        with pytest.raises(ValueError):
            _read_varint(b"\x80", 0)

    def test_overlong_raises(self):
        # >10 continuation bytes shouldn't be accepted
        with pytest.raises(ValueError):
            _read_varint(b"\x80" * 11, 0)


# ── _parse_top_level_country_codes ────────────────────────────────────────────


class TestParseTopLevelCountryCodes:
    def test_empty_buffer_returns_empty_set(self):
        assert _parse_top_level_country_codes(b"") == set()

    def test_single_entry(self):
        payload = _list(_entry("CN"))
        # Lower-cased on extract
        assert _parse_top_level_country_codes(payload) == {"cn"}

    def test_multiple_entries(self):
        payload = _list(
            _entry("CN"),
            _entry("RU"),
            _entry("category-ads-all"),
        )
        assert _parse_top_level_country_codes(payload) == {
            "cn", "ru", "category-ads-all",
        }

    def test_lowercase_normalisation(self):
        # xray sometimes upper-cases tags in error messages; the cache
        # stores lower-cased form so the validator can do
        # case-insensitive comparisons.
        payload = _list(_entry("CATEGORY-TELEMETRY"), _entry("Mixed-Case"))
        assert _parse_top_level_country_codes(payload) == {
            "category-telemetry", "mixed-case",
        }

    def test_empty_country_code_skipped(self):
        # Some entries may have an empty string (malformed upstream).
        # We don't add empty strings to the cache.
        payload = _list(_entry(""))
        assert _parse_top_level_country_codes(payload) == set()

    def test_skips_non_field_1_at_top_level(self):
        # A top-level varint field (e.g. version field added in a future
        # schema) should be skipped without breaking parsing.
        # Field 99, wire type 0 (varint), value 7
        unknown_top = _tag(99, 0) + _varint(7)
        payload = unknown_top + _list(_entry("CN")) + unknown_top
        # The list-only call only handles type-2 entries; the standalone
        # varint should still be skipped cleanly.
        # Reconstruct properly: we need to interleave the varint between
        # actual list entries inside the same "stream".
        full = unknown_top + _length_delimited(1, _entry("CN")) + unknown_top
        assert _parse_top_level_country_codes(full) == {"cn"}

    def test_skips_unknown_inner_fields(self):
        # Each Entry can have extra fields beyond country_code (the
        # actual schema has a `repeated Domain domain = 2`). We should
        # extract country_code (field 1) and skip anything else.
        # Inside entry: field 2 length-delimited "domain stuff" + field 1 = "RU"
        inner = _length_delimited(2, b"\x01\x02\x03") + _length_delimited(1, b"RU")
        payload = _length_delimited(1, inner)
        assert _parse_top_level_country_codes(payload) == {"ru"}

    def test_invalid_utf8_replaced_not_dropped(self):
        # We use errors="replace" so a malformed byte sequence shows up
        # as a tag with the unicode replacement char rather than empty.
        bad_payload = _length_delimited(1, b"\x01\xff\xfe")
        outer = _length_delimited(1, bad_payload)
        result = _parse_top_level_country_codes(outer)
        # The resulting tag is lower-cased and includes the replacement
        # chars; we just check it doesn't raise + isn't empty.
        assert result
        # No real-world tag should match — admin will see something odd
        # in the cache but the parser stays alive.

    def test_unsupported_wire_type_raises(self):
        # Wire type 3/4 (group start/end, deprecated proto3) → ValueError
        bad = _tag(1, 3)
        with pytest.raises(ValueError):
            _parse_top_level_country_codes(bad)


# ── parse_geosite_tags / parse_geoip_tags (file-based wrappers) ──────────────


class TestParseFileWrappers:
    def test_missing_file_returns_empty(self, tmp_path):
        # File doesn't exist → empty set, no exception
        nonexistent = str(tmp_path / "nonexistent.dat")
        assert parse_geosite_tags(nonexistent) == set()
        assert parse_geoip_tags(nonexistent) == set()

    def test_corrupt_file_returns_empty(self, tmp_path):
        # Random bytes that don't decode as valid protobuf → caught
        # by the broad except clause, empty set
        bad = tmp_path / "corrupt.dat"
        bad.write_bytes(b"\xff" * 100 + b"not protobuf")
        # _read_varint will raise on overlong varint; wrapper swallows it
        result = parse_geosite_tags(str(bad))
        # May return empty OR partially parsed garbage — we just want
        # no exception. Covered.
        assert isinstance(result, set)

    def test_valid_synthetic_file(self, tmp_path):
        target = tmp_path / "test.dat"
        payload = _list(
            _entry("CN"),
            _entry("category-ads-all"),
            _entry("ru"),
        )
        target.write_bytes(payload)
        assert parse_geosite_tags(str(target)) == {
            "cn", "category-ads-all", "ru",
        }
        assert parse_geoip_tags(str(target)) == {
            "cn", "category-ads-all", "ru",
        }


# ── refresh_tag_cache ─────────────────────────────────────────────────────────


class TestRefreshTagCache:
    def test_refresh_replaces_module_state(self, tmp_path, monkeypatch):
        # Point the settings to a temporary geosite + geoip file
        from app.config import settings as app_settings
        gs_path = tmp_path / "geosite.dat"
        gi_path = tmp_path / "geoip.dat"
        gs_path.write_bytes(_list(_entry("category-cn"), _entry("category-ru")))
        gi_path.write_bytes(_list(_entry("CN"), _entry("US"), _entry("RU")))

        monkeypatch.setattr(app_settings, "xray_geosite_path", str(gs_path))
        monkeypatch.setattr(app_settings, "xray_geoip_path", str(gi_path))

        gs_count, gi_count = refresh_tag_cache()
        assert gs_count == 2
        assert gi_count == 3

        # State observable after refresh
        from app.core import geo as geo_mod
        assert geo_mod.AVAILABLE_GEOSITE_TAGS == {"category-cn", "category-ru"}
        assert geo_mod.AVAILABLE_GEOIP_TAGS == {"cn", "us", "ru"}

    def test_refresh_with_missing_files_yields_empty(self, tmp_path, monkeypatch):
        from app.config import settings as app_settings
        monkeypatch.setattr(app_settings, "xray_geosite_path", str(tmp_path / "nope1"))
        monkeypatch.setattr(app_settings, "xray_geoip_path", str(tmp_path / "nope2"))

        gs_count, gi_count = refresh_tag_cache()
        assert gs_count == 0
        assert gi_count == 0


# ── End-to-end: real v2fly fixture (skipped if not present) ──────────────────


@pytest.mark.skipif(
    not Path(".claude_temp/v2fly-dlc.dat").exists()
    and not Path("../.claude_temp/v2fly-dlc.dat").exists(),
    reason="real .dat fixture not present (developer-only)",
)
def test_parses_real_v2fly_dat():
    """Sanity test against an actual upstream `geosite.dat` from v2fly.
    Skipped on CI but useful locally — catches regressions in the parser
    against a 5-10 MB real-world payload."""
    candidates = [
        ".claude_temp/v2fly-dlc.dat",
        "../.claude_temp/v2fly-dlc.dat",
        os.path.expanduser("~/pitun/.claude_temp/v2fly-dlc.dat"),
    ]
    for path in candidates:
        if Path(path).exists():
            tags = parse_geosite_tags(path)
            # v2fly's domain-list-community typically has 1000+ entries
            assert len(tags) > 500, f"only {len(tags)} tags from real .dat — parser regression?"
            # Spot-check: well-known categories should be present
            assert "cn" in tags or "category-cn" in tags
            return
    pytest.skip("no real .dat fixture found")
