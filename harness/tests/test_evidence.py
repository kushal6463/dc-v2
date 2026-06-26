"""NO-DB tests for the deterministic evidence-ledger fold (:mod:`harness.kg.evidence`).

The fold turns an append-only evidence ledger into a Beta posterior (FRD §5.7 /
FR-SCORE-001): ``supports`` events add their weight to alpha, ``refutes`` events
to beta, starting from the Jeffreys prior ``Beta(0.5, 0.5)``; confidence is
``alpha / (alpha + beta)`` and evidence mass is ``alpha + beta``. This module
covers: the empty-ledger prior, a single PRIOR event, the supports/refutes
direction split, the strictly increasing tier pseudo-counts, the legacy
flat-confidence seed (shrunk toward 0.5 at low mass), deterministic /
dedupe-safe event ids, and the lag-plausibility multiplier.

Pure: the module is database-free, so every assertion runs in-process with no
Neo4j and no I/O.
"""

from __future__ import annotations

import pytest

from harness.kg.evidence import (
    EvidenceTier,
    TIER_WEIGHTS,
    fold_ledger,
    lag_plausibility,
    make_event_id,
    seed_prior_event,
)

# ---------------------------------------------------------------------------
# The fold over the ledger (FR-SCORE-001)
# ---------------------------------------------------------------------------


def test_fold_empty_ledger_is_jeffreys_prior() -> None:
    """An empty ledger returns the bare Jeffreys prior: confidence 0.5, mass 1.0."""
    folded = fold_ledger([])
    assert folded["alpha"] == pytest.approx(0.5)
    assert folded["beta"] == pytest.approx(0.5)
    assert folded["confidence"] == pytest.approx(0.5)
    assert folded["evidence_mass"] == pytest.approx(1.0)


def test_single_prior_supports_event() -> None:
    """One PRIOR ``supports`` event of weight 1.0 -> alpha 1.5, beta 0.5, conf 0.75."""
    folded = fold_ledger(
        [{"tier": EvidenceTier.PRIOR.value, "direction": "supports", "weight": 1.0}]
    )
    assert folded["alpha"] == pytest.approx(1.5)
    assert folded["beta"] == pytest.approx(0.5)
    assert folded["confidence"] == pytest.approx(0.75)
    assert folded["evidence_mass"] == pytest.approx(2.0)


def test_supports_adds_to_alpha() -> None:
    """A ``supports`` event adds its tier pseudo-count to alpha, leaving beta at 0.5."""
    folded = fold_ledger(
        [{"tier": EvidenceTier.OBSERVATIONAL.value, "direction": "supports"}]
    )
    # OBSERVATIONAL default weight is 2.0 -> alpha = 0.5 + 2.0, beta untouched.
    assert folded["alpha"] == pytest.approx(0.5 + TIER_WEIGHTS[EvidenceTier.OBSERVATIONAL])
    assert folded["beta"] == pytest.approx(0.5)


def test_refutes_adds_to_beta() -> None:
    """A ``refutes`` event adds its tier pseudo-count to beta, leaving alpha at 0.5."""
    folded = fold_ledger(
        [{"tier": EvidenceTier.OBSERVATIONAL.value, "direction": "refutes"}]
    )
    # OBSERVATIONAL default weight is 2.0 -> beta = 0.5 + 2.0, alpha untouched.
    assert folded["beta"] == pytest.approx(0.5 + TIER_WEIGHTS[EvidenceTier.OBSERVATIONAL])
    assert folded["alpha"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Tier pseudo-count weights (FRD §5.7)
# ---------------------------------------------------------------------------


def test_tier_weights_strictly_increasing() -> None:
    """Tier weights rise strictly PRIOR < OBSERVATIONAL < QUASI < INTERVENTIONAL < HUMAN."""
    assert (
        TIER_WEIGHTS[EvidenceTier.PRIOR]
        < TIER_WEIGHTS[EvidenceTier.OBSERVATIONAL]
        < TIER_WEIGHTS[EvidenceTier.QUASI_EXPERIMENTAL]
        < TIER_WEIGHTS[EvidenceTier.INTERVENTIONAL]
        < TIER_WEIGHTS[EvidenceTier.HUMAN]
    )


# ---------------------------------------------------------------------------
# Seeding a legacy flat confidence into the ledger (shrunk toward 0.5)
# ---------------------------------------------------------------------------


def test_seed_prior_event_folds_to_shrunk_confidence() -> None:
    """A 0.8 legacy confidence seeds to conf ~0.74 at mass ~5.0 -- shrunk toward 0.5."""
    events = seed_prior_event(
        0.8, attribution="legacy:migration", timestamp="2026-01-01T00:00:00Z"
    )
    # Two PRIOR events (supports then refutes), each carrying a deterministic id.
    assert len(events) == 2
    assert [e["direction"] for e in events] == ["supports", "refutes"]
    assert all(e["tier"] == EvidenceTier.PRIOR.value for e in events)
    assert all(e["event_id"] for e in events)

    folded = fold_ledger(events)
    assert folded["confidence"] == pytest.approx(0.74, abs=0.01)
    assert folded["evidence_mass"] == pytest.approx(5.0)
    # Calibrated honesty: a low-mass legacy guess reads shrunk, strictly inside (0.5, 0.8).
    assert 0.5 < folded["confidence"] < 0.8


# ---------------------------------------------------------------------------
# Deterministic, dedupe-safe event identity
# ---------------------------------------------------------------------------


def test_make_event_id_is_deterministic() -> None:
    """The same event maps to the same 16-hex-char id on every call (no clock/random)."""
    event = {
        "tier": EvidenceTier.HUMAN.value,
        "direction": "supports",
        "attribution": "analyst:jane",
        "timestamp": "2026-06-25T12:00:00Z",
    }
    first = make_event_id(event)
    assert first == make_event_id(dict(event))
    assert len(first) == 16
    assert all(c in "0123456789abcdef" for c in first)


def test_make_event_id_is_dedupe_safe() -> None:
    """Identity is tier/direction/attribution/timestamp only -> re-append is idempotent."""
    base = {
        "tier": EvidenceTier.PRIOR.value,
        "direction": "supports",
        "attribution": "x",
        "timestamp": "t",
    }
    # Non-identity fields (e.g. weight) do not change the id -> safe to dedupe on it.
    assert make_event_id({**base, "weight": 3.2}) == make_event_id({**base, "weight": 99.0})
    # Flipping an identity field (direction) yields a different id.
    assert make_event_id(base) != make_event_id({**base, "direction": "refutes"})


# ---------------------------------------------------------------------------
# Lag plausibility for time-aware path scoring (FR-SCORE-003)
# ---------------------------------------------------------------------------


def test_lag_plausibility_observed_is_one() -> None:
    """An observed (data-derived) lag carries no penalty, scoring exactly 1.0."""
    assert lag_plausibility("P120D", observed=True) == pytest.approx(1.0)


def test_lag_plausibility_long_estimated_lag_is_discounted_and_floored() -> None:
    """A long estimated lag is discounted below 1.0 but floored at 0.3."""
    score = lag_plausibility("P120D")
    assert score < 1.0
    assert score >= 0.3
    assert score == pytest.approx(0.3)  # exp(-120/60) ~ 0.135, clamped to the 0.3 floor


@pytest.mark.parametrize("temporal_lag", [None, "", "P1D", "PT48H", "P120D", "garbage"])
def test_lag_plausibility_stays_in_unit_interval(temporal_lag: str | None) -> None:
    """Every estimated-lag score is a multiplier in ``(0, 1]``."""
    score = lag_plausibility(temporal_lag)
    assert 0.0 < score <= 1.0
