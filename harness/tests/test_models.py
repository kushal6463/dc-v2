"""NO-DB tests for the Pydantic node models and graph-collection invariants.

Covers: the spine seed validates against the models; ``cypher_props`` excludes
``None``, JSON-encodes dict fields, and yields only primitive / homogeneous-list
values; ``NODE_LABELS`` has 10 entries; every ``EDGE_TYPE`` is a non-empty str.
"""

from __future__ import annotations

import json
from typing import Any

from harness.kg.config import REPO_ROOT
from harness.kg.models import (
    EDGE_TYPES,
    NODE_KEY_FIELDS,
    NODE_LABELS,
    Business,
    Domain,
    IntelligenceProduct,
    Metric,
    Proposal,
    UIComponent,
)

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
# Seed validation
# ---------------------------------------------------------------------------


def test_seed_business_validates(spine_seed: dict) -> None:
    """The seed business block builds a valid :class:`Business`."""
    biz = Business.model_validate(spine_seed["business"])
    assert biz.business_id == "rare-seeds"
    assert biz.key_value == "rare-seeds"
    assert biz.LABEL == "Business"
    assert biz.KEY_FIELD == "business_id"


def test_seed_domains_validate(spine_seed: dict) -> None:
    """Every seed domain builds a valid :class:`Domain` with the right key."""
    domains = [Domain.model_validate(d) for d in spine_seed["domains"]]
    assert len(domains) == len(spine_seed["domains"])
    for dom in domains:
        assert dom.LABEL == "Domain"
        assert dom.KEY_FIELD == "domain_id"
        assert dom.key_value == dom.domain_id
        assert dom.domain_id


def test_seed_products_validate(spine_seed: dict) -> None:
    """Every seed product builds a valid :class:`IntelligenceProduct`."""
    products = [IntelligenceProduct.model_validate(p) for p in spine_seed["products"]]
    assert len(products) == len(spine_seed["products"])
    for prod in products:
        assert prod.LABEL == "IntelligenceProduct"
        assert prod.KEY_FIELD == "product_id"
        assert prod.key_value == prod.product_id


def test_seed_cypher_props_are_neo4j_safe(spine_seed: dict) -> None:
    """``cypher_props`` for every seed node yields only Neo4j-safe values."""
    nodes = (
        [Business.model_validate(spine_seed["business"])]
        + [Domain.model_validate(d) for d in spine_seed["domains"]]
        + [IntelligenceProduct.model_validate(p) for p in spine_seed["products"]]
    )
    for node in nodes:
        _assert_neo4j_safe(node.cypher_props())


# ---------------------------------------------------------------------------
# cypher_props serialization contract
# ---------------------------------------------------------------------------


def test_cypher_props_excludes_none() -> None:
    """Optional fields left at ``None`` never appear in the property map."""
    dom = Domain(
        domain_id="d1",
        name="D1",
        decision_scope_summary="scope",
        min_level=10,
        data_classification="internal",
        status="active",
    )
    props = dom.cypher_props()
    # An untouched optional field must be absent (not present-as-None).
    assert "parent_domain_id" not in props
    assert "owner_role_id" not in props
    assert "created_at" not in props
    assert None not in props.values()


def test_cypher_props_json_encodes_dict_field() -> None:
    """A dict-typed field (``*_json``) is serialized to a JSON *string*."""
    metric = Metric(
        metric_uid="m1",
        canonical_id="c1",
        metric_id="mid1",
        display_name="M1",
        product_ids=["miq"],
        domain_ids=["marketing"],
        scope_key="global",
        metric_base="revenue",
        is_derived=False,
        data_classification="internal",
        min_level=30,
        status="active",
        platform_data_quality_json={"ga4": {"status": "good", "n": 3}},
    )
    props = metric.cypher_props()
    assert isinstance(props["platform_data_quality_json"], str)
    # And it is valid JSON round-tripping back to the original mapping.
    import json

    assert json.loads(props["platform_data_quality_json"]) == {
        "ga4": {"status": "good", "n": 3}
    }
    _assert_neo4j_safe(props)


def test_cypher_props_homogeneous_primitive_lists() -> None:
    """List fields come out as homogeneous primitive lists with no ``None``."""
    metric = Metric(
        metric_uid="m2",
        canonical_id="c2",
        metric_id="mid2",
        display_name="M2",
        product_ids=["miq", "ciq"],
        domain_ids=["marketing", "customer"],
        scope_key="global",
        metric_base="revenue",
        is_derived=False,
        data_classification="internal",
        min_level=30,
        status="active",
        synonyms=["rev", "sales"],
    )
    props = metric.cypher_props()
    assert props["product_ids"] == ["miq", "ciq"]
    assert props["synonyms"] == ["rev", "sales"]
    _assert_neo4j_safe(props)


def test_cypher_props_enum_values_are_strings() -> None:
    """``use_enum_values=True`` means enum fields serialize to plain strings."""
    biz = Business(
        business_id="b1",
        display_name="B1",
        tier="smb",
        status="active",
        business_type="ecommerce",
    )
    props = biz.cypher_props()
    assert props["tier"] == "smb"
    assert props["status"] == "active"
    assert props["business_type"] == "ecommerce"
    assert all(isinstance(v, str) for v in (props["tier"], props["status"]))


# ---------------------------------------------------------------------------
# Collection invariants
# ---------------------------------------------------------------------------


