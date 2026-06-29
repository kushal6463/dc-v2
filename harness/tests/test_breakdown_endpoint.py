"""NO-DB tests for the active-campaign-breakdown endpoint (runtime overlay).

``GET /api/active-campaign-breakdown`` is a thin, read-only passthrough to
:func:`harness.marts.snowflake_reader.fetch_active_campaign_breakdown` — it walks
no graph edges and needs no Neo4j, so these tests run without a database (like the
other API tests, the module-level ``TestClient`` does not enter the app's
lifespan, so no DB connection is opened). The reader pulls an OPTIONAL Snowflake
connector and real creds, so we never call the real thing: a stub module is
installed before the server import (so the top-level
``from harness.marts.snowflake_reader import fetch_active_campaign_breakdown``
always resolves, even if the connector/creds are absent) and each test
monkeypatches the name the endpoint actually calls.

Coverage:

* the endpoint forwards the HTTP ``metric_uid`` query param to the reader's
  ``anchor_metric_uid`` kwarg (plus ``date_from`` / ``date_to``) and returns the
  reader payload verbatim (the full contract shape passes through untouched);
* the graceful-degradation contract: when the reader reports ``stale=True`` with
  empty counts (Snowflake unconfigured / unreachable) the endpoint still returns
  HTTP 200 with that payload — it never 5xx's on a warehouse outage;
* the typed query args are required (422 when missing), matching the other
  typed-arg GET routes.
"""

from __future__ import annotations

import sys
import types
from typing import Any

# The Snowflake reader is a sibling module whose connector/creds may be absent in
# CI. Install a stub BEFORE importing the server so the top-level
# ``from harness.marts.snowflake_reader import fetch_active_campaign_breakdown``
# resolves no matter what; every test then patches the bound name on the server
# module, so the real connector is never touched.
if "harness.marts.snowflake_reader" not in sys.modules:
    try:  # pragma: no cover - prefer the real module when it imports cleanly
        import harness.marts.snowflake_reader  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure -> stub it
        _stub = types.ModuleType("harness.marts.snowflake_reader")

        def _fetch_stub(
            anchor_metric_uid: str, date_from: str, date_to: str, **_: Any
        ) -> dict[str, Any]:
            return {
                "anchor_metric_uid": anchor_metric_uid,
                "date_from": date_from,
                "date_to": date_to,
                "counts_by_metric_uid": {},
                "overlay_dims": {"ad_network_type": {}, "objective": {}},
                "zero_count_metric_uids": [],
                "stale": True,
                "freshness_notes": "snowflake_reader stub (test)",
                "source_marts": [],
            }

        _stub.fetch_active_campaign_breakdown = _fetch_stub  # type: ignore[attr-defined]
        sys.modules["harness.marts.snowflake_reader"] = _stub

from fastapi.testclient import TestClient  # noqa: E402

from harness.api import server  # noqa: E402

client = TestClient(server.app)


def test_breakdown_forwards_params_and_returns_reader_payload(monkeypatch) -> None:
    """The endpoint forwards ``metric_uid`` -> ``anchor_metric_uid`` (+ the dates)
    and returns the reader's payload verbatim."""
    captured: dict[str, Any] = {}

    def fake_fetch(
        *, anchor_metric_uid: str, date_from: str, date_to: str
    ) -> dict[str, Any]:
        captured.update(
            anchor_metric_uid=anchor_metric_uid,
            date_from=date_from,
            date_to=date_to,
        )
        return {
            "anchor_metric_uid": anchor_metric_uid,
            "date_from": date_from,
            "date_to": date_to,
            "counts_by_metric_uid": {
                "blended.active_campaigns": 12,
                "google_ads.active_campaigns": 7,
                "meta_ads.active_campaigns": 5,
            },
            "overlay_dims": {
                "ad_network_type": {"SEARCH": 4, "SHOPPING": 3},
                "objective": {"OUTCOME_SALES": 5},
            },
            "zero_count_metric_uids": ["klaviyo.active_campaigns"],
            "stale": False,
            "freshness_notes": "fresh through 2026-06-29",
            "source_marts": ["MART_GOOGLE_ADS", "MART_META_ADS"],
        }

    monkeypatch.setattr(server, "fetch_active_campaign_breakdown", fake_fetch)

    resp = client.get(
        "/api/active-campaign-breakdown",
        params={
            "metric_uid": "blended.active_campaigns",
            "date_from": "2026-06-01",
            "date_to": "2026-06-29",
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    # The HTTP param is metric_uid (shared with every other endpoint); the reader
    # receives it as anchor_metric_uid, with the dates passed straight through.
    assert captured == {
        "anchor_metric_uid": "blended.active_campaigns",
        "date_from": "2026-06-01",
        "date_to": "2026-06-29",
    }
    # The whole reader contract passes through untouched — counts/zero buckets keyed
    # by dot-form metric_uid (== graph node id), plus the non-additive dims.
    assert body["anchor_metric_uid"] == "blended.active_campaigns"
    assert body["counts_by_metric_uid"]["google_ads.active_campaigns"] == 7
    assert body["overlay_dims"]["ad_network_type"]["SEARCH"] == 4
    assert body["overlay_dims"]["objective"] == {"OUTCOME_SALES": 5}
    assert body["zero_count_metric_uids"] == ["klaviyo.active_campaigns"]
    assert body["stale"] is False
    assert body["source_marts"] == ["MART_GOOGLE_ADS", "MART_META_ADS"]


def test_breakdown_graceful_stale_when_warehouse_unavailable(monkeypatch) -> None:
    """When the reader degrades (``stale=True``, empty counts) the endpoint still
    returns HTTP 200 with that payload — it never 5xx's on a warehouse outage."""

    def stale_fetch(
        *, anchor_metric_uid: str, date_from: str, date_to: str
    ) -> dict[str, Any]:
        return {
            "anchor_metric_uid": anchor_metric_uid,
            "date_from": date_from,
            "date_to": date_to,
            "counts_by_metric_uid": {},
            "overlay_dims": {"ad_network_type": {}, "objective": {}},
            "zero_count_metric_uids": [],
            "stale": True,
            "freshness_notes": "Snowflake not configured; overlay unavailable.",
            "source_marts": [],
        }

    monkeypatch.setattr(server, "fetch_active_campaign_breakdown", stale_fetch)

    resp = client.get(
        "/api/active-campaign-breakdown",
        params={
            "metric_uid": "google_ads.active_campaigns",
            "date_from": "2026-01-01",
            "date_to": "2026-01-31",
        },
    )
    # Graceful: a warehouse outage is a stale=True payload, NOT a 5xx.
    assert resp.status_code == 200
    body = resp.json()
    assert body["stale"] is True
    assert body["counts_by_metric_uid"] == {}
    assert body["zero_count_metric_uids"] == []
    assert body["overlay_dims"] == {"ad_network_type": {}, "objective": {}}


def test_breakdown_requires_query_params() -> None:
    """metric_uid / date_from / date_to are required typed query args (422 when
    omitted) — matching the other typed-arg GET routes; no reader call is made."""
    resp = client.get("/api/active-campaign-breakdown")
    assert resp.status_code == 422
