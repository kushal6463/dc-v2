"""MOCKED unit tests for the read-only Snowflake active_campaigns COUNT overlay.

:mod:`harness.marts.snowflake_reader` is the live-Snowflake seam behind the
runtime ``active_campaigns`` count overlay. Its contract:

* it imports with NO module-level ``snowflake`` dependency (the connector is the
  optional ``counts`` extra; the import is local to ``_open_connection``);
* ``COUNT(DISTINCT CAMPAIGN_ID)`` is bucketed ``CAMPAIGN_TYPE -> google_<x>``
  (additive sub-channels) while ``AD_NETWORK_TYPE`` / ``OBJECTIVE`` stay in
  ``overlay_dims`` (non-additive) and never leak into the additive tree;
* it degrades gracefully (``stale=True``, empty counts, NEVER raises) when
  Snowflake is unconfigured or unreachable.

Every test here mocks the connection — no live Snowflake (and no installed
connector) is required. ``_open_connection`` is monkeypatched to return a fake
connection whose cursor replays canned ``GROUP BY`` rows.
"""

from __future__ import annotations

import inspect
import subprocess
import sys

from harness.kg.config import Settings
from harness.marts import snowflake_reader as sr

# ---------------------------------------------------------------------------
# Fakes — a connection/cursor that replays canned rows per GROUP BY query.
# ---------------------------------------------------------------------------


def _classify(sql: str) -> str | None:
    """Map an emitted GROUP BY query to its canned-row key (mirrors the reader)."""
    if sr._MART_CAMPAIGN_MATRIX in sql and "PLATFORM" in sql:
        return "platform"
    if sr._MART_GOOGLE_CAMPAIGN_PERFORMANCE in sql and "CAMPAIGN_TYPE" in sql:
        return "google"
    if sr._MART_GOOGLE_CAMPAIGN_PERFORMANCE in sql and "AD_NETWORK_TYPE" in sql:
        return "network"
    if sr._MART_META_CAMPAIGN_PERFORMANCE in sql and "OBJECTIVE" in sql:
        return "objective"
    return None


class _FakeCursor:
    def __init__(self, rows: dict[str, list[tuple]], executed: list[tuple]):
        self._rows = rows
        self._executed = executed
        self._last_key: str | None = None
        self.closed = False

    def execute(self, sql, params=None):
        self._executed.append((sql, params))
        self._last_key = _classify(sql)
        return self

    def fetchall(self):
        if self._last_key is None:
            return []
        return list(self._rows.get(self._last_key, []))

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _install_fake(monkeypatch, rows, *, executed=None, conn_box=None):
    """Patch ``_open_connection`` to hand back a fake conn replaying ``rows``."""
    executed = [] if executed is None else executed
    cursor = _FakeCursor(rows, executed)
    conn = _FakeConnection(cursor)
    if conn_box is not None:
        conn_box.append(conn)

    def _fake_open(_cfg):
        return conn

    monkeypatch.setattr(sr, "_open_connection", _fake_open)
    return executed


def _configured_settings() -> Settings:
    """A fully-configured Settings (explicit kwargs override any env/.env)."""
    return Settings(
        snowflake_account="acct",
        snowflake_user="usr",
        snowflake_password="pwd",
        snowflake_role="role",
        snowflake_warehouse="wh",
        snowflake_database="DB_RARE_SEEDS",
        snowflake_schema="MARTS",
        snowflake_private_key_path="",
    )


