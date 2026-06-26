"""NO-DB tests for the deterministic mart-lineage candidate producer.

Covers the pure, stdlib-only helpers in :mod:`harness.marts.lineage` (no Neo4j,
no network, no LLM — nothing here touches the ``graphdb`` fixture):

* :func:`~harness.marts.lineage.normalize_mart` — ``DB_<TENANT>.`` strip + the
  canonical ``MARTS.<table>`` (table lowercased) form.
* :func:`~harness.marts.lineage.parse_mart_refs` — over a ``tmp_path`` of sample
  ``*.sql`` files: only ``mart_`` ``ref()`` targets are captured (non-mart refs /
  ``source()`` ignored, self-refs dropped, every mart a key), and a missing
  directory yields ``{}``.
* :func:`~harness.marts.lineage.shared_mart_candidates` /
  :func:`~harness.marts.lineage.shared_column_candidates` — one unordered
  (``from < to``) pair per shared key, and a key shared by *more than* ``hub_cap``
  metrics is moved to ``skipped_hubs`` (no pairs).
* :func:`~harness.marts.lineage.lineage_candidates` — directed ``A -> B`` exactly
  when ``A``'s mart ``ref()``-depends on ``B``'s mart (never the reverse).
* ``cross_domain`` — ``True`` iff the two metrics' ``domain_ids`` are disjoint
  (empty domains are disjoint from everything).
* :func:`~harness.marts.lineage.generate_candidates` — the deduped union of the
  three producers, with per-producer ``counts`` that sum to the candidate count.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from harness.marts.lineage import (
    generate_candidates,
    lineage_candidates,
    normalize_mart,
    parse_mart_refs,
    shared_column_candidates,
    shared_mart_candidates,
)


def _metric(
    uid: str,
    *,
    marts: Sequence[str] = (),
    columns: Sequence[str] = (),
    domains: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a metric record in the shape the producers consume."""
    return {
        "metric_uid": uid,
        "mart_sources": list(marts),
        "source_columns": list(columns),
        "domain_ids": list(domains),
    }


# ---------------------------------------------------------------------------
# normalize_mart — DB_ strip + canonical MARTS.<lower>
# ---------------------------------------------------------------------------


def test_normalize_mart_strips_db_tenant_qualifier() -> None:
    """A leading ``DB_<TENANT>.`` qualifier is stripped to the ``MARTS.`` table."""
    assert normalize_mart("DB_RARE_SEEDS.MARTS.mart_x") == "MARTS.mart_x"


def test_normalize_mart_lowercases_bare_table() -> None:
    """A bare uppercase identifier is lowercased under the canonical schema."""
    assert normalize_mart("MART_GOOGLE_AD_PERFORMANCE") == "MARTS.mart_google_ad_performance"


def test_normalize_mart_bare_stem_gets_marts_schema() -> None:
    """A bare table/file-stem name is prefixed with the literal ``MARTS.``."""
    assert normalize_mart("creative_performance") == "MARTS.creative_performance"


def test_normalize_mart_foreign_schema_is_replaced_by_marts() -> None:
    """A non-``DB_`` schema qualifier is dropped; the canonical schema is ``MARTS``."""
    assert normalize_mart("ANALYTICS.mart_x") == "MARTS.mart_x"


# ---------------------------------------------------------------------------
# parse_mart_refs — only mart_ refs captured; missing dir -> {}
# ---------------------------------------------------------------------------


