"""Pure (NO-DB) tests for the deterministic enrichment helpers.

Covers :mod:`harness.agentic.enrich` — mart-token extraction, the f-string
variable resolver, the alias-aware registry lookup, freshness, and the
structural-dedup candidate filter. No Neo4j, no BC_2 dependency (file reads use
``tmp_path`` fixtures); the DB-touching ``run_deterministic_enrich`` is not
exercised here.
"""

from __future__ import annotations

from datetime import date

from harness.agentic import enrich


def test_marts_from_text_variants() -> None:
    """Uppercase, lowercase, and DB-qualified mart tokens all normalize."""
    sql = (
        'FROM MART_GOOGLE_AD_PERFORMANCE x '
        'JOIN DB_RARE_SEEDS.MARTS.mart_meta_ad_performance y, "mart_campaign_matrix"'
    )
    assert enrich.marts_from_text(sql) == [
        "MARTS.mart_campaign_matrix",
        "MARTS.mart_google_ad_performance",
        "MARTS.mart_meta_ad_performance",
    ]


def test_marts_from_text_ignores_vars_and_empty() -> None:
    """A ``{GOOGLE_MART}`` placeholder (``*_MART`` var) is NOT a mart token."""
    assert enrich.marts_from_text("FROM {GOOGLE_MART} WHERE x > 0") == []
    assert enrich.marts_from_text(None) == []
    assert enrich.marts_from_text("") == []


def test_extract_source_columns() -> None:
    """Aggregate arguments are captured + upper-cased + de-duplicated."""
    sql = "SELECT SUM(SPEND), COUNT(DISTINCT AD_ID), AVG(roas) FROM t"
    assert enrich.extract_source_columns_from_sql(sql) == ["AD_ID", "ROAS", "SPEND"]


def test_registry_row_exact_and_alias() -> None:
    """Exact id wins; else the channel namespace aliases to the registry one."""
    reg = {
        "google.spend": {
            "node_id": "google.spend",
            "mart_source": "DB_X.MARTS.mart_google_campaign_performance",
        }
    }
    assert enrich.registry_row("google.spend", reg)["mart_source"].endswith(
        "mart_google_campaign_performance"
    )
    # graph ``google_ads.spend`` resolves to registry ``google.spend``
    assert enrich.registry_row("google_ads.spend", reg)["mart_source"].endswith(
        "mart_google_campaign_performance"
    )
    assert enrich.registry_row("unknown.metric", reg) == {}


def test_registry_freshness_stale_logic() -> None:
    """``data_stale`` is True/False past/within the SLA, None when absent."""
    reg = {
        "m": {
            "node_id": "m",
            "history_start": "2024-01-01",
            "history_end": "2026-06-01",
            "n_periods": "880",
            "availability": "daily",
        }
    }
    fresh = enrich.registry_freshness("m", reg, today=date(2026, 6, 26))
    assert fresh["history_start"] == "2024-01-01"
    assert fresh["n_periods"] == 880
    assert fresh["data_stale"] is True
    near = enrich.registry_freshness("m", reg, today=date(2026, 6, 3))
    assert near["data_stale"] is False
    assert enrich.registry_freshness("absent", reg, today=date(2026, 6, 26))[
        "data_stale"
    ] is None


def test_read_real_sql(tmp_path) -> None:
    """Cited line ranges are read (a bare ``:a-b`` reuses the previous file)."""
    path = tmp_path / "backend" / "x.py"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(f"line{i}" for i in range(1, 11)))
    out = enrich.read_real_sql("backend/x.py:2-4, :6-7", bc2_root=tmp_path)
    assert "line2" in out and "line4" in out and "line6" in out
    assert "line9" not in out


def test_resolve_mart_vars_handles_fstring() -> None:
    """``FROM {VAR}`` resolves against plain and f-string mart constants."""
    slice_sql = "FROM {GOOGLE_MART} JOIN {META_MART}"
    file_text = (
        'GOOGLE_MART = "MART_GOOGLE_AD_PERFORMANCE"\n'
        'META_MART = f"{DBT_SCHEMA}.MART_META_AD_PERFORMANCE"'
    )
    assert enrich.resolve_mart_vars(slice_sql, file_text) == [
        "MARTS.mart_google_ad_performance",
        "MARTS.mart_meta_ad_performance",
    ]


def test_filter_structural_dups() -> None:
    """Candidates whose unordered pair is already structural are dropped."""
    candidates = [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]
    kept, dropped = enrich.filter_structural_dups(
        candidates, {frozenset(("a", "b"))}
    )
    assert kept == [{"from": "c", "to": "d"}]
    assert dropped == [{"from": "a", "to": "b"}]


def test_build_enrich_candidates_excludes_structural(tmp_path) -> None:
    """Two metrics sharing a mart/column yield candidates — unless structural."""
    metrics = [
        {"metric_uid": "a", "mart_sources": ["MARTS.m1"], "source_columns": ["X"], "domain_ids": ["d1"]},
        {"metric_uid": "b", "mart_sources": ["MARTS.m1"], "source_columns": ["X"], "domain_ids": ["d1"]},
    ]
    # No structural edge -> candidates survive.
    out = enrich.build_enrich_candidates(metrics, tmp_path, set())
    assert out["candidates"], "expected shared-mart/column candidates"
    # With a structural {a,b} edge -> all dropped.
    out2 = enrich.build_enrich_candidates(metrics, tmp_path, {frozenset(("a", "b"))})
    assert out2["candidates"] == []
    assert out2["counts"]["dropped_structural"] >= 1