#: A representative full result set used by the happy-path tests.
_FULL_ROWS = {
    "platform": [
        ("Google Ads", 12),
        ("Meta Ads", 7),
        ("Klaviyo", 3),
        ("LinkedIn Ads", 2),  # no node in the target tree -> ignored
    ],
    "google": [
        ("SEARCH", 5),
        ("VIDEO", 2),
        ("SHOPPING", 3),
        ("DISPLAY", 1),
        ("DEMAND_GEN", 1),
        ("PERFORMANCE_MAX", 4),
        ("MULTI_CHANNEL", 2),  # -> google_other
        ("UNKNOWN", 1),  # -> google_other
    ],
    "network": [
        ("SEARCH", 6),
        ("CONTENT", 4),
        ("YOUTUBE_WATCH", 9),  # distinct from VIDEO=2 to prove the split source
        ("MIXED", 1),
    ],
    "objective": [
        ("CONVERSIONS", 5),
        ("TRAFFIC", 2),
        ("AWARENESS", 1),
    ],
}


# ---------------------------------------------------------------------------
# Import contract: no module-level snowflake dependency.
# ---------------------------------------------------------------------------


def test_import_pulls_no_snowflake_connector() -> None:
    """Importing the reader in a clean interpreter must not load snowflake."""
    code = (
        "import sys; import harness.marts.snowflake_reader;"
        "assert 'snowflake' not in sys.modules, sorted(sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_signature_matches_agreed_interface() -> None:
    """The reader pins the agreed positional signature (+ injectable settings)."""
    params = list(inspect.signature(sr.fetch_active_campaign_breakdown).parameters)
    assert params[:3] == ["anchor_metric_uid", "date_from", "date_to"]
    assert "settings" in params


# ---------------------------------------------------------------------------
# CAMPAIGN_TYPE -> uid mapping.
# ---------------------------------------------------------------------------


def test_campaign_type_maps_to_google_subchannel_uids(monkeypatch) -> None:
    """Each Google CAMPAIGN_TYPE buckets into the right google_<x> uid."""
    _install_fake(monkeypatch, _FULL_ROWS)

    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )
    counts = out["counts_by_metric_uid"]

    assert counts["google_search.active_campaigns"] == 5
    assert counts["google_youtube.active_campaigns"] == 2  # VIDEO, not AD_NETWORK_TYPE
    assert counts["google_shopping.active_campaigns"] == 3
    assert counts["google_display.active_campaigns"] == 1
    assert counts["google_demand_gen.active_campaigns"] == 1
    assert counts["google_pmax.active_campaigns"] == 4  # PERFORMANCE_MAX
    # MULTI_CHANNEL (2) + UNKNOWN (1) fold into the catch-all bucket.
    assert counts["google_other.active_campaigns"] == 3


def test_unknown_campaign_type_folds_into_google_other(monkeypatch) -> None:
    """Unmapped/novel CAMPAIGN_TYPE values sum into google_other, never dropped."""
    rows = {"google": [("SMART", 4), ("local", 2), ("HOTEL", 1)]}
    _install_fake(monkeypatch, rows)

    out = sr.fetch_active_campaign_breakdown(
        "google_ads.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )
    # 4 + 2 (case-insensitive) + 1 all land in google_other.
    assert out["counts_by_metric_uid"]["google_other.active_campaigns"] == 7


# ---------------------------------------------------------------------------
# Platform counts + blended SUM, and additive-vs-overlay separation.
# ---------------------------------------------------------------------------


def test_platform_counts_and_blended_is_definitional_sum(monkeypatch) -> None:
    """Platform counts come from the matrix; blended = google_ads+meta_ads+klaviyo."""
    _install_fake(monkeypatch, _FULL_ROWS)

    counts = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )["counts_by_metric_uid"]

    assert counts["google_ads.active_campaigns"] == 12
    assert counts["meta_ads.active_campaigns"] == 7
    assert counts["klaviyo.active_campaigns"] == 3
    # LinkedIn Ads (2) is excluded; blended is the SUM of the three tree addends.
    assert counts["blended.active_campaigns"] == 12 + 7 + 3 == 22


