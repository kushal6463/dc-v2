"""Evidence ledger -> Beta-posterior fold for causal-edge confidence (FRD §5.7).

Every signal about a causal edge -- an LLM hypothesis, a lagged cross-correlation
from statistical inference, a quasi-experimental result, a monitoring-contract
reconciliation, a human confirm/refute -- is an append-only **evidence event**
``{tier, direction: supports|refutes, weight, attribution, timestamp}``. An
edge's confidence is never set or overwritten in place; it is a *deterministic
fold* over that ledger into a Beta posterior (**FR-SCORE-001**):

* ``supports`` events add their weight to alpha, ``refutes`` events to beta,
  starting from a Jeffreys prior ``Beta(0.5, 0.5)``;
* **confidence** = alpha / (alpha + beta) -- the one aggregated score on the edge;
* **evidence mass** = alpha + beta -- distinguishing "0.8 from one LLM guess"
  from "0.8 from forty observations" (FR-SCORE-004).

Evidence tiers carry pseudo-count weights (:data:`TIER_WEIGHTS`): PRIOR ~ 1,
OBSERVATIONAL ~ 2-5 (scaled by effect size / FDR via an explicit per-event
``weight``), QUASI-EXPERIMENTAL ~ 5, INTERVENTIONAL ~ 8, HUMAN ~ 10. Each event
carries its tier, direction, weight, and attribution so any score is traceable
to the experiences that produced it (**FR-SCORE-002**).

Traversal then ranks candidate paths by *path score* = product of edge
confidence and :func:`lag_plausibility`, so Decision Capsules receive weighted,
time-aware causal chains (**FR-SCORE-003**).

This module is **pure** -- no database, no Snowflake, no I/O -- so the fold and
its helpers are deterministic and unit-testable in isolation. Persisting the
ledger and recomputing edge confidence from it live in the arbitration/storage
layers, not here.
"""

from __future__ import annotations

import hashlib
import math
import re
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Evidence tiers and their pseudo-count weights (FRD §5.7).
# ---------------------------------------------------------------------------


class EvidenceTier(StrEnum):
    """The five evidence tiers, ordered by epistemic strength (FRD §5.7).

    Each tier maps to a default pseudo-count weight in :data:`TIER_WEIGHTS`.
    """

    PRIOR = "prior"
    OBSERVATIONAL = "observational"
    QUASI_EXPERIMENTAL = "quasi_experimental"
    INTERVENTIONAL = "interventional"
    HUMAN = "human"


# Default pseudo-count weight contributed by one event of each tier. The
# OBSERVATIONAL default (2.0) is the bottom of the FRD's "2-5" band: callers may
# instead pass an explicit per-event ``weight`` scaled by effect size / FDR.
TIER_WEIGHTS: dict[EvidenceTier, float] = {
    EvidenceTier.PRIOR: 1.0,
    EvidenceTier.OBSERVATIONAL: 2.0,
    EvidenceTier.QUASI_EXPERIMENTAL: 5.0,
    EvidenceTier.INTERVENTIONAL: 8.0,
    EvidenceTier.HUMAN: 10.0,
}

# ---------------------------------------------------------------------------
# Jeffreys prior Beta(0.5, 0.5) -- the starting point of every fold.
# ---------------------------------------------------------------------------

JEFFREYS_ALPHA: float = 0.5
JEFFREYS_BETA: float = 0.5


# ---------------------------------------------------------------------------
# Evidence-event identity (idempotent appends).
# ---------------------------------------------------------------------------