def test_parse_mart_refs_captures_only_mart_refs(tmp_path: Path) -> None:
    """Only ``mart_`` ``ref()`` targets count; non-mart refs/``source()`` ignored.

    ``mart_a`` refs ``mart_b`` (kept) and ``stg_orders`` (dropped — not a mart);
    ``mart_b`` only calls ``source()`` (no mart edges); ``mart_c`` self-refs
    (dropped) and refs ``mart_a`` (kept). Every discovered mart is a key, so the
    result doubles as the mart inventory.
    """
    (tmp_path / "mart_a.sql").write_text(
        "select * from {{ ref('mart_b') }} a join {{ ref('stg_orders') }} s",
        encoding="utf-8",
    )
    (tmp_path / "mart_b.sql").write_text(
        "select * from {{ source('raw', 'orders') }}", encoding="utf-8"
    )
    (tmp_path / "mart_c.sql").write_text(
        "select * from {{ ref('mart_c') }} self, {{ ref('mart_a') }} up",
        encoding="utf-8",
    )

    assert parse_mart_refs(tmp_path) == {
        "MARTS.mart_a": {"MARTS.mart_b"},
        "MARTS.mart_b": set(),
        "MARTS.mart_c": {"MARTS.mart_a"},
    }


def test_parse_mart_refs_missing_dir_is_empty(tmp_path: Path) -> None:
    """A missing / non-directory path yields an empty mapping (no crash)."""
    assert parse_mart_refs(tmp_path / "does_not_exist") == {}


# ---------------------------------------------------------------------------
# shared_mart_candidates — unordered pairs + hub skipping
# ---------------------------------------------------------------------------


def test_shared_mart_candidates_emits_one_unordered_pair() -> None:
    """Two metrics on the same canonical mart yield one ``from < to`` candidate."""
    metrics = [
        _metric("metric:a", marts=["mart_x"], domains=["marketing"]),
        _metric("metric:b", marts=["DB_T.MARTS.mart_x"], domains=["marketing"]),
    ]
    candidates, skipped = shared_mart_candidates(metrics)
    assert skipped == []
    assert candidates == [
        {
            "from": "metric:a",
            "to": "metric:b",
            "basis": "shared_mart",
            "via": "MARTS.mart_x",
            "cross_domain": False,
        }
    ]


def test_shared_mart_candidates_over_cap_mart_is_skipped_hub() -> None:
    """A mart shared by more than ``hub_cap`` metrics emits no pairs — it's a hub."""
    metrics = [
        _metric("metric:a", marts=["mart_hub"]),
        _metric("metric:b", marts=["mart_hub"]),
        _metric("metric:c", marts=["mart_hub"]),
    ]
    candidates, skipped = shared_mart_candidates(metrics, hub_cap=2)
    assert candidates == []
    assert skipped == [
        {"basis": "shared_mart", "via": "MARTS.mart_hub", "metric_count": 3}
    ]


# ---------------------------------------------------------------------------
# shared_column_candidates — unordered pairs + hub skipping (columns verbatim)
# ---------------------------------------------------------------------------


def test_shared_column_candidates_emits_one_unordered_pair() -> None:
    """Two metrics reading the same column yield one ``from < to`` candidate."""
    metrics = [
        _metric("metric:a", columns=["spend"], domains=["marketing"]),
        _metric("metric:b", columns=["spend"], domains=["marketing"]),
    ]
    candidates, skipped = shared_column_candidates(metrics)
    assert skipped == []
    assert candidates == [
        {
            "from": "metric:a",
            "to": "metric:b",
            "basis": "shared_column",
            "via": "spend",
            "cross_domain": False,
        }
    ]


def test_shared_column_candidates_over_cap_column_is_skipped_hub() -> None:
    """A column shared by more than ``hub_cap`` metrics emits no pairs — it's a hub."""
    metrics = [_metric(f"metric:{c}", columns=["amount"]) for c in "abc"]
    candidates, skipped = shared_column_candidates(metrics, hub_cap=2)
    assert candidates == []
    assert skipped == [
        {"basis": "shared_column", "via": "amount", "metric_count": 3}
    ]


# ---------------------------------------------------------------------------
# lineage_candidates — directed per the parsed mart->mart refs
# ---------------------------------------------------------------------------


