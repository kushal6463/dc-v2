"""NO-DB tests for the Phase 5 edge reconciliation diff.

These cover the pure, unit-testable core of the deprecate-never-delete reconcile
pass (:func:`harness.kg.reconcile.compute_edge_diff`): a stale deterministic edge
is deprecated; a review-protected ``INFLUENCES`` edge absent from the recompute is
skipped (never deprecated); an edge present in both is unchanged; a new computed
edge is added. Everything here runs without Neo4j — :func:`compute_edge_diff` is a
pure function over two edge lists plus the eligible-source-kind set.
"""

from __future__ import annotations

from typing import Any

from harness.kg.reconcile import (
    ELIGIBLE_SOURCE_KINDS,
    _edge_key,
    compute_edge_diff,
)


def _live(
    from_id: str,
    rel_type: str,
    relation: str,
    to_id: str,
    source_kind: str,
    **kw: Any,
) -> dict[str, Any]:
    """Build a live-shape edge dict (flat fields, as read back from Neo4j)."""
    return {
        "from_id": from_id,
        "rel_type": rel_type,
        "relation": relation,
        "to_id": to_id,
        "source_kind": source_kind,
        **kw,
    }


def _computed(
    from_id: str, rel_type: str, relation: str, to_id: str, source_kind: str
) -> dict[str, Any]:
    """Build a computed-shape edge dict (the arbitration edge payload shape).

    Provenance / subtype live inside ``properties`` — exactly the shape a causal
    edge proposal's ``payload`` carries — so the diff's key extractor must read
    nested ``properties`` too.
    """
    return {
        "type": rel_type,
        "from_id": from_id,
        "to_id": to_id,
        "properties": {"relation": relation, "source_kind": source_kind},
    }


def test_eligible_source_kinds_excludes_review_protected() -> None:
    """The eligible (auto-deprecate) set never contains a review-protected kind."""
    assert "statistical_proposal" not in ELIGIBLE_SOURCE_KINDS  # protected wins
    assert "curated_rule" not in ELIGIBLE_SOURCE_KINDS
    assert "llm_proposal" not in ELIGIBLE_SOURCE_KINDS
    assert "formula_parse" in ELIGIBLE_SOURCE_KINDS
    assert "scope_rollup" in ELIGIBLE_SOURCE_KINDS


def test_stale_deterministic_edge_is_deprecated() -> None:
    """A live deterministic edge absent from the recompute -> deprecated."""
    stale = _live("roas", "DECOMPOSES_INTO", "formula", "spend", "formula_parse")
    diff = compute_edge_diff([stale], [], set(ELIGIBLE_SOURCE_KINDS))

    assert diff["deprecated"] == [stale]
    assert diff["skipped"] == []
    assert diff["unchanged"] == []
    assert diff["added"] == []


def test_review_protected_influence_absent_is_skipped_not_deprecated() -> None:
    """A review-protected INFLUENCES edge absent from the recompute -> skipped."""
    protected = _live(
        "spend", "INFLUENCES", "curated_rule", "revenue", "curated_rule"
    )
    diff = compute_edge_diff([protected], [], set(ELIGIBLE_SOURCE_KINDS))

    assert diff["skipped"] == [protected]
    assert diff["deprecated"] == []  # never auto-deprecated
    assert diff["unchanged"] == []
    assert diff["added"] == []


def test_unchanged_edge_is_unchanged() -> None:
    """An edge present in BOTH the live and computed sets -> unchanged."""
    live = _live("roas", "DECOMPOSES_INTO", "formula", "spend", "formula_parse")
    computed = _computed(
        "roas", "DECOMPOSES_INTO", "formula", "spend", "formula_parse"
    )
    diff = compute_edge_diff([live], [computed], set(ELIGIBLE_SOURCE_KINDS))

    assert diff["unchanged"] == [live]
    assert diff["added"] == []
    assert diff["deprecated"] == []
    assert diff["skipped"] == []


def test_new_computed_edge_is_added() -> None:
    """A computed edge whose key is absent from the live set -> added."""
    computed = _computed(
        "roas", "DECOMPOSES_INTO", "formula", "revenue", "formula_parse"
    )
    diff = compute_edge_diff([], [computed], set(ELIGIBLE_SOURCE_KINDS))

    assert diff["added"] == [computed]
    assert diff["unchanged"] == []
    assert diff["deprecated"] == []
    assert diff["skipped"] == []


def test_full_partition_mix() -> None:
    """A realistic mix lands each edge in exactly one bucket."""
    unchanged = _live("roas", "DECOMPOSES_INTO", "formula", "spend", "formula_parse")
    stale = _live("aov", "DECOMPOSES_INTO", "rollup", "aov_ch", "scope_rollup")
    protected = _live("spend", "INFLUENCES", "llm_verified", "rev", "llm_proposal")

    live_edges = [unchanged, stale, protected]
    computed_edges = [
        # mirrors `unchanged`
        _computed("roas", "DECOMPOSES_INTO", "formula", "spend", "formula_parse"),
        # brand-new edge -> added
        _computed("rev", "DECOMPOSES_INTO", "formula", "orders", "formula_parse"),
    ]

    diff = compute_edge_diff(
        live_edges, computed_edges, set(ELIGIBLE_SOURCE_KINDS)
    )

    assert diff["unchanged"] == [unchanged]
    assert diff["deprecated"] == [stale]
    assert diff["skipped"] == [protected]
    assert [_edge_key(e) for e in diff["added"]] == [
        ("rev", "DECOMPOSES_INTO", "formula", "orders")
    ]

    # Partition is total + disjoint: every edge accounted for exactly once.
    assert (
        len(diff["unchanged"])
        + len(diff["deprecated"])
        + len(diff["skipped"])
        == len(live_edges)
    )


def test_relation_distinguishes_otherwise_identical_edges() -> None:
    """Same endpoints + rel_type but a different ``relation`` are distinct edges."""
    formula = _live("roas", "DECOMPOSES_INTO", "formula", "spend", "formula_parse")
    identity = _live("roas", "DECOMPOSES_INTO", "identity", "spend", "identity_fallback")
    # Recompute only reproduces the formula edge; the identity edge is now stale.
    computed = [_computed("roas", "DECOMPOSES_INTO", "formula", "spend", "formula_parse")]

    diff = compute_edge_diff([formula, identity], computed, set(ELIGIBLE_SOURCE_KINDS))

    assert diff["unchanged"] == [formula]
    assert diff["deprecated"] == [identity]  # identity_fallback is eligible
    assert diff["added"] == []


def test_edge_key_reads_nested_and_flat_shapes() -> None:
    """The key extractor yields the same tuple for the live + computed shapes."""
    live = _live("a", "INFLUENCES", "statistical", "b", "statistical_proposal")
    computed = _computed("a", "INFLUENCES", "statistical", "b", "statistical_proposal")
    assert _edge_key(live) == _edge_key(computed) == (
        "a", "INFLUENCES", "statistical", "b"
    )
