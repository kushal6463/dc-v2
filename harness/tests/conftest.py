"""Shared pytest fixtures for the ThoughtWire Causal Knowledge Graph suite.

The DB-dependent fixtures auto-skip when Neo4j is not configured or reachable so
the whole suite passes without a database (``NEO4J_PASSWORD`` is empty by
default). The NO-DB tests never touch these fixtures.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from harness.kg.config import REPO_ROOT, get_settings
from harness.kg.driver import GraphDB

#: Absolute path to the spine seed file (used by NO-DB and DB tests).
SPINE_SEED_PATH: Path = REPO_ROOT / "harness" / "seed" / "spine_seed.json"


@pytest.fixture(scope="session")
def spine_seed() -> dict:
    """Return the parsed ``spine_seed.json`` payload."""
    return json.loads(SPINE_SEED_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def metric_payload() -> dict:
    """Return a minimal valid :class:`~harness.kg.models.Metric` payload dict.

    Carries exactly the Metric fields with no default (the required ones) so a
    NO-DB test can build a :class:`~harness.kg.models.Metric` and then layer the
    new optional fields (``node_kind``/``has_endpoint``/``ml_*``/``source_expr``/
    ``bc2_ref`` …) on top of it. A fresh dict per test (function scope) so a test
    may mutate it freely.
    """
    return {
        "metric_uid": "metric:blended:roas",
        "canonical_id": "blended-roas",
        "metric_id": "roas",
        "display_name": "Blended ROAS",
        "product_ids": ["miq"],
        "domain_ids": ["marketing"],
        "scope_key": "blended",
        "metric_base": "roas",
        "is_derived": True,
        "data_classification": "internal",
        "min_level": 30,
        "status": "active",
    }


@pytest.fixture
def graphdb() -> Iterator[GraphDB]:
    """Yield a verified :class:`GraphDB`, or skip if no DB is available.

    The test (and its module) is skipped cleanly when ``NEO4J_PASSWORD`` is
    empty or when connectivity verification fails, so DB-dependent tests do not
    fail in environments without a running Neo4j.
    """
    settings = get_settings()
    if not settings.neo4j_password:
        pytest.skip("NEO4J_PASSWORD is empty; skipping DB-dependent test.")

    db = GraphDB.from_settings(settings)
    try:
        db.verify()
    except Exception as exc:  # noqa: BLE001 — any connectivity failure -> skip
        db.close()
        pytest.skip(f"Neo4j not reachable; skipping DB-dependent test ({exc}).")

    try:
        yield db
    finally:
        db.close()
