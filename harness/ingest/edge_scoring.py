"""Deterministic edge-scoring policy (KG skeleton, plan §9/§14).

A single source of truth for the ``confidence`` / ``evidence_mass`` /
``scoring_policy`` / ``review`` that every metric->metric edge proposal carries at
creation time. This is **not** the full append-only evidence ledger (deferred); it
is the deterministic scoring contract the ledger can later recompute from events.

The ``review`` flag is the single gate the CLI ``--apply-safe`` / auto-approve
paths consult: ``review == False`` => auto-safe (applied through arbitration),
``review == True`` => held for the human review queue.

Edge classes are keyed ``"<TYPE>:<relation>"`` (e.g. ``"DECOMPOSES_INTO:formula"``).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Finite sentinel for deterministic/pinned evidence mass (Neo4j has no inf, and
#: a real float keeps UI sorting sane). Paired with ``deterministic=True`` on the
#: edge properties so consumers can tell "pinned" apart from a measured mass.
PINNED_MASS: float = 1_000_000.0


@dataclass(frozen=True)
class EdgeScore:
    """Resolved score for one edge proposal."""

    confidence: float
    evidence_mass: float
    scoring_policy: str
    review: bool
    deterministic: bool = False


# edge_class -> (confidence | None, evidence_mass | None, scoring_policy, review,
# deterministic). ``None`` confidence/mass means "resolve from source_confidence /
# beta at call time" (see :func:`score_edge`).
_POLICY: dict[str, tuple[float | None, float | None, str, bool, bool]] = {
    # --- DECOMPOSES_INTO (structural) ---
    "DECOMPOSES_INTO:formula": (1.0, PINNED_MASS, "formula_exact_v1", False, True),
    "DECOMPOSES_INTO:component": (1.0, PINNED_MASS, "component_exact_v1", False, True),
    "DECOMPOSES_INTO:identity": (1.0, PINNED_MASS, "identity_fallback_v1", True, True),
    "DECOMPOSES_INTO:crossproduct": (0.9, 1.0, "crossproduct_v1", True, False),
    "DECOMPOSES_INTO:rollup": (0.9, 1.0, "rollup_v1", True, False),
    "DECOMPOSES_INTO:funnel": (0.85, 1.0, "funnel_template_v1", True, False),
    # --- INFLUENCES (causal / statistical) ---
    "INFLUENCES:curated_rule": (0.6, 1.0, "curated_prior_v1", True, False),
    "INFLUENCES:llm_verified": (None, None, "beta_fold_v1", True, False),
    "INFLUENCES:statistical": (None, 1.0, "statistical_import_v1", True, False),
    "INFLUENCES:statistical_candidate": (None, 1.0, "statistical_candidate_v1", True, False),
    "INFLUENCES:promoted": (None, None, "promoted_v1", True, False),
    # A mart-lineage candidate (two metrics share a mart/column, or one mart
    # ref()-depends on the other) is only weak structural co-location evidence —
    # NOT a measured causal link. It is pinned LOW (confidence 0.3, mass 2.0),
    # always review=True (parked as a 'held' edge for the human queue), and
    # NEVER deterministic/auto-safe. Without this row it fell through to the
    # unknown_edge_class_v1 fallback; the explicit row makes the held/low-mass
    # contract intentional and testable.
    "INFLUENCES:mart_lineage": (0.3, 2.0, "mart_lineage_v1", True, False),
}

#: Default confidence when a policy row resolves from source_confidence but none
#: was supplied.
_DEFAULT_SOURCE_CONFIDENCE: dict[str, float] = {
    "INFLUENCES:statistical": 0.5,
    "INFLUENCES:statistical_candidate": 0.4,
    "INFLUENCES:promoted": 0.95,
}


def edge_class(rel_type: str, relation: str) -> str:
    """Build the policy key from a rel type + relation subtype."""
    return f"{rel_type}:{relation}"


def known_edge_classes() -> frozenset[str]:
    """All edge classes the policy recognizes (for validation / tests)."""
    return frozenset(_POLICY)


def score_edge(
    edge_class_key: str,
    *,
    source_confidence: float | None = None,
    beta: tuple[float, float] | None = None,
) -> EdgeScore:
    """Score one edge proposal.

    Args:
        edge_class_key: ``"<TYPE>:<relation>"`` (see :func:`edge_class`).
        source_confidence: measured/source strength, used by policy rows whose
            confidence is ``None`` (statistical / promoted).
        beta: ``(confidence, evidence_mass)`` from
            :func:`harness.ingest.causal.beta_confidence`, used by
            ``INFLUENCES:llm_verified`` (judge/refuter fold).

    Returns:
        An :class:`EdgeScore`. Unknown edge classes fall back to a conservative
        review-only score (confidence 0.5, mass 1.0) so a typo never silently
        auto-applies.
    """
    row = _POLICY.get(edge_class_key)
    if row is None:
        return EdgeScore(
            confidence=source_confidence if source_confidence is not None else 0.5,
            evidence_mass=1.0,
            scoring_policy="unknown_edge_class_v1",
            review=True,
            deterministic=False,
        )
    conf, mass, policy, review, deterministic = row

    if conf is None:
        if beta is not None:
            conf = float(beta[0])
        elif source_confidence is not None:
            conf = float(source_confidence)
        else:
            conf = _DEFAULT_SOURCE_CONFIDENCE.get(edge_class_key, 0.5)
    if mass is None:
        mass = float(beta[1]) if beta is not None else 1.0

    return EdgeScore(
        confidence=round(float(conf), 4),
        evidence_mass=round(float(mass), 4),
        scoring_policy=policy,
        review=review,
        deterministic=deterministic,
    )
