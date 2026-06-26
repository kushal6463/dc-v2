"""Deterministic pre-pass for ingesting Dashboard surfaces over LIVE metrics.

Unlike :mod:`harness.ingest.prepass` (which drafts Metric/Dashboard nodes for a
from-scratch build and keys metrics as ``metric:{dashboard}:{chart}``), this
module is purpose-built to add :class:`~harness.kg.models.Dashboard` nodes to a
graph whose **317 Metric nodes already exist** — built by the agentic builder and
keyed by their catalog identity (``metric_uid = source.concept``, e.g.
``blended.active_ads``). It never invents metric ids; every ``SHOWN_ON`` edge it
plans targets one of the live 317.

Two in-repo ground-truth sources are merged (no ``../BC_2`` seed data), and each
candidate ``(dashboard, metric)`` link is tagged with the ROLE that produced it —
because not every referenced metric is genuinely *displayed* on a dashboard:

* ``chart_metric`` — the metric a chart on this dashboard actually plots
  (chart-registry ``metric_key``). Genuinely shown.
* ``membership`` — the metric's own ``dashboards: [...]`` array in the catalog
  declares it belongs to this dashboard. Genuinely shown.
* ``dependency`` — the metric only appears as a chart's ``depends_on`` input
  (a computation feeder, e.g. ``google_ads.spend`` behind a blended ROAS). These
  are **intermediate** metrics that should usually NOT be mapped onto the
  surface; an LLM adjudicates them per dashboard.

The deterministic **floor** (``chart_metric`` ∪ ``membership``) is always shown
(420 edges, full 317-metric coverage). The ``dependency``-only candidates (the
suspect intermediates) are handed to the proposer's LLM subagent, which decides
which — if any — are genuinely displayed rather than mere inputs. A metric shown
on several dashboards yields several edges; a dashboard spanning several domains
carries the full ``domain_ids`` list — both multiplicities are preserved.

This stage is pure (no Neo4j, no LLM).
"""

from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from typing import Any

from harness.ingest.prepass import _title_case
from harness.kg.config import REPO_ROOT

#: The catalog the live 317 Metric nodes were built from (source of metric_uids
#: + each metric's own ``dashboards`` membership, product, domain, and flags).
CATALOG_PATH = REPO_ROOT / "data" / "metric_nodes.rare_seeds.json"
#: The canonical chart registry (chart ``metric_key``/``depends_on`` → metrics).
REGISTRY_PATH = REPO_ROOT / "docs" / "frd-docs" / "chart-registry.json"

#: Maps the catalog ``product`` label to the spine ``IntelligenceProduct.product_id``.
PRODUCT_TO_ID: dict[str, str] = {
    "MarketingIQ": "miq",
    "CustomerIQ": "ciq",
    "ProductIQ": "piq",
    "StoreFrontIQ": "storefront_iq",
}
#: Product id used for a dashboard that shows no live metric (the few registry
#: surfaces whose metrics are all operational/non-live).
DEFAULT_PRODUCT_ID = "dc"
#: Slugs treated as executive-tier dashboards (drives the ``dashboard_type`` hint).
EXECUTIVE_SLUGS: frozenset[str] = frozenset(
    {"ceo-pulse", "weekly-exec", "monthly-review", "quarterly-review", "annual-planning"}
)

#: Default data classification / seniority / status for a drafted Dashboard
#: (the LLM proposer may revise classification + type).
DEFAULT_DATA_CLASSIFICATION = "internal"
DEFAULT_MIN_LEVEL = 1
DEFAULT_STATUS = "active"
#: Provenance tag stamped on every drafted Dashboard.
SOURCE_REGISTRY = "chart-registry+metric-catalog"


def load_live_metrics() -> dict[str, dict[str, Any]]:
    """Return ``{metric_uid: {...}}`` for the live 317 metrics.

    The live identity is the catalog's top-level key (``source.concept``); only
    ``node_type == "metric"`` entries are live, so the 8 ``operational.*`` metrics
    are dropped here and can never become a ``SHOWN_ON`` edge target. Carries the
    fields the proposer needs to judge whether a metric is genuinely displayed.
    """
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["metrics"]
    live: dict[str, dict[str, Any]] = {}
    for uid, entry in catalog.items():
        if entry.get("node_type") != "metric":
            continue
        live[uid] = {
            "title": entry.get("title") or uid,
            "product": entry.get("product"),
            "domain": entry.get("domain"),
            "is_kpi": bool(entry.get("is_kpi")),
            "is_derived": bool(entry.get("is_derived")),
            # The metric's own dashboard membership (the ``membership`` role).
            "dashboards": [str(d) for d in (entry.get("dashboards") or []) if d],
        }
    return live


def _dashboard_type(slug: str) -> str:
    """Infer the ``dashboard_type`` enum value from the slug (LLM may revise)."""
    if slug.startswith("ml-"):
        return "ml"
    if slug in EXECUTIVE_SLUGS:
        return "executive"
    return "operational"