def test_lineage_candidates_are_directed_per_refs() -> None:
    """``A``'s mart ref-depends on ``B``'s mart -> directed ``A -> B`` only."""
    metrics = [
        _metric("metric:a", marts=["mart_a"], domains=["d1"]),
        _metric("metric:b", marts=["mart_b"], domains=["d1"]),
    ]
    mart_refs = {"MARTS.mart_a": {"MARTS.mart_b"}}
    assert lineage_candidates(metrics, mart_refs) == [
        {
            "from": "metric:a",
            "to": "metric:b",
            "basis": "mart_lineage",
            "via": ["MARTS.mart_a", "MARTS.mart_b"],
            "cross_domain": False,
        }
    ]


def test_lineage_candidates_no_edge_without_a_ref() -> None:
    """No ``ref()`` dependency between the two marts -> no lineage candidate."""
    metrics = [
        _metric("metric:a", marts=["mart_a"]),
        _metric("metric:b", marts=["mart_b"]),
    ]
    assert lineage_candidates(metrics, {"MARTS.mart_a": set()}) == []


# ---------------------------------------------------------------------------
# cross_domain — True iff the metrics' domain_ids are disjoint
# ---------------------------------------------------------------------------


def test_cross_domain_true_when_domains_disjoint() -> None:
    """Disjoint ``domain_ids`` -> ``cross_domain`` is ``True``."""
    metrics = [
        _metric("metric:a", marts=["mart_x"], domains=["marketing"]),
        _metric("metric:b", marts=["mart_x"], domains=["finance"]),
    ]
    (candidate,), _ = shared_mart_candidates(metrics)
    assert candidate["cross_domain"] is True


def test_cross_domain_false_when_domains_overlap() -> None:
    """Any shared domain -> ``cross_domain`` is ``False``."""
    metrics = [
        _metric("metric:a", marts=["mart_x"], domains=["marketing", "ops"]),
        _metric("metric:b", marts=["mart_x"], domains=["ops"]),
    ]
    (candidate,), _ = shared_mart_candidates(metrics)
    assert candidate["cross_domain"] is False


def test_cross_domain_true_when_domains_empty() -> None:
    """Empty ``domain_ids`` are disjoint from everything -> ``cross_domain`` True."""
    metrics = [
        _metric("metric:a", marts=["mart_x"]),
        _metric("metric:b", marts=["mart_x"]),
    ]
    (candidate,), _ = shared_mart_candidates(metrics)
    assert candidate["cross_domain"] is True


# ---------------------------------------------------------------------------
# generate_candidates — deduped union + per-producer counts
# ---------------------------------------------------------------------------


def test_generate_candidates_unions_producers_and_counts(tmp_path: Path) -> None:
    """The union spans all bases and per-producer counts sum to the candidates.

    Two metrics on *different* marts that ``ref``-chain (``mart_a -> mart_b``) and
    that share one column: shared_mart 0, shared_column 1, lineage 1 -> a 2-row
    deduped union (the bases never collide), with no skipped hubs.
    """
    (tmp_path / "mart_a.sql").write_text(
        "select * from {{ ref('mart_b') }}", encoding="utf-8"
    )
    (tmp_path / "mart_b.sql").write_text("select 1", encoding="utf-8")
    metrics = [
        _metric("metric:a", marts=["mart_a"], columns=["spend"], domains=["d1"]),
        _metric("metric:b", marts=["mart_b"], columns=["spend"], domains=["d1"]),
    ]

    result = generate_candidates(metrics, tmp_path)
    counts = result["counts"]

    assert counts["shared_mart"] == 0
    assert counts["shared_column"] == 1
    assert counts["lineage"] == 1
    assert counts["skipped"] == len(result["skipped_hubs"]) == 0
    # Bases never collide, so the per-producer counts sum to the union length.
    assert len(result["candidates"]) == (
        counts["shared_mart"] + counts["shared_column"] + counts["lineage"]
    )
    assert {c["basis"] for c in result["candidates"]} == {"shared_column", "mart_lineage"}