def test_additive_tree_and_overlay_dims_are_separate(monkeypatch) -> None:
    """ad_network_type / objective live ONLY in overlay_dims, never in the tree."""
    _install_fake(monkeypatch, _FULL_ROWS)

    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )
    counts = out["counts_by_metric_uid"]

    # The additive tree is exactly the 3 platforms + 7 google sub-channels + blended.
    expected_keys = set(sr.PLATFORM_UIDS) | set(sr.GOOGLE_SUBCHANNEL_UIDS) | {
        "blended.active_campaigns"
    }
    assert set(counts) == expected_keys

    # Overlay dims are reported verbatim and non-additively.
    assert out["overlay_dims"]["ad_network_type"] == {
        "SEARCH": 6,
        "CONTENT": 4,
        "YOUTUBE_WATCH": 9,
        "MIXED": 1,
    }
    assert out["overlay_dims"]["objective"] == {
        "CONVERSIONS": 5,
        "TRAFFIC": 2,
        "AWARENESS": 1,
    }

    # No overlay-dimension label leaks into the additive counts, and the
    # YOUTUBE_WATCH placement (9) never contaminates google_youtube (VIDEO=2).
    overlay_labels = set(out["overlay_dims"]["ad_network_type"]) | set(
        out["overlay_dims"]["objective"]
    )
    assert overlay_labels.isdisjoint(set(counts))
    assert 9 not in counts.values()


# ---------------------------------------------------------------------------
# Zero-fill / zero_count separation.
# ---------------------------------------------------------------------------


def test_missing_buckets_zero_fill_and_report_zero_counts(monkeypatch) -> None:
    """Absent platforms/sub-channels are zero-filled and listed in zero_count."""
    rows = {"platform": [("Google Ads", 5)], "google": [("SEARCH", 5)]}
    _install_fake(monkeypatch, rows)

    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )
    counts = out["counts_by_metric_uid"]
    zeros = out["zero_count_metric_uids"]

    # All seven google sub-channels and all three platforms are always present.
    for uid in (*sr.GOOGLE_SUBCHANNEL_UIDS, *sr.PLATFORM_UIDS):
        assert uid in counts

    assert counts["google_search.active_campaigns"] == 5
    assert counts["google_ads.active_campaigns"] == 5
    assert counts["blended.active_campaigns"] == 5  # only Google Ads had activity

    # Measured-zero uids are reported; the non-zero ones are not.
    assert "meta_ads.active_campaigns" in zeros
    assert "klaviyo.active_campaigns" in zeros
    assert "google_youtube.active_campaigns" in zeros
    assert "google_search.active_campaigns" not in zeros
    assert "blended.active_campaigns" not in zeros
    assert zeros == sorted(zeros)


# ---------------------------------------------------------------------------
# SQL discipline: COUNT(DISTINCT ...), 4-way predicate, mart-correct value col.
# ---------------------------------------------------------------------------


def test_queries_use_distinct_count_and_correct_predicate(monkeypatch) -> None:
    """Each query counts DISTINCT campaigns with the mart-correct value column."""
    executed = _install_fake(monkeypatch, _FULL_ROWS)

    sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-02-01", "2025-02-28", settings=_configured_settings()
    )

    by_key = {_classify(sql): (sql, params) for sql, params in executed if _classify(sql)}
    assert set(by_key) == {"platform", "google", "network", "objective"}

    for _key, (sql, params) in by_key.items():
        assert "COUNT(DISTINCT CAMPAIGN_ID)" in sql  # never COUNT(*)
        assert "SPEND > 0" in sql
        assert "IMPRESSIONS > 0" in sql
        assert "CLICKS > 0" in sql
        assert params == {"date_from": "2025-02-01", "date_to": "2025-02-28"}

    # Matrix uses the singular CONVERSION_VALUE; the google/meta marts use the
    # plural CONVERSIONS_VALUE. The substrings are distinguishable.
    matrix_sql = by_key["platform"][0]
    assert "CONVERSION_VALUE > 0" in matrix_sql
    assert "CONVERSIONS_VALUE" not in matrix_sql
    for key in ("google", "network", "objective"):
        assert "CONVERSIONS_VALUE > 0" in by_key[key][0]

    # Schema-qualified table references (snowflake_schema=MARTS).
    assert f"MARTS.{sr._MART_CAMPAIGN_MATRIX}" in matrix_sql


