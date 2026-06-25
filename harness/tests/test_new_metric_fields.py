"""NO-DB tests for the new agentic-build fields on the :class:`Metric` model.

The agentic build adds node-classification + ML + lineage fields to ``Metric``
(``node_kind``/``has_endpoint``/``ml_kind``/``is_ml``/``ml_task``/``ml_model``/
``ml_entity``/``source_expr``/``bc2_ref``). This module asserts the model accepts
them, that the new enums (``NodeKind``/``MLKind``) and the ``neutral``
``DefaultDirection`` are wired, and that :meth:`Metric.cypher_props` keeps its
contract: ``None`` is dropped, defaults survive, and every value is Neo4j-safe.

NO-DB: builds models in-process; nothing touches Neo4j.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.kg.models import DefaultDirection, Metric, MLKind, NodeKind

# Primitive types that may appear directly as a Neo4j property value.
_PRIMITIVES = (str, int, float, bool)


def _assert_neo4j_safe(props: dict[str, Any]) -> None:
    """Assert every value in ``props`` is a Neo4j-storable primitive/list."""
    for key, value in props.items():
        assert value is not None, f"{key!r} is None (should be excluded)"
        if isinstance(value, list):
            for item in value:
                assert item is not None, f"{key!r} list contains None"
                assert isinstance(item, _PRIMITIVES), (
                    f"{key!r} list element {item!r} is not a primitive"
                )
        else:
            assert isinstance(value, _PRIMITIVES), (
                f"{key!r} value {value!r} is not a primitive/list"
            )


# ---------------------------------------------------------------------------
# New enums + DefaultDirection.neutral
# ---------------------------------------------------------------------------


def test_node_kind_enum_values() -> None:
    """``NodeKind`` carries the four causal-chain node roles as plain strings."""
    assert {k.value for k in NodeKind} == {
        "metric",
        "intermediary",
        "input",
        "constant",
    }


def test_ml_kind_enum_values() -> None:
    """``MLKind`` carries the three ML-metric flavours as plain strings."""
    assert {k.value for k in MLKind} == {"prediction", "performance", "hybrid"}


def test_default_direction_has_neutral() -> None:
    """``DefaultDirection`` gained ``neutral`` (catalog ``polarity`` has it)."""
    assert DefaultDirection.neutral.value == "neutral"


# ---------------------------------------------------------------------------
# Metric accepts the new fields
# ---------------------------------------------------------------------------


def test_metric_defaults_node_kind_and_has_endpoint(metric_payload: dict) -> None:
    """A bare Metric defaults to ``node_kind='metric'`` and ``has_endpoint=True``."""
    metric = Metric(**metric_payload)
    assert metric.node_kind == NodeKind.metric.value
    assert metric.has_endpoint is True
    # The unset ML / lineage fields default to None.
    assert metric.is_ml is None
    assert metric.ml_kind is None
    assert metric.ml_task is None
    assert metric.ml_model is None
    assert metric.ml_entity is None
    assert metric.source_expr is None
    assert metric.bc2_ref is None


def test_metric_accepts_all_new_fields(metric_payload: dict) -> None:
    """A Metric accepts every new agentic-build field with valid values."""
    metric = Metric(
        **metric_payload,
        node_kind="input",
        has_endpoint=False,
        is_ml=True,
        ml_kind="prediction",
        ml_task="timeseries",
        ml_model="prophet",
        ml_entity="customer",
        source_expr="SUM(REVENUE) / SUM(AD_SPEND)",
        bc2_ref="backend/app/repositories/blended.py:432-489",
    )
    assert metric.node_kind == "input"
    assert metric.has_endpoint is False
    assert metric.is_ml is True
    assert metric.ml_kind == "prediction"
    assert metric.ml_task == "timeseries"
    assert metric.ml_model == "prophet"
    assert metric.ml_entity == "customer"
    assert metric.source_expr == "SUM(REVENUE) / SUM(AD_SPEND)"
    assert metric.bc2_ref == "backend/app/repositories/blended.py:432-489"


@pytest.mark.parametrize("kind", ["metric", "intermediary", "input", "constant"])
def test_metric_node_kind_accepts_each_value(metric_payload: dict, kind: str) -> None:
    """Every ``NodeKind`` value is accepted on ``Metric.node_kind``."""
    metric = Metric(**{**metric_payload, "node_kind": kind})
    assert metric.node_kind == kind


def test_metric_rejects_unknown_node_kind(metric_payload: dict) -> None:
    """An out-of-vocab ``node_kind`` fails validation (StrEnum is constrained)."""
    with pytest.raises(Exception):
        Metric(**{**metric_payload, "node_kind": "not_a_kind"})


def test_metric_rejects_unknown_ml_kind(metric_payload: dict) -> None:
    """An out-of-vocab ``ml_kind`` fails validation (StrEnum is constrained)."""
    with pytest.raises(Exception):
        Metric(**{**metric_payload, "ml_kind": "not_a_flavour"})


# ---------------------------------------------------------------------------
# cypher_props contract over the new fields
# ---------------------------------------------------------------------------


def test_cypher_props_drops_none_new_fields(metric_payload: dict) -> None:
    """Unset ML / lineage fields never appear in the property map."""
    props = Metric(**metric_payload).cypher_props()
    for absent in (
        "is_ml",
        "ml_kind",
        "ml_task",
        "ml_model",
        "ml_entity",
        "source_expr",
        "bc2_ref",
    ):
        assert absent not in props, f"{absent!r} should be excluded when None"
    # The two defaulted fields DO survive (they are never None).
    assert props["node_kind"] == "metric"
    assert props["has_endpoint"] is True
    assert None not in props.values()
    _assert_neo4j_safe(props)


def test_cypher_props_emits_new_fields_neo4j_safe(metric_payload: dict) -> None:
    """When set, every new field appears as a Neo4j-safe primitive value."""
    metric = Metric(
        **metric_payload,
        node_kind="constant",
        has_endpoint=False,
        is_ml=True,
        ml_kind="hybrid",
        ml_task="regression",
        ml_model="xgboost",
        ml_entity="product",
        source_expr="SUM(REVENUE)",
        bc2_ref="backend/app/repositories/x.py:10-20",
        default_direction="neutral",
    )
    props = metric.cypher_props()
    expected = {
        "node_kind": "constant",
        "has_endpoint": False,
        "is_ml": True,
        "ml_kind": "hybrid",
        "ml_task": "regression",
        "ml_model": "xgboost",
        "ml_entity": "product",
        "source_expr": "SUM(REVENUE)",
        "bc2_ref": "backend/app/repositories/x.py:10-20",
        "default_direction": "neutral",
    }
    for key, value in expected.items():
        assert props[key] == value, f"{key!r} mismatch: {props.get(key)!r}"
    # Enum-typed fields serialize to plain strings (use_enum_values=True).
    assert isinstance(props["node_kind"], str)
    assert isinstance(props["ml_kind"], str)
    assert isinstance(props["default_direction"], str)
    _assert_neo4j_safe(props)
