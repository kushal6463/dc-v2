"""Smoke test: ``blended.active_campaigns`` decomposes into platform campaign COUNTS.

Service-free structural guard (no Neo4j / Snowflake) for the active-campaign
platform/network decomposition. Verifies the snapshot encodes the additive
``DECOMPOSES_INTO`` rollup::

    blended.active_campaigns    = google_ads + meta_ads + klaviyo
    google_ads.active_campaigns = search + youtube + shopping + display
                                  + demand_gen + pmax + other
    meta_ads.active_campaigns   = prospecting + retargeting + other

and — the bug this change fixes — that ``blended`` no longer decomposes into
*spend*. This pins the decomposition that the ``kg build`` STRUCTURAL phase draws
from ``formula_components``; if it regresses, the canvas fan-out silently breaks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_SNAPSHOT = Path(__file__).resolve().parents[2] / "data" / "metric_nodes.rare_seeds.json"

PLATFORM_CHILDREN = {
    "google_ads.active_campaigns",
    "meta_ads.active_campaigns",
    "klaviyo.active_campaigns",
}
GOOGLE_CHILDREN = {
    "google_search.active_campaigns",
    "google_youtube.active_campaigns",
    "google_shopping.active_campaigns",
    "google_display.active_campaigns",
    "google_demand_gen.active_campaigns",
    "google_pmax.active_campaigns",
    "google_other.active_campaigns",
}
META_CHILDREN = {
    "meta_prospecting.active_campaigns",
    "meta_retargeting.active_campaigns",
    "meta_other.active_campaigns",
}


@pytest.fixture(scope="module")
def metrics() -> dict:
    return json.loads(_SNAPSHOT.read_text())["metrics"]


def _addends(entry: dict) -> set[str]:
    return {
        c["node_id"]
        for c in entry.get("formula_components", [])
        if c.get("role") == "addend"
    }


def test_blended_decomposes_into_platform_counts(metrics: dict) -> None:
    """blended.active_campaigns rolls up the 3 platform campaign counts — not spend."""
    entry = metrics["blended.active_campaigns"]
    assert _addends(entry) == PLATFORM_CHILDREN
    # The original bug: it must NOT decompose into any ``*.spend``.
    components = {c["node_id"] for c in entry.get("formula_components", [])}
    assert not {c for c in components if c.endswith(".spend")}, (
        f"blended.active_campaigns still decomposes into spend: {sorted(components)}"
    )


def test_google_ads_rolls_up_seven_networks(metrics: dict) -> None:
    assert _addends(metrics["google_ads.active_campaigns"]) == GOOGLE_CHILDREN


def test_meta_ads_rolls_up_three_funnel_stages(metrics: dict) -> None:
    assert _addends(metrics["meta_ads.active_campaigns"]) == META_CHILDREN


def test_every_addend_resolves_to_a_real_node(metrics: dict) -> None:
    """No dangling DECOMPOSES_INTO endpoints anywhere in the rollup tree."""
    for root in {"blended.active_campaigns"} | PLATFORM_CHILDREN:
        for nid in _addends(metrics[root]):
            assert nid in metrics, f"{root} -> {nid} is a dangling addend (no node)"


def test_rollup_children_are_count_metrics(metrics: dict) -> None:
    """Every child is a count metric, keeping the additive sum honest."""
    for nid in PLATFORM_CHILDREN | GOOGLE_CHILDREN | META_CHILDREN:
        entry = metrics.get(nid)
        assert entry is not None, f"{nid} missing from snapshot"
        assert entry.get("concept") == "active_campaigns", f"{nid} concept"
        assert entry.get("unit") == "count", f"{nid} unit"