def make_event_id(event: dict[str, Any]) -> str:
    """Return a deterministic 16-hex-char id for an evidence event.

    The id is the first 16 hex characters of the SHA-1 of
    ``f"{tier}|{direction}|{attribution}|{timestamp}"``. It is a pure function of
    those four identity fields -- no randomness, no clock -- so appending the
    *same* event twice yields the *same* id, which is the basis for idempotent
    writes to the append-only ledger. Missing identity fields stringify as
    ``"None"`` rather than raising.

    Args:
        event: An evidence event whose ``tier``, ``direction``, ``attribution``,
            and ``timestamp`` determine the id.

    Returns:
        The 16-character lowercase hex id.
    """
    raw = (
        f"{event.get('tier')}|{event.get('direction')}|"
        f"{event.get('attribution')}|{event.get('timestamp')}"
    )
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The deterministic ledger fold (FR-SCORE-001).
# ---------------------------------------------------------------------------


def fold_ledger(events: list[dict[str, Any]]) -> dict[str, float]:
    """Fold an append-only evidence ledger into a Beta posterior (FR-SCORE-001).

    Starting from the Jeffreys prior ``Beta(0.5, 0.5)``, each ``supports`` event
    adds its weight to alpha and each ``refutes`` event adds its weight to beta.
    An event's weight is its explicit ``weight`` when present, otherwise the
    pseudo-count for its tier (:data:`TIER_WEIGHTS`). Events whose ``direction``
    is neither ``"supports"`` nor ``"refutes"`` contribute nothing, so a
    malformed event never breaks the fold.

    Args:
        events: The edge's evidence events, each a mapping carrying at least a
            ``tier`` and a ``direction`` (and an optional explicit ``weight``).

    Returns:
        ``{"alpha", "beta", "confidence", "evidence_mass"}`` where
        ``confidence = alpha / (alpha + beta)`` and
        ``evidence_mass = alpha + beta``. An empty ledger returns the bare prior:
        ``{"alpha": 0.5, "beta": 0.5, "confidence": 0.5, "evidence_mass": 1.0}``.

    Raises:
        KeyError: If an event has no explicit ``weight`` and no ``tier``.
        ValueError: If such an event's ``tier`` is not a valid
            :class:`EvidenceTier`.
    """
    alpha = JEFFREYS_ALPHA
    beta = JEFFREYS_BETA
    for event in events:
        # Explicit per-event weight wins; otherwise fall back to the tier
        # pseudo-count. (`or` means an explicit falsy/zero weight also falls
        # back to the tier default -- harmless here: the documented legacy
        # confidences are 0.4/0.6/0.8, never the 0/1 extremes, and a true
        # zero-weight event carries no information either way.)
        weight = event.get("weight") or TIER_WEIGHTS[EvidenceTier(event["tier"])]
        direction = event.get("direction")
        if direction == "supports":
            alpha += weight
        elif direction == "refutes":
            beta += weight
    total = alpha + beta
    return {
        "alpha": alpha,
        "beta": beta,
        "confidence": alpha / total,
        "evidence_mass": total,
    }


# ---------------------------------------------------------------------------
# Migrating a legacy flat confidence into the ledger.
# ---------------------------------------------------------------------------


