"""Deterministic pre-pass for the metric / UIComponent ingestion engine.

This module is the **zero-LLM** first stage of Milestone 2 (implementation plan
section 5a). It loads the two ground-truth source files
(``docs/frd-docs/chart-registry.json`` and ``docs/frd-docs/openapi.json``) and
derives, per dashboard, deterministic *draft* dicts for the two surface labels
the proposer later enriches and proposes:

* :class:`~harness.kg.models.Dashboard` — one per distinct ``dashboard_id``.
* :class:`~harness.kg.models.Metric` — one per chart-registry entry; the agent
  later groups these by ``concept_key`` and wires ``ROLLS_UP_TO`` edges.

**M2 product decision (approved deviation from schema §4).** We no longer emit
one ``UIComponent`` draft per chart-registry entry (646, 1:1 with Metric — too
repetitive). Instead a small fixed set of *generalised* chart-TYPE UIComponent
nodes is seeded once at bootstrap (``harness/seed/component_types.json``), and
the per-chart registry semantics (``formula``/``formula_explanation``/
``how_to_read``/``decisions_answered``/``narration_text``/``chart_id``) are
**folded onto the Metric draft**. The agent classifies each metric's
``chart_type`` and emits a ``VISUALIZES`` edge from the matching
``uic:<chart_type>`` type node to the metric. The per-dashboard bucket keeps an
empty ``"components"`` list for backward-compat with callers that read the key.

The chart registry is the **authoritative** source of concrete ids + semantics.
OpenAPI only *enriches* endpoint paths where a dashboard actually exposes them;
the OpenAPI metric-by-id response carries runtime values only (no semantics), so
it is never used as a semantic source. Endpoint harvesting honours
:func:`is_excluded` (master-config/master, non-GET, auth/settings/health/docs/
redoc, non-dashboard admin).

Every draft dict uses only field names that exist on the corresponding Pydantic
model in :mod:`harness.kg.models`; unknown keys are never emitted, so each draft
validates with ``Model(**draft)``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from harness.kg.config import REPO_ROOT

#: Directory holding the ground-truth source files.
FRD_DOCS_DIR = REPO_ROOT / "docs" / "frd-docs"
#: The authoritative chart registry (646 entries, 83 dashboards).
REGISTRY_PATH = FRD_DOCS_DIR / "chart-registry.json"
#: The OpenAPI spec used solely to enrich endpoint paths.
OPENAPI_PATH = FRD_DOCS_DIR / "openapi.json"

#: Default data classification for engine-drafted surface nodes (agent revises).
DEFAULT_DATA_CLASSIFICATION = "internal"
#: Default minimum seniority level for engine-drafted nodes (agent revises).
DEFAULT_MIN_LEVEL = 1
#: Default IntelligenceProduct a dashboard belongs to until the agent revises it.
DEFAULT_PRODUCT_ID = "miq"
#: Source-registry tag stamped on every drafted Dashboard.
SOURCE_REGISTRY = "chart-registry"

# Endpoint segments that, when a dashboard exposes the *templated* by-id form,
# yield a deterministic ``card_endpoint`` / ``series_endpoint`` pattern.
_TEMPLATED_METRIC_RE = re.compile(r"^/api/v1/([^/]+)/metrics/\{")
_TEMPLATED_CHART_RE = re.compile(r"^/api/v1/([^/]+)/charts/\{")
_DEFAULT_PATH_RE = re.compile(r"^/api/v1/([^/]+)/$")
_METADATA_PATH_RE = re.compile(r"^/api/v1/([^/]+)/metadata/?$")


def load_sources() -> tuple[dict, dict]:
    """Parse and return ``(openapi, registry)`` from ``docs/frd-docs/``.

    Returns:
        A two-tuple of the parsed OpenAPI spec dict and the parsed chart-registry
        dict (keyed ``"{dashboard_id}:{chart_id}"``).
    """
    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return openapi, registry


def is_excluded(path: str, method: str = "get") -> bool:
    """Return ``True`` if an OpenAPI path/method must never become a node/edge.

    Exclusion rules (implementation-plan facts):

    * Any non-GET method (POST/PUT/PATCH/DELETE).
    * Any path containing ``master-config`` or a ``/master/`` segment.
    * The ``/auth``, ``/settings``, ``/health``, ``/docs``, ``/redoc`` surfaces.
    * Non-dashboard ``/admin`` (an ``/admin`` path that is not under
      ``/api/v1/`` — dashboard endpoints live under ``/api/v1/{dash}/``).

    Args:
        path: The OpenAPI path template (e.g. ``/api/v1/ceo-pulse/metrics/``).
        method: The HTTP method; anything other than GET is excluded.

    Returns:
        Whether the endpoint is excluded from harvesting.
    """
    if method.lower() != "get":
        return True
    low = path.lower()
    if "master-config" in low or "/master/" in low:
        return True
    if any(seg in low for seg in ("/auth", "/settings", "/health", "/docs", "/redoc")):
        return True
    if "/admin" in low and "/api/v1/" not in low:
        return True
    return False


def _included_get_paths(openapi: dict) -> dict[str, dict]:
    """Return the GET paths of ``openapi`` with all excluded paths removed."""
    paths: dict[str, dict] = openapi.get("paths", {}) or {}
    return {
        path: methods
        for path, methods in paths.items()
        if "get" in {m.lower() for m in methods} and not is_excluded(path, "get")
    }


def _endpoint_index(openapi: dict) -> dict[str, dict[str, Any]]:
    """Build a per-dashboard index of the endpoint patterns OpenAPI exposes.

    Only the *shapes* relevant to drafting are captured (deterministic, no
    fuzzy id matching): whether a dashboard exposes templated metric/chart
    by-id endpoints, plus its default and metadata paths. Concrete per-metric
    segments are intentionally *not* matched against registry ``chart_id`` values
    because those id spaces do not align one-to-one.

    Returns:
        Map ``dashboard_id -> {"metric_template", "chart_template",
        "default_path", "metadata_path"}`` (each value a path str or ``None``).
    """
    index: dict[str, dict[str, Any]] = {}

    def slot(dash: str) -> dict[str, Any]:
        return index.setdefault(
            dash,
            {
                "metric_template": None,
                "chart_template": None,
                "default_path": None,
                "metadata_path": None,
            },
        )

    for path in _included_get_paths(openapi):
        if (m := _TEMPLATED_METRIC_RE.match(path)) is not None:
            slot(m.group(1))["metric_template"] = path
        elif (m := _TEMPLATED_CHART_RE.match(path)) is not None:
            slot(m.group(1))["chart_template"] = path
        elif (m := _DEFAULT_PATH_RE.match(path)) is not None:
            slot(m.group(1))["default_path"] = path
        elif (m := _METADATA_PATH_RE.match(path)) is not None:
            slot(m.group(1))["metadata_path"] = path

    return index


def _title_case(dashboard_id: str) -> str:
    """Render a slug ``dashboard_id`` as a human title (``ceo-pulse`` -> ``Ceo Pulse``)."""
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", dashboard_id) if part)


def _as_list(value: Any) -> list[str]:
    """Coerce a registry value into a clean ``list[str]`` (drops empties/None)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _opt_str(value: Any) -> str | None:
    """Return a non-empty string or ``None`` (registry uses ``""`` for absent)."""
    if value in (None, ""):
        return None
    return str(value)


