"""NO-DB tests for edge ``role`` + ``relation`` validation at the single writer.

The arbitration writer validates a metric->metric edge's ``relation`` subtype
(:func:`~harness.kg.arbitration._validate_relation`) and a structural edge's
``role`` (:func:`~harness.kg.arbitration._validate_edge_props`) against the model
allowlists (:data:`~harness.kg.models.DECOMPOSES_RELATIONS`,
:data:`~harness.kg.models.INFLUENCES_RELATIONS`, :data:`~harness.kg.models.\
EDGE_ROLES`) BEFORE any endpoint lookup. A bad role / relation must therefore be
rejected with a ``ValueError`` before the edge can reach the DB.

These tests use the ``_ExplodingDB`` no-DB pattern (mirroring
``test_apply_validation``): a stand-in :class:`~harness.kg.driver.GraphDB` whose
``read``/``write`` raise. A rejected edge never touches it (``ValueError`` first);
a valid edge passes validation and reaches the presence read — which the
``_ExplodingDB`` turns into a loud ``AssertionError``, proving validation let it
through. NO-DB throughout.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.kg.arbitration import upsert_edge


class _ExplodingDB:
    """A stand-in GraphDB that fails loudly if the writer ever touches the DB.

    A rejected edge must return (raise ``ValueError``) BEFORE any endpoint
    lookup, so any ``read``/``write`` here means a bad edge leaked past
    validation — a test failure.
    """

    def write(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("writer reached for an invalid edge")

    def read(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("writer reached for an invalid edge")


def _edge(db: Any, rel_type: str, props: dict[str, Any]) -> dict[str, Any]:
    """Call :func:`upsert_edge` for a metric->metric edge with ``props``."""
    return upsert_edge(
        db,
        rel_type=rel_type,
        from_label="Metric",
        from_key="metric:a",
        to_label="Metric",
        to_key="metric:b",
        props=props,
    )


# ---------------------------------------------------------------------------
# Invalid structural ``role`` — rejected before the DB
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role", ["numeratorr", "divisor", "weight", "ratio", "", "DENOMINATOR"]
)
def test_invalid_decomposes_role_rejected(role: str) -> None:
    """A ``DECOMPOSES_INTO`` with an out-of-vocab ``role`` raises before any DB."""
    with pytest.raises(ValueError, match="invalid edge role"):
        _edge(
            _ExplodingDB(),
            "DECOMPOSES_INTO",
            {"relation": "formula", "role": role, "confidence": 1.0},
        )


# ---------------------------------------------------------------------------
# Invalid ``relation`` subtypes — rejected before the DB
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relation", ["formulaic", "divides", "computes", "ratio", "llm"]
)
def test_invalid_decomposes_relation_rejected(relation: str) -> None:
    """A ``DECOMPOSES_INTO`` with an unknown ``relation`` raises before any DB."""
    with pytest.raises(ValueError, match="invalid DECOMPOSES_INTO relation"):
        _edge(_ExplodingDB(), "DECOMPOSES_INTO", {"relation": relation})


@pytest.mark.parametrize(
    "relation", ["llm", "causal", "guess", "curated", "statistical_ish"]
)
def test_invalid_influences_relation_rejected(relation: str) -> None:
    """An ``INFLUENCES`` with an unknown ``relation`` raises before any DB."""
    with pytest.raises(ValueError, match="invalid INFLUENCES relation"):
        _edge(
            _ExplodingDB(),
            "INFLUENCES",
            {"relation": relation, "confidence": 0.6},
        )


# ---------------------------------------------------------------------------
# Happy path — a valid edge passes validation and reaches the (exploding) DB
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel_type", "props"),
    [
        ("DECOMPOSES_INTO", {"relation": "formula", "role": "numerator", "confidence": 1.0}),
        ("DECOMPOSES_INTO", {"relation": "formula", "role": "denominator", "confidence": 1.0}),
        ("DECOMPOSES_INTO", {"relation": "component", "role": "component"}),
        ("INFLUENCES", {"relation": "llm_causal", "confidence": 0.6}),
    ],
)
def test_valid_edge_passes_validation_to_db(
    rel_type: str, props: dict[str, Any]
) -> None:
    """A valid role/relation passes validation and only then reaches the DB.

    Validation runs before any endpoint lookup, so a valid edge reaching the
    presence read — which the ``_ExplodingDB`` raises as ``AssertionError`` —
    proves the role/relation were accepted (a ``ValueError`` would mean reject).
    """
    with pytest.raises(AssertionError, match="writer reached"):
        _edge(_ExplodingDB(), rel_type, props)


def test_edge_with_no_role_or_relation_passes_validation() -> None:
    """A spine-style edge (no ``relation``/``role``) is untouched by validation.

    It must reach the DB (the ``_ExplodingDB`` read), proving neither validator
    rejected it for the missing fields.
    """
    with pytest.raises(AssertionError, match="writer reached"):
        upsert_edge(
            _ExplodingDB(),
            rel_type="BELONGS_TO_DOMAIN",
            from_label="Metric",
            from_key="metric:a",
            to_label="Domain",
            to_key="marketing",
        )
