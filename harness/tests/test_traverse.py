"""Unit tests for the unified causal-path traversal helpers.

NO-DB tests cover the pure shaping/parsing/scoring helpers the traversal payload
is built from: :func:`harness.api.server._hop_kind` (rel_type -> structural|
causal), :func:`harness.api.server._hop_sign` (structural ``role`` -> per-hop
sign), :func:`harness.api.server._shape_hop` (raw rel-map -> wire edge with the
pinned ``from/to/rel_type/relation/kind/role/sign/confidence/temporal_lag/
lag_plausibility`` fields), :func:`harness.api.server._score_path` (raw rel-maps
-> ``(score, cumulative_lag_days, path_sign)`` per FR-SCORE-003) and
:func:`harness.api.server._lag_to_days` (ISO-8601 duration -> fractional days).

The acyclicity guard was removed when the traversal began *returning* cyclic
paths separately (``cyclic_paths``) instead of dropping them, so there is no
longer an ``_acyclicity_clause`` helper to test.
"""

from __future__ import annotations

import math

import pytest

from harness.api.server import (
    _hop_kind,
    _hop_sign,
    _lag_to_days,
    _score_path,
    _shape_hop,
)


# ---------------------------------------------------------------------------
# _hop_kind — rel_type -> structural | causal
# ---------------------------------------------------------------------------


def test_hop_kind_decomposes_into_is_structural() -> None:
    """A ``DECOMPOSES_INTO`` hop is a structural (formula/identity) edge."""
    assert _hop_kind("DECOMPOSES_INTO") == "structural"


def test_hop_kind_influences_is_causal() -> None:
    """An ``INFLUENCES`` hop is a causal edge."""
    assert _hop_kind("INFLUENCES") == "causal"


@pytest.mark.parametrize("rel_type", ["INFLUENCES", "ROLLS_UP_TO", "", None, "other"])
def test_hop_kind_anything_not_structural_is_causal(rel_type: object) -> None:
    """Only ``DECOMPOSES_INTO`` is structural; everything else is causal."""
    assert _hop_kind(rel_type) == "causal"


# ---------------------------------------------------------------------------
# _hop_sign — structural role -> per-hop sign (+1 / -1 / 0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["denominator", "subtrahend"])
def test_hop_sign_inverse_roles_are_negative(role: str) -> None:
    """A divisor / subtracted component enters its parent inversely (-1)."""
    assert _hop_sign(role) == -1


@pytest.mark.parametrize(
    "role", ["numerator", "addend", "factor", "component", "driver"]
)
def test_hop_sign_additive_roles_are_positive(role: str) -> None:
    """Every other known structural role is additive (+1)."""
    assert _hop_sign(role) == 1


@pytest.mark.parametrize("role", [None, "", "bogus"])
def test_hop_sign_missing_or_unknown_role_is_zero(role: object) -> None:
    """No role (a causal hop) or an unrecognised role is unsigned (0)."""
    assert _hop_sign(role) == 0


# ---------------------------------------------------------------------------
# _shape_hop — raw rel-map -> stable wire edge
# ---------------------------------------------------------------------------


def test_shape_hop_full_structural_edge() -> None:
    """A complete structural rel-map maps to all pinned fields + kind + sign."""
    edge = _shape_hop(
        {
            "from": "m_a",
            "to": "m_b",
            "rel_type": "DECOMPOSES_INTO",
            "relation": "formula",
            "role": "numerator",
            "confidence": 1.0,
            "temporal_lag": "P0D",
            "lag_plausibility": 0.9,
        }
    )
    assert edge == {
        "from": "m_a",
        "to": "m_b",
        "rel_type": "DECOMPOSES_INTO",
        "relation": "formula",
        "kind": "structural",
        "role": "numerator",
        "sign": 1,
        "confidence": 1.0,
        "temporal_lag": "P0D",
        "lag_plausibility": 0.9,
    }