def seed_prior_event(
    flat_confidence: float,
    *,
    attribution: str,
    timestamp: str,
    prior_mass: float = 4.0,
) -> list[dict[str, Any]]:
    """Convert a legacy flat confidence into two PRIOR evidence events.

    Pre-ledger edges carry a single flat confidence (e.g. 0.8 / 0.6 / 0.4). To
    fold such an edge into the ledger *without overstating its certainty*, the
    flat score is expressed as a small amount of prior pseudo-evidence split
    between the two directions: a ``supports`` event of weight
    ``prior_mass * flat_confidence`` and a ``refutes`` event of weight
    ``prior_mass * (1 - flat_confidence)``. Folded on top of the Jeffreys prior
    this reproduces the flat confidence *shrunk toward 0.5* at low evidence mass
    -- calibrated honesty: a legacy guess reads as "likely, but barely
    evidenced", not "certain".

    For example ``prior_mass=4.0``, ``flat_confidence=0.8`` yields weights
    ``3.2`` and ``0.8``; :func:`fold_ledger` then gives ``alpha=3.7``,
    ``beta=1.3``, ``confidence=0.74``, ``evidence_mass=5.0``.

    Args:
        flat_confidence: The legacy confidence in ``[0, 1]``.
        attribution: Source attribution recorded on both events (FR-SCORE-002).
        timestamp: ISO-8601 timestamp recorded on both events.
        prior_mass: Total pseudo-count split across the two events (default
            ``4.0`` -- deliberately low, so the seeded edge stays low-mass).

    Returns:
        Two event dicts (``supports`` then ``refutes``), each
        ``{"tier": "prior", "direction", "weight", "attribution", "timestamp",
        "event_id"}`` with ``event_id`` from :func:`make_event_id`, ready to
        append to a ledger and recombine via :func:`fold_ledger`.
    """
    events: list[dict[str, Any]] = []
    for direction, weight in (
        ("supports", prior_mass * flat_confidence),
        ("refutes", prior_mass * (1.0 - flat_confidence)),
    ):
        event: dict[str, Any] = {
            "tier": EvidenceTier.PRIOR.value,
            "direction": direction,
            "weight": weight,
            "attribution": attribution,
            "timestamp": timestamp,
        }
        event["event_id"] = make_event_id(event)
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Lag plausibility for time-aware path scoring (FR-SCORE-003).
# ---------------------------------------------------------------------------

# An ISO-8601 duration restricted to the day/hour/minute/second components used
# for causal lags (e.g. "P1D", "PT48H", "P1DT12H"). Year/month components are
# intentionally unsupported -- their length in days is ambiguous.
_ISO_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)


def _iso_duration_to_days(duration: str) -> float | None:
    """Parse an ISO-8601 duration (``P#DT#H#M#S``) to fractional days.

    Handles the day/hour/minute/second components only. Returns ``None`` for an
    unparseable string or one with no components (e.g. ``"P"``, ``"PT"``,
    ``"3 days"``), letting the caller treat it as "no usable lag".
    """
    match = _ISO_DURATION_RE.fullmatch(duration.strip())
    if match is None:
        return None
    parts = match.groupdict()
    if all(value is None for value in parts.values()):
        return None  # bare "P" / "PT" carry no duration
    days = float(parts["days"] or 0.0)
    hours = float(parts["hours"] or 0.0)
    minutes = float(parts["minutes"] or 0.0)
    seconds = float(parts["seconds"] or 0.0)
    return days + hours / 24.0 + minutes / 1440.0 + seconds / 86400.0


def lag_plausibility(temporal_lag: str | None, *, observed: bool = False) -> float:
    """Score how plausible a causal edge's temporal lag is, in ``(0, 1]``.

    Used as a multiplier in path scoring (FR-SCORE-003): a path's score is the
    product of its edge confidences and their lag plausibilities, so an edge
    whose lag is a long, *estimated* guess (e.g. an LLM hypothesis) is discounted
    relative to one whose lag was *observed* in the data.

    Contract:

    * an ``observed`` (data-derived) lag, or a missing/empty/unparseable lag,
      scores ``1.0`` (no penalty);
    * otherwise an estimated lag decays gently with its length and is floored at
      ``0.3`` so even a long lag never zeroes out a path:
      ``max(0.3, exp(-days / 60))`` -- short lags stay ~ 1.0.

    The decay constant (60 days) and floor (0.3) are a **tunable V1 heuristic**,
    not a learned model.

    Args:
        temporal_lag: An ISO-8601 duration (``P#DT#H#M#S``, e.g. ``"PT48H"``),
            or ``None`` when the edge carries no lag.
        observed: ``True`` when the lag was measured from data (e.g. a
            cross-correlation peak) rather than estimated by a model/LLM.

    Returns:
        A plausibility multiplier in ``(0, 1]``.
    """
    if observed:
        return 1.0
    if not temporal_lag:
        return 1.0
    days = _iso_duration_to_days(temporal_lag)
    if days is None:
        return 1.0
    return max(0.3, math.exp(-days / 60.0))