def _metric_draft(entry: dict, endpoints: dict[str, Any] | None) -> dict[str, Any]:
    """Build a deterministic Metric draft dict for one registry entry.

    ``concept_key``/``metric_base`` are seeded from the raw ``chart_id`` and are
    reconciled by the agent; endpoint fields are populated only when the
    dashboard exposes the corresponding templated OpenAPI endpoint.
    """
    dashboard_id = str(entry["dashboard_id"])
    chart_id = str(entry["chart_id"])
    formula = _opt_str(entry.get("formula"))

    card_endpoint: str | None = None
    series_endpoint: str | None = None
    if endpoints is not None:
        card_endpoint = endpoints.get("metric_template")
        series_endpoint = endpoints.get("chart_template")
    endpoint_paths = [p for p in (card_endpoint, series_endpoint) if p]

    draft: dict[str, Any] = {
        "metric_uid": f"metric:{dashboard_id}:{chart_id}",
        # Metric.canonical_id intentionally joins with a dash ("{dash}-{chart_id}")
        # — unlike the colon used by the registry canonical_id — because Metric
        # occupies a distinct concept space that the agent later reconciles
        # (grouping drafts by concept_key/metric_base), so its canonical id must
        # not collide with the registry id space.
        "canonical_id": f"{dashboard_id}-{chart_id}",
        "metric_id": chart_id,
        "display_name": str(entry["title"]),
        "concept_key": chart_id,
        "scope_key": dashboard_id,
        # ``metric_base`` is required on the model; seed it from the raw concept
        # so the draft validates (the agent normalizes it during proposal).
        "metric_base": chart_id,
        "formula_text": formula,
        "formula_status": "parsed" if formula else "unknown",
        # The agent fills these from the spine; prepass leaves them empty.
        "domain_ids": [],
        "product_ids": [],
        # ``is_derived`` is required; at prepass we make no causal claim (the
        # agent decides from the formula), so default to the conservative False.
        "is_derived": False,
        "card_endpoint": card_endpoint,
        "series_endpoint": series_endpoint,
        "endpoint_paths": endpoint_paths,
        "data_classification": DEFAULT_DATA_CLASSIFICATION,
        "min_level": DEFAULT_MIN_LEVEL,
        "status": "proposed",
        # --- Folded-in chart-registry semantics (M2 product decision) ---------
        # Per-chart registry specifics now live on the Metric instead of on a
        # per-entry UIComponent. ``chart_id`` is the raw registry chart id;
        # ``chart_type`` is left for the agent to classify (15 ChartType values).
        "chart_id": chart_id,
        "formula_explanation": _opt_str(entry.get("formula_explanation")),
        "how_to_read": _as_list(entry.get("how_to_read")),
        "decisions_answered": _as_list(entry.get("decisions_answered")),
    }
    if (narration := _opt_str(entry.get("narration_text"))) is not None:
        draft["narration_text"] = narration
    return draft


