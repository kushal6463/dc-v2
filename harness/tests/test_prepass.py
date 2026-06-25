"""NO-DB tests for the deterministic pre-pass (M2 product decision).

The pre-pass no longer emits one UIComponent draft per chart-registry entry
(646, 1:1 with Metric — too repetitive). Instead the per-chart registry
semantics are folded onto each Metric draft, and chart types are seeded as a
small fixed set of generalised type nodes at bootstrap. These tests assert the
new contract: 90 dashboards, 989 metric drafts, 0 per-entry component drafts,
each metric draft validates against the model and carries the folded fields.

Counts track the current ``docs/frd-docs/chart-registry.json`` snapshot (90
dashboards / 989 entries). NOTE: the legacy prepass still emits one coarse draft
per registry entry; the meaningful scoped Metric nodes are now built by the LLM
agentic builder (:mod:`harness.agentic`), which supersedes prepass as the metric
source.
"""

from __future__ import annotations

from harness.ingest import prepass
from harness.kg.models import Metric


def test_prepass_counts() -> None:
    """Per-entry components are 0; metrics track the registry (989 / 90 dashboards)."""
    counts = prepass.run_prepass()["counts"]
    assert counts["dashboards"] == 90
    assert counts["metrics"] == 989
    # M2 product decision: no per-entry UIComponent drafts.
    assert counts["components"] == 0


def test_prepass_buckets_have_no_per_entry_components() -> None:
    """Every dashboard bucket keeps an empty ``components`` list (backward-compat)."""
    dashboards = prepass.run_prepass()["dashboards"]
    assert len(dashboards) == 90
    for bucket in dashboards.values():
        assert bucket["components"] == []
        assert bucket["metrics"]


def test_prepass_metric_drafts_fold_registry_fields_and_validate() -> None:
    """Each metric draft validates and folds in the chart-registry semantics."""
    dashboards = prepass.run_prepass()["dashboards"]
    total = 0
    saw_how_to_read = False
    saw_narration = False
    for bucket in dashboards.values():
        for draft in bucket["metrics"]:
            total += 1
            # Folded-in registry fields are present on the draft.
            assert "chart_id" in draft
            assert "how_to_read" in draft
            assert "decisions_answered" in draft
            # The draft validates against the (now-extended) Metric model.
            metric = Metric(**draft)
            assert metric.chart_id == draft["chart_id"]
            assert metric.how_to_read == draft["how_to_read"]
            if metric.how_to_read:
                saw_how_to_read = True
            if metric.narration_text:
                saw_narration = True
            # Pre-pass never classifies chart_type — the agent does.
            assert metric.chart_type is None
    assert total == 989
    # The registry has how_to_read on every entry and narration on most.
    assert saw_how_to_read
    assert saw_narration