def _build_roles(
    live: dict[str, dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Merge both sources into per-dashboard ``floor`` and ``dependency`` sets.

    Returns:
        ``(floor, dependency, all_dashboard_ids)`` where ``floor[slug]`` is the
        genuinely-shown metrics (chart ``metric_key`` ∪ catalog membership) and
        ``dependency[slug]`` is the ``depends_on``-only intermediates NOT already
        in the floor (the suspects the LLM adjudicates).
    """
    chart_metric: dict[str, set[str]] = {}
    membership: dict[str, set[str]] = {}
    depends: dict[str, set[str]] = {}
    all_dashboards: set[str] = set()

    # Catalog membership — each metric's own ``dashboards`` array.
    for uid, info in live.items():
        for slug in info["dashboards"]:
            all_dashboards.add(slug)
            membership.setdefault(slug, set()).add(uid)

    # Chart registry — ``metric_key`` (displayed) vs ``depends_on`` (inputs).
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    for entry in registry.values():
        if not isinstance(entry, dict):
            continue
        slug = entry.get("dashboard_id")
        if not slug:
            continue
        slug = str(slug)
        all_dashboards.add(slug)
        mk = entry.get("metric_key")
        if mk in live:
            chart_metric.setdefault(slug, set()).add(str(mk))
        for ref in entry.get("depends_on") or []:
            if ref in live:
                depends.setdefault(slug, set()).add(ref)

    floor: dict[str, set[str]] = {}
    dependency: dict[str, set[str]] = {}
    for slug in all_dashboards:
        f = chart_metric.get(slug, set()) | membership.get(slug, set())
        floor[slug] = f
        dependency[slug] = depends.get(slug, set()) - f
    return floor, dependency, all_dashboards


def _dominant_product_id(metric_uids: set[str], live: dict[str, dict[str, Any]]) -> str:
    """Return the most common spine product id among a dashboard's shown metrics.

    Deterministic tie-break: highest count, then lexicographically smallest
    product id. Falls back to :data:`DEFAULT_PRODUCT_ID` when the dashboard shows
    no live metric (so an unmapped/empty product never breaks validation).
    """
    counts: Counter[str] = Counter()
    for uid in metric_uids:
        pid = PRODUCT_TO_ID.get(live[uid]["product"] or "")
        if pid:
            counts[pid] += 1
    if not counts:
        return DEFAULT_PRODUCT_ID
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _dashboard_draft(slug: str, floor: set[str], live: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic, fully-valid Dashboard draft for one slug.

    ``product_id`` / ``domain_ids`` are derived from the genuinely-shown *floor*
    metrics (never from intermediate dependencies), so an input metric pulled in
    by ``depends_on`` cannot skew a dashboard's product or domains. ``domain_ids``
    captures all domains the floor metrics span (multi-domain preserved).
    """
    domain_ids = sorted({live[uid]["domain"] for uid in floor if live[uid]["domain"]})
    return {
        "dashboard_id": slug,
        "display_name": _title_case(slug),
        "product_id": _dominant_product_id(floor, live),
        "data_classification": DEFAULT_DATA_CLASSIFICATION,
        "min_level": DEFAULT_MIN_LEVEL,
        "status": DEFAULT_STATUS,
        "route_path": f"/{slug}",
        "domain_ids": domain_ids,
        "dashboard_type": _dashboard_type(slug),
        "source_registry": SOURCE_REGISTRY,
    }


def _candidate(uid: str, live: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A compact candidate-metric record for the proposer's LLM prompt."""
    info = live[uid]
    return {
        "metric_uid": uid,
        "title": info["title"],
        "is_kpi": info["is_kpi"],
        "is_derived": info["is_derived"],
    }


@lru_cache(maxsize=1)
def _build() -> dict[str, Any]:
    """Build (and cache) the full deterministic dashboard plan from the sources."""
    live = load_live_metrics()
    floor, dependency, all_dashboards = _build_roles(live)

    dashboards: dict[str, dict[str, Any]] = {}
    floor_edges = 0
    dep_candidates = 0
    for slug in sorted(all_dashboards):
        f = floor.get(slug, set())
        deps = dependency.get(slug, set())
        floor_edges += len(f)
        dep_candidates += len(deps)
        dashboards[slug] = {
            "dashboard": _dashboard_draft(slug, f, live),
            # Deterministic floor: genuinely-shown metrics (always mapped).
            "shown_on": sorted(f),
            # Suspect intermediates the LLM adjudicates (may add a few back).
            "dependency_candidates": [_candidate(u, live) for u in sorted(deps)],
        }

    covered = len({u for ms in floor.values() for u in ms})
    return {
        "dashboards": dashboards,
        "counts": {
            "dashboards": len(dashboards),
            "floor_edges": floor_edges,
            "dependency_candidates": dep_candidates,
            "metrics_covered": covered,
            "live_metrics": len(live),
            "unlinked_dashboards": sum(1 for d in dashboards.values() if not d["shown_on"]),
        },
    }


def run_prepass() -> dict:
    """Run the full deterministic dashboard pre-pass.

    Returns:
        ``{"dashboards": {slug: {"dashboard": <draft>, "shown_on": [floor uid...],
        "dependency_candidates": [{metric_uid,title,is_kpi,is_derived}...]}},
        "counts": {...}}``.
    """
    return _build()


def prepass_for(dashboard_id: str) -> dict:
    """Return the ``{"dashboard", "shown_on", "dependency_candidates"}`` slice.

    Raises:
        KeyError: If the dashboard id is in neither source.
    """
    dashboards = run_prepass()["dashboards"]
    if dashboard_id not in dashboards:
        raise KeyError(f"unknown dashboard_id: {dashboard_id!r}")
    return dashboards[dashboard_id]


def all_dashboard_ids() -> list[str]:
    """Return every distinct dashboard id, in stable sorted order."""
    return sorted(run_prepass()["dashboards"])
