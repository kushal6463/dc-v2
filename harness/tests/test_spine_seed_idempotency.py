"""Tests for the idempotent tri-axis spine seeder (``harness.ingest.spine_seed``).

The NO-DB tests round-trip the LOCAL seed files through the Pydantic node models
and :meth:`~harness.kg.models.GraphNode.cypher_props` (no Neo4j): every entry
validates, the branding edits landed (the ``storefront_iq`` product exists and
the ``magento`` platform's display name is ``StoreFront IQ`` while its
``platform_id`` stays ``magento`` for mart/SQL lineage), and ``--dry-run`` builds
+ validates without touching the DB.

An optional DB test (``graphdb`` fixture, auto-skipped when ``NEO4J_PASSWORD`` is
empty) asserts the seeder is idempotent: a second run reports every node as
``updated`` and the per-label node counts are unchanged (``MERGE`` on identity).
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout

import pytest

from harness.ingest import spine_seed
from harness.kg.driver import GraphDB
from harness.kg.models import (
    Business,
    Domain,
    IntelligenceProduct,
    Platform,
)

# Primitive types that may appear directly as a Neo4j property value.
_PRIMITIVES = (str, int, float, bool)


# ---------------------------------------------------------------------------
# NO-DB: seed files round-trip through the models
# ---------------------------------------------------------------------------


def test_build_models_returns_full_spine() -> None:
    """``build_models`` builds the whole spine in upsert order (Business first)."""
    built = spine_seed.build_models()
    counts: dict[str, int] = {}
    for model in built:
        counts[model.LABEL] = counts.get(model.LABEL, 0) + 1
    assert built[0].LABEL == "Business"
    assert counts == {
        "Business": 1,
        "Domain": 9,
        "IntelligenceProduct": 6,
        "Platform": 12,  # 5 top-level + 7 sub-platforms (google/meta sub-channels)
    }
    # Every node validated to its declared model type.
    by_label = {
        "Business": Business,
        "Domain": Domain,
        "IntelligenceProduct": IntelligenceProduct,
        "Platform": Platform,
    }
    for model in built:
        assert isinstance(model, by_label[model.LABEL])


def test_seed_models_cypher_props_neo4j_safe() -> None:
    """Every seed node's ``cypher_props`` yields only Neo4j-safe values."""
    for model in spine_seed.build_models():
        props = model.cypher_props()
        for key, value in props.items():
            assert value is not None, f"{key!r} is None (should be excluded)"
            if isinstance(value, list):
                for item in value:
                    assert isinstance(item, _PRIMITIVES), (
                        f"{key!r} list element {item!r} is not a primitive"
                    )
            else:
                assert isinstance(value, _PRIMITIVES), (
                    f"{key!r} value {value!r} is not a primitive/list"
                )


def test_storefront_iq_product_present() -> None:
    """The seed carries the new ``storefront_iq`` IntelligenceProduct."""
    products = {
        m.key_value: m
        for m in spine_seed.build_models()
        if m.LABEL == "IntelligenceProduct"
    }
    assert "storefront_iq" in products
    sfi = products["storefront_iq"]
    assert sfi.display_name == "StoreFront IQ"
    assert sfi.category == "analytics"
    # Still also has the other analytics products (it is an addition).
    for pid in ("miq", "ciq", "piq"):
        assert pid in products


def test_magento_platform_renamed_keeps_id() -> None:
    """The ``magento`` platform displays as ``StoreFront IQ`` but keeps its id."""
    platforms = {
        m.key_value: m for m in spine_seed.build_models() if m.LABEL == "Platform"
    }
    assert "magento" in platforms, "platform_id must remain 'magento' for lineage"
    magento = platforms["magento"]
    assert magento.platform_id == "magento"
    assert magento.platform_name == "StoreFront IQ"
    # The display rename survives the Neo4j serialization unchanged.
    props = magento.cypher_props()
    assert props["platform_id"] == "magento"
    assert props["platform_name"] == "StoreFront IQ"


def test_dry_run_builds_without_db() -> None:
    """``seed_spine(dry_run=True)`` validates + prints every node, no DB write."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        results = spine_seed.seed_spine(dry_run=True)
    assert len(results) == 28  # 1 Business + 9 Domain + 6 Product + 12 Platform
    assert {r["status"] for r in results} == {"dry_run"}
    assert all({"label", "key"} <= set(r) for r in results)
    # The dry-run banner prints the count (and no DB was acquired).
    assert "28 spine nodes" in buf.getvalue()


# ---------------------------------------------------------------------------
# DB (optional): a second seed run is idempotent (auto-skips without Neo4j)
# ---------------------------------------------------------------------------


def _spine_node_count(db: GraphDB) -> int:
    """Return the total count of the four spine labels."""
    rows = db.read(
        "MATCH (n) WHERE n:Business OR n:Domain OR n:IntelligenceProduct "
        "OR n:Platform RETURN count(n) AS c"
    )
    return int(rows[0]["c"]) if rows else 0


@pytest.mark.skipif(
    os.environ.get("KG_RUN_SEED_DB_TEST") != "1",
    reason=(
        "spine-seed DB test WRITES the real spine into Neo4j; opt in explicitly "
        "with KG_RUN_SEED_DB_TEST=1 (default-skipped so a normal run never "
        "mutates the graph)."
    ),
)
def test_second_seed_is_idempotent(graphdb: GraphDB) -> None:
    """A second seed reports every node ``updated`` with a stable node count.

    Seeds once (each node ``created`` or ``updated`` depending on prior state),
    captures the spine node count, then seeds again: the second run must report
    every node as ``updated`` (the identity already exists) and leave the spine
    node count unchanged — the ``MERGE``-on-identity idempotency guarantee.

    Unlike the throwaway-node DB tests, this WRITES the real rare_seeds spine, so
    it is opt-in (``KG_RUN_SEED_DB_TEST=1``) on top of the ``graphdb`` auto-skip.
    """
    spine_seed.seed_spine(dry_run=False)
    count_after_first = _spine_node_count(graphdb)

    second = spine_seed.seed_spine(dry_run=False)
    assert {r["status"] for r in second} == {"updated"}
    assert _spine_node_count(graphdb) == count_after_first