def _dashboard_draft(dashboard_id: str, endpoints: dict[str, Any] | None) -> dict[str, Any]:
    """Build a deterministic Dashboard draft dict for one dashboard id."""
    draft: dict[str, Any] = {
        "dashboard_id": dashboard_id,
        "display_name": _title_case(dashboard_id),
        "product_id": DEFAULT_PRODUCT_ID,
        "source_registry": SOURCE_REGISTRY,
        "data_classification": DEFAULT_DATA_CLASSIFICATION,
        "min_level": DEFAULT_MIN_LEVEL,
        "status": "proposed",
    }
    if endpoints is not None:
        if (default_path := endpoints.get("default_path")) is not None:
            draft["default_endpoint_path"] = default_path
        if (metadata_path := endpoints.get("metadata_path")) is not None:
            draft["metadata_endpoint_path"] = metadata_path
    return draft


@lru_cache(maxsize=1)
def _build() -> dict[str, Any]:
    """Build (and cache) the full prepass result from the source files."""
    openapi, registry = load_sources()
    endpoint_index = _endpoint_index(openapi)

    dashboards: dict[str, dict[str, Any]] = {}
    metric_count = 0

    # Iterate the registry in a stable key order so output is deterministic.
    for key in sorted(registry):
        entry = registry[key]
        dashboard_id = str(entry["dashboard_id"])
        endpoints = endpoint_index.get(dashboard_id)

        bucket = dashboards.get(dashboard_id)
        if bucket is None:
            bucket = {
                "dashboard": _dashboard_draft(dashboard_id, endpoints),
                # M2 product decision: no per-entry UIComponent drafts. The
                # per-chart registry semantics are folded onto each Metric, and
                # metrics link to the 17 generalised chart-TYPE UIComponent nodes
                # (seeded at bootstrap) via VISUALIZES. ``components`` is kept as
                # an empty list for backward-compat with callers reading the key.
                "components": [],
                "metrics": [],
            }
            dashboards[dashboard_id] = bucket

        metric = _metric_draft(entry, endpoints)
        bucket["metrics"].append(metric)
        metric_count += 1

    excluded_endpoints = _count_excluded_endpoints(openapi)

    return {
        "dashboards": dashboards,
        "counts": {
            "dashboards": len(dashboards),
            # Per-entry UIComponent drafts are no longer emitted (M2 product
            # decision): chart types are seeded as 17 generalised UIComponent
            # nodes at bootstrap, not one per registry entry. The count is 0.
            "components": 0,
            "metrics": metric_count,
            "excluded_endpoints": excluded_endpoints,
        },
    }


def _count_excluded_endpoints(openapi: dict) -> int:
    """Count distinct (path, method) pairs excluded by :func:`is_excluded`."""
    paths: dict[str, dict] = openapi.get("paths", {}) or {}
    excluded = 0
    for path, methods in paths.items():
        for method in methods:
            if is_excluded(path, method):
                excluded += 1
    return excluded


def run_prepass() -> dict:
    """Run the full deterministic pre-pass over all dashboards.

    Returns:
        A dict with two keys:

        * ``"dashboards"`` — ``{dashboard_id: {"dashboard": <Dashboard draft>,
          "components": [] (always empty — chart types are generalised type nodes
          seeded at bootstrap, not per-entry), "metrics": [<Metric draft>...]}}``.
        * ``"counts"`` — ``{"dashboards", "components" (0 — per-entry components
          are no longer drafted), "metrics", "excluded_endpoints"}`` integer
          totals.
    """
    return _build()


def prepass_for(dashboard_id: str) -> dict:
    """Return the single-dashboard slice of :func:`run_prepass`.

    Args:
        dashboard_id: The dashboard slug to slice.

    Returns:
        The ``{"dashboard", "components", "metrics"}`` bucket for the dashboard.

    Raises:
        KeyError: If the dashboard id is not present in the chart registry.
    """
    dashboards = run_prepass()["dashboards"]
    if dashboard_id not in dashboards:
        raise KeyError(f"unknown dashboard_id: {dashboard_id!r}")
    return dashboards[dashboard_id]


def all_dashboard_ids() -> list[str]:
    """Return every distinct dashboard id, in stable sorted order."""
    return sorted(run_prepass()["dashboards"])