def test_shape_hop_denominator_role_is_negative_sign() -> None:
    """A ``denominator`` structural hop carries sign ``-1``."""
    edge = _shape_hop(
        {
            "from": "spend",
            "to": "roas",
            "rel_type": "DECOMPOSES_INTO",
            "relation": "formula",
            "role": "denominator",
        }
    )
    assert edge["sign"] == -1


def test_shape_hop_causal_edge_labels_kind_causal() -> None:
    """An ``INFLUENCES`` rel-map is labelled ``kind == "causal"`` with sign 0."""
    edge = _shape_hop(
        {
            "from": "m_x",
            "to": "m_y",
            "rel_type": "INFLUENCES",
            "relation": "drives",
            "confidence": 0.6,
            "temporal_lag": "P3D",
        }
    )
    assert edge["kind"] == "causal"
    assert edge["rel_type"] == "INFLUENCES"
    assert edge["confidence"] == 0.6
    assert edge["sign"] == 0


def test_shape_hop_exposes_exactly_the_pinned_fields() -> None:
    """The wire edge always carries exactly the pinned hop keys (stable shape)."""
    edge = _shape_hop(
        {
            "from": "a",
            "to": "b",
            "rel_type": "INFLUENCES",
            "relation": None,
            "confidence": None,
            "temporal_lag": None,
        }
    )
    assert set(edge) == {
        "from",
        "to",
        "rel_type",
        "relation",
        "kind",
        "role",
        "sign",
        "confidence",
        "temporal_lag",
        "lag_plausibility",
    }


def test_shape_hop_missing_endpoints_become_none() -> None:
    """Absent ``from``/``to`` coerce to ``None`` (uniform shape, no KeyError)."""
    edge = _shape_hop({"rel_type": "INFLUENCES"})
    assert edge["from"] is None
    assert edge["to"] is None
    assert edge["relation"] is None
    assert edge["confidence"] is None
    assert edge["temporal_lag"] is None
    assert edge["lag_plausibility"] is None
    assert edge["kind"] == "causal"
    assert edge["role"] is None
    assert edge["sign"] == 0


def test_shape_hop_coerces_endpoints_to_str() -> None:
    """Non-string endpoint values are stringified for a uniform wire shape."""
    edge = _shape_hop({"from": 1, "to": 2, "rel_type": "DECOMPOSES_INTO"})
    assert edge["from"] == "1"
    assert edge["to"] == "2"


# ---------------------------------------------------------------------------
# _lag_to_days — ISO-8601 duration -> fractional days
# ---------------------------------------------------------------------------


def test_lag_to_days_none_is_zero() -> None:
    """A missing lag contributes no days."""
    assert _lag_to_days(None) == 0.0


def test_lag_to_days_p0d_is_zero() -> None:
    """The canonical no-lag duration ``P0D`` is zero days."""
    assert _lag_to_days("P0D") == 0.0


def test_lag_to_days_whole_days() -> None:
    """``P#D`` passes through as whole days."""
    assert _lag_to_days("P3D") == 3.0


def test_lag_to_days_hours_are_fractional_days() -> None:
    """``PT#H`` converts hours to fractional days (6h == 0.25d)."""
    assert math.isclose(_lag_to_days("PT6H"), 0.25)


def test_lag_to_days_minutes_and_seconds() -> None:
    """Minutes and seconds convert to fractional days."""
    assert math.isclose(_lag_to_days("PT30M"), 30.0 / (24.0 * 60.0))
    assert math.isclose(_lag_to_days("PT45S"), 45.0 / (24.0 * 60.0 * 60.0))


def test_lag_to_days_combined_days_and_hours_sum() -> None:
    """A combined ``P#DT#H`` form sums the day + hour parts."""
    assert math.isclose(_lag_to_days("P1DT12H"), 1.5)


def test_lag_to_days_unparseable_is_zero() -> None:
    """An unparseable / empty value is treated as no lag."""
    assert _lag_to_days("garbage") == 0.0
    assert _lag_to_days("") == 0.0