def test_result_envelope_echoes_anchor_and_lists_source_marts(monkeypatch) -> None:
    """The result echoes anchor/date and reports the marts it derives from."""
    _install_fake(monkeypatch, _FULL_ROWS)

    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-03-01", "2025-03-31", settings=_configured_settings()
    )
    assert out["anchor_metric_uid"] == "blended.active_campaigns"
    assert out["date_from"] == "2025-03-01"
    assert out["date_to"] == "2025-03-31"
    assert out["stale"] is False
    assert out["source_marts"] == list(sr.SOURCE_MARTS)
    assert f"MARTS.{sr._MART_META_CAMPAIGN_PERFORMANCE}" in out["source_marts"]


# ---------------------------------------------------------------------------
# Graceful degradation: NEVER raise.
# ---------------------------------------------------------------------------


def test_unconfigured_degrades_without_connecting(monkeypatch) -> None:
    """Unconfigured Snowflake -> stale=True, empty counts, no connection attempt."""

    def _boom(_cfg):
        raise AssertionError("_open_connection must not be called when unconfigured")

    monkeypatch.setattr(sr, "_open_connection", _boom)

    unconfigured = Settings(
        snowflake_account="",
        snowflake_user="",
        snowflake_password="",
        snowflake_private_key_path="",
    )
    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=unconfigured
    )

    assert out["stale"] is True
    assert out["counts_by_metric_uid"] == {}
    assert out["overlay_dims"] == {"ad_network_type": {}, "objective": {}}
    assert out["zero_count_metric_uids"] == []
    assert out["source_marts"] == list(sr.SOURCE_MARTS)
    assert out["anchor_metric_uid"] == "blended.active_campaigns"
    assert "not configured" in out["freshness_notes"].lower()


def test_partial_credentials_are_treated_as_unconfigured(monkeypatch) -> None:
    """Account+user but no credential still degrades gracefully (no connect)."""
    monkeypatch.setattr(
        sr,
        "_open_connection",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("should not connect")),
    )
    partial = Settings(
        snowflake_account="acct",
        snowflake_user="usr",
        snowflake_password="",
        snowflake_private_key_path="",
    )
    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=partial
    )
    assert out["stale"] is True
    assert out["counts_by_metric_uid"] == {}


def test_connection_error_degrades_gracefully(monkeypatch) -> None:
    """A connect failure is swallowed -> stale=True, empty, never raises."""

    def _fail(_cfg):
        raise RuntimeError("cannot reach Snowflake")

    monkeypatch.setattr(sr, "_open_connection", _fail)

    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )
    assert out["stale"] is True
    assert out["counts_by_metric_uid"] == {}
    assert out["overlay_dims"] == {"ad_network_type": {}, "objective": {}}
    assert "failed" in out["freshness_notes"].lower()


def test_query_error_degrades_and_closes_connection(monkeypatch) -> None:
    """A mid-query failure degrades gracefully and still closes the connection."""

    class _BoomCursor(_FakeCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("query exploded")

    conn = _FakeConnection(_BoomCursor({}, []))
    monkeypatch.setattr(sr, "_open_connection", lambda _cfg: conn)

    out = sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )
    assert out["stale"] is True
    assert out["counts_by_metric_uid"] == {}
    assert conn.closed is True  # finally-block close ran despite the error


def test_successful_read_closes_connection(monkeypatch) -> None:
    """The happy path closes the connection in the finally block."""
    box: list[_FakeConnection] = []
    _install_fake(monkeypatch, _FULL_ROWS, conn_box=box)

    sr.fetch_active_campaign_breakdown(
        "blended.active_campaigns", "2025-01-01", "2025-01-31", settings=_configured_settings()
    )
    assert box and box[0].closed is True