def test_node_labels_has_ten_entries() -> None:
    """There are exactly the 10 V1 node labels, matching the key-field map."""
    assert len(NODE_LABELS) == 10
    assert set(NODE_LABELS) == set(NODE_KEY_FIELDS)
    for label, key_field in NODE_KEY_FIELDS.items():
        assert label in NODE_LABELS
        assert isinstance(key_field, str) and key_field


def test_edge_types_are_nonempty_strings() -> None:
    """Every edge type is a non-empty string and the set is non-empty."""
    assert EDGE_TYPES
    for edge in EDGE_TYPES:
        assert isinstance(edge, str)
        assert edge.strip(), f"empty edge type {edge!r}"
    # Spine edges exercised by the M1 bootstrap must be present.
    assert "HAS_DOMAIN" in EDGE_TYPES
    assert "HAS_PRODUCT" in EDGE_TYPES


# ---------------------------------------------------------------------------
# Proposal payload (section 8)
# ---------------------------------------------------------------------------


def test_proposal_payload_shape() -> None:
    """A :class:`Proposal` carries the section-8 fields with sane defaults."""
    prop = Proposal(
        proposal_id="p1",
        target_label="Metric",
        target_id="m1",
        source_kind="harvester",
    )
    assert prop.operation == "upsert"
    assert prop.review_state == "proposed"
    assert prop.payload == {}
    assert prop.relationship_payloads == []


# ---------------------------------------------------------------------------
# Generalised chart-type UIComponent seed (M2 product decision)
# ---------------------------------------------------------------------------


def test_generalised_component_types_seed_validates() -> None:
    """The 17 generalised chart-type UIComponent seed entries all validate.

    M2 product decision: instead of one UIComponent per chart-registry entry
    (646), we seed a small fixed set of chart-TYPE nodes once at bootstrap — the
    15 ChartType values plus kpi_card / alert_panel. A type node only carries
    component_id (key) + component_kind / chart_type / display_name / status.
    """
    seed_path = REPO_ROOT / "harness" / "seed" / "component_types.json"
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    types = data["component_types"]
    assert len(types) == 17

    components = [UIComponent(**c) for c in types]
    ids = {c.component_id for c in components}
    # All ids are namespaced and unique; the 2 kind-only nodes are present.
    assert len(ids) == 17
    assert all(c.component_id.startswith("uic:") for c in components)
    assert "uic:bar" in ids
    assert "uic:kpi_card" in ids
    assert "uic:alert_panel" in ids
    # The 15 chart-type nodes carry a chart_type; the 2 kind-only nodes do not.
    chart_typed = [c for c in components if c.chart_type is not None]
    assert len(chart_typed) == 15
    # Every seed node yields Neo4j-safe props (key field is the only required).
    for component in components:
        assert component.key_value == component.component_id
        _assert_neo4j_safe(component.cypher_props())


def test_generalised_uicomponent_type_node_validates() -> None:
    """A bare generalised chart-type UIComponent (no per-entry fields) validates."""
    bar = UIComponent(
        component_id="uic:bar",
        component_kind="chart",
        chart_type="bar",
        display_name="Bar Chart",
        status="active",
    )
    assert bar.component_id == "uic:bar"
    assert bar.chart_type == "bar"
    # Per-entry-only fields are optional and default to None / absent.
    assert bar.dashboard_id is None
    assert bar.chart_id is None
    props = bar.cypher_props()
    assert "dashboard_id" not in props
    assert "chart_id" not in props
    _assert_neo4j_safe(props)


def test_metric_accepts_folded_chart_registry_fields() -> None:
    """A Metric accepts the folded-in chart-registry fields (M2 product decision)."""
    metric = Metric(
        metric_uid="metric:d:roas",
        canonical_id="d-roas",
        metric_id="roas",
        display_name="ROAS",
        product_ids=["miq"],
        domain_ids=["marketing"],
        scope_key="d",
        metric_base="roas",
        is_derived=True,
        data_classification="internal",
        min_level=30,
        status="active",
        # Folded-in per-chart registry semantics:
        formula_explanation="Return on ad spend.",
        how_to_read=["a", "b"],
        decisions_answered=["b"],
        narration_text="This shows ROAS.",
        chart_type="bar",
        chart_id="roas",
    )
    assert metric.how_to_read == ["a", "b"]
    assert metric.decisions_answered == ["b"]
    assert metric.chart_type == "bar"
    assert metric.chart_id == "roas"
    props = metric.cypher_props()
    # The new fields appear and are Neo4j-safe (lists of str, plain strings).
    for key in (
        "formula_explanation",
        "how_to_read",
        "decisions_answered",
        "narration_text",
        "chart_type",
        "chart_id",
    ):
        assert key in props, f"{key} missing from cypher_props"
    assert props["how_to_read"] == ["a", "b"]
    assert props["chart_type"] == "bar"
    _assert_neo4j_safe(props)


def test_metric_folded_list_fields_default_empty() -> None:
    """The folded list fields default to empty lists (and are dropped if empty)."""
    metric = Metric(
        metric_uid="metric:d:x",
        canonical_id="d-x",
        metric_id="x",
        display_name="X",
        product_ids=[],
        domain_ids=[],
        scope_key="d",
        metric_base="x",
        is_derived=False,
        data_classification="internal",
        min_level=1,
        status="proposed",
    )
    assert metric.how_to_read == []
    assert metric.decisions_answered == []
    # chart_type / chart_id default to None and are excluded from props.
    props = metric.cypher_props()
    assert "chart_type" not in props
    assert "chart_id" not in props