def test_lag_to_days_is_case_insensitive() -> None:
    """Lowercase ISO durations parse the same as uppercase."""
    assert _lag_to_days("p2d") == 2.0


# ---------------------------------------------------------------------------
# _score_path — raw rel-maps -> (score, cumulative_lag_days, path_sign)
# ---------------------------------------------------------------------------


def test_score_path_empty_is_neutral() -> None:
    """An empty path scores the multiplicative / additive identities."""
    assert _score_path([]) == (1.0, 0.0, 1)


def test_score_path_product_of_confidence_times_lag_plausibility() -> None:
    """FR-SCORE-003: score is the product of ``confidence × lag_plausibility``."""
    score, lag, sign = _score_path(
        [
            {"confidence": 0.5, "lag_plausibility": 0.8, "role": "numerator"},
            {"confidence": 0.5, "lag_plausibility": 0.5, "role": "factor"},
        ]
    )
    assert math.isclose(score, 0.5 * 0.8 * 0.5 * 0.5)
    assert lag == 0.0
    assert sign == 1


def test_score_path_missing_factors_count_as_one() -> None:
    """A missing / unparseable confidence or lag_plausibility is the neutral 1.0."""
    score, _lag, _sign = _score_path(
        [
            {"confidence": 0.6},  # no lag_plausibility -> 1.0
            {"lag_plausibility": 0.5},  # no confidence -> 1.0
            {"confidence": "bad", "lag_plausibility": None},  # unparseable / None
        ]
    )
    assert math.isclose(score, 0.6 * 0.5)


def test_score_path_cumulative_lag_sums_days() -> None:
    """Cumulative lag sums :func:`_lag_to_days` over each hop's ``temporal_lag``."""
    _score, lag, _sign = _score_path(
        [{"temporal_lag": "P2D"}, {"temporal_lag": "PT12H"}, {"temporal_lag": None}]
    )
    assert math.isclose(lag, 2.5)


def test_score_path_sign_is_product_of_hop_signs() -> None:
    """``path_sign`` is the product of per-hop signs (a denominator flips it)."""
    _score, _lag, sign = _score_path(
        [{"role": "numerator"}, {"role": "denominator"}]
    )
    assert sign == -1


def test_score_path_causal_hop_zeroes_the_sign() -> None:
    """An unsigned causal hop (no ``role``) zeroes the whole path's sign."""
    _score, _lag, sign = _score_path(
        [{"role": "numerator"}, {"confidence": 0.6}]  # second hop has no role
    )
    assert sign == 0


# ---------------------------------------------------------------------------
# _score_path — pinned-value regression checks (the contract's worked examples)
# ---------------------------------------------------------------------------


def test_score_path_confidence_times_lag_plausibility_exact() -> None:
    """A single hop scores ``confidence × lag_plausibility``: 0.8 × 0.5 == 0.4."""
    score, _lag, _sign = _score_path([{"confidence": 0.8, "lag_plausibility": 0.5}])
    assert math.isclose(score, 0.4)


def test_score_path_missing_lag_plausibility_is_factor_one() -> None:
    """A hop with no ``lag_plausibility`` uses the neutral 1.0: score == confidence."""
    score, _lag, _sign = _score_path([{"confidence": 0.8}])
    assert math.isclose(score, 0.8)


def test_score_path_cumulative_lag_p1d_plus_pt12h_is_one_and_a_half() -> None:
    """Cumulative lag sums :func:`_lag_to_days`: ``P1D`` + ``PT12H`` == 1.5 days."""
    _score, lag, _sign = _score_path(
        [{"temporal_lag": "P1D"}, {"temporal_lag": "PT12H"}]
    )
    assert math.isclose(lag, 1.5)


def test_score_path_path_sign_multiplies_hop_signs() -> None:
    """``path_sign`` is the product of per-hop signs: two denominators -> +1."""
    _score, _lag, sign = _score_path(
        [{"role": "denominator"}, {"role": "denominator"}]
    )
    assert sign == 1
