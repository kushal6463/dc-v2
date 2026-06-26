"""DB tests for the column-impact + confidence-filtered traversal API.

Exercised through the FastAPI :class:`~fastapi.testclient.TestClient` (like the
other API tests) and auto-skipped via the shared ``graphdb`` fixture, so they do
nothing in environments without a reachable Neo4j. The endpoints read through the
process-wide :func:`~harness.kg.driver.get_db` singleton, which is built from the
same settings as the ``graphdb`` fixture, so metrics/edges seeded on the fixture
are visible to the client. Every test seeds uniquely-suffixed ``metric_uid``s and
``DETACH DELETE``s them in a ``finally`` so reruns stay clean.

Coverage:

* ``GET /api/column-impact`` returns exactly the metrics whose ``source_columns``
  contains the queried column (and surfaces their pinned fields), excluding a
  metric that does not read it.
* ``GET /api/traverse/upstream`` honours ``min_confidence``: a raised floor drops
  a path whose only hop is below it (the low-confidence edge is filtered out).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from harness.api.server import app
from harness.kg.driver import GraphDB

client = TestClient(app)


def test_column_impact_returns_metrics_with_that_source_column(
    graphdb: GraphDB,
) -> None:
    """A metric whose ``source_columns`` contains the column is returned; one
    that does not read it is excluded."""
    # A unique column name so the property-scan returns only our seeded metric.
    column = f"col_test_{uuid.uuid4().hex[:8]}"
    hit_uid = f"metric:test:{uuid.uuid4().hex[:8]}"
    miss_uid = f"metric:test:{uuid.uuid4().hex[:8]}"

    graphdb.write(
        "MERGE (m:Metric {metric_uid: $uid}) "
        "SET m.display_name = $name, m.source_columns = $cols, "
        "m.mart_sources = $marts, m.domain_ids = $domains",
        uid=hit_uid,
        name="Impact Hit",
        cols=["spend", column],
        marts=["MARTS.mart_x"],
        domains=["marketing"],
    )
    graphdb.write(
        "MERGE (m:Metric {metric_uid: $uid}) SET m.source_columns = $cols",
        uid=miss_uid,
        cols=["revenue"],
    )

    try:
        resp = client.get("/api/column-impact", params={"column": column})
        assert resp.status_code == 200
        body = resp.json()

        assert body["column"] == column
        uids = {m["metric_uid"] for m in body["metrics"]}
        # The metric whose source_columns contains the column is present...
        assert hit_uid in uids
        # ...and a metric without that column is not.
        assert miss_uid not in uids
        # The column is unique, so it matches exactly the one seeded metric.
        assert body["count"] == 1

        row = next(m for m in body["metrics"] if m["metric_uid"] == hit_uid)
        assert row["display_name"] == "Impact Hit"
        assert row["mart_sources"] == ["MARTS.mart_x"]
        assert row["domain_ids"] == ["marketing"]
    finally:
        graphdb.write(
            "MATCH (m:Metric) WHERE m.metric_uid IN $uids DETACH DELETE m",
            uids=[hit_uid, miss_uid],
        )


def test_min_confidence_filters_low_confidence_traversal_edge(
    graphdb: GraphDB,
) -> None:
    """``min_confidence`` drops a path whose only hop is below the floor."""
    suffix = uuid.uuid4().hex[:8]
    cause_uid = f"metric:test:cause:{suffix}"
    effect_uid = f"metric:test:effect:{suffix}"

    graphdb.write("MERGE (m:Metric {metric_uid: $uid})", uid=cause_uid)
    graphdb.write("MERGE (m:Metric {metric_uid: $uid})", uid=effect_uid)
    # cause -[INFLUENCES, confidence 0.3]-> effect (so cause is upstream of effect).
    graphdb.write(
        "MATCH (a:Metric {metric_uid: $a}), (b:Metric {metric_uid: $b}) "
        "MERGE (a)-[r:INFLUENCES]->(b) "
        "SET r.relation = 'statistical', r.confidence = 0.3, "
        "r.status = 'active', r.temporal_lag = 'P1D'",
        a=cause_uid,
        b=effect_uid,
    )

    try:
        # No floor: the single 0.3-confidence path is present.
        low = client.get(
            "/api/traverse/upstream",
            params={"metric_uid": effect_uid, "min_confidence": 0.0},
        )
        assert low.status_code == 200
        low_body = low.json()
        assert low_body["summary"]["acyclic_count"] == 1
        assert any(cause_uid in path["nodes"] for path in low_body["paths"])

        # Raise the floor above the edge's confidence: the path is filtered out.
        high = client.get(
            "/api/traverse/upstream",
            params={"metric_uid": effect_uid, "min_confidence": 0.5},
        )
        assert high.status_code == 200
        high_body = high.json()
        assert high_body["summary"]["acyclic_count"] == 0
        assert all(cause_uid not in path["nodes"] for path in high_body["paths"])
    finally:
        graphdb.write(
            "MATCH (m:Metric) WHERE m.metric_uid IN $uids DETACH DELETE m",
            uids=[cause_uid, effect_uid],
        )
