"""Idempotent Google-Ads governance demo seeder.

Populates the (otherwise empty) governance layer with a realistic demo: one or
more Policies + a single shared Threshold per Google-Ads KPI (ROAS, CPA, CTR,
CPC, conversion rate). Each metric is wired by one ``HAS_THRESHOLD`` edge plus
``GOVERNS`` + ``ENFORCES_THRESHOLD`` per policy
(``Policy -GOVERNS-> Metric``, ``Metric -HAS_THRESHOLD-> Threshold``,
``Policy -ENFORCES_THRESHOLD-> Threshold``). A metric entry may carry either a
singular ``policy`` (back-compat) or a ``policies`` list. Three of the KPIs (CTR, CPC,
conversion rate) are not yet in the graph, so the seeder ``MERGE``\\ s those
Metric nodes first (cloning the Google-Ads scaffolding) and draws their
``DECOMPOSES_INTO`` formula edges to the existing component metrics.

Source is one LOCAL seed file (never BC_2):
``harness/seed/governance.rare_seeds.json``. Every node is built into its
:mod:`harness.kg.models` Pydantic model (malformed entries fail validation before
any write) and upserted through the single arbitration writer
(:func:`~harness.kg.arbitration.write_node_model` / ``upsert_edge``), which
``MERGE``\\ s on identity — re-running is a **no-op on identity** (no duplicates).

Run it as a module::

    uv run python -m harness.seed.governance_seed            # seed into Neo4j
    uv run python -m harness.seed.governance_seed --dry-run  # build + print only

…or via the CLI: ``uv run kg seed-governance`` (``--dry-run`` supported).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from harness.kg import models
from harness.kg.arbitration import upsert_edge, write_node_model
from harness.kg.driver import get_db

#: Governance demo seed file (LOCAL — never a BC_2 source).
GOVERNANCE_SEED: Path = (
    Path(__file__).resolve().parent / "governance.rare_seeds.json"
)

#: Stamped on every node/edge this seeder writes (provenance for later cleanup).
_SOURCE = "demo_seed"


def _slug(text: str | None, *, fallback: str = "policy") -> str:
    """Lower-snake a label into an id-safe slug (``fallback`` when empty)."""
    out = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return out or fallback


def _metric_model(uid: str, m: dict[str, Any]) -> models.Metric:
    """Build a Google-Ads Metric node from a seed ``metric`` block.

    Injects the shared Google-Ads scaffolding (scope/product/domain/platform,
    identity) so the seed block only carries the metric's distinguishing fields.
    The ``components`` key (formula edges) is consumed by the caller, not here.
    """
    return models.Metric(
        metric_uid=uid,
        canonical_id=uid,
        metric_id=uid,
        scope_key="google_ads",
        metric_base=m["metric_base"],
        display_name=m["display_name"],
        product_ids=["miq"],
        domain_ids=["marketing"],
        platform_ids=["google_ads"],
        primary_platform_id="google_ads",
        data_classification="internal",
        min_level=0,
        category=m.get("category", "google_ads"),
        unit_family=m.get("unit_family"),
        value_format=m.get("value_format"),
        default_direction=m.get("default_direction"),
        aggregation=m.get("aggregation"),
        is_derived=m.get("is_derived", True),
        node_kind=m.get("node_kind", "metric"),
        description=m.get("description"),
        formula_text=m.get("formula_text"),
        status="active",
    )


def _entry_policies(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a metric entry's policies as a list.

    Accepts either the singular ``policy`` (back-compat) or a ``policies`` list.
    A metric may carry several policies (alerting / budget / SLA …); all of them
    enforce the metric's single shared Threshold band-set.
    """
    if entry.get("policies"):
        return list(entry["policies"])
    return [entry["policy"]]


def _policy_id(uid: str, policy: dict[str, Any]) -> str:
    """Derive a policy id, slugged by its name so multiple policies stay distinct."""
    return f"policy:{uid}:{_slug(policy.get('policy_name'))}"


def _threshold_id(uid: str) -> str:
    """The one shared Threshold band-set id for a metric."""
    return f"threshold:{uid}:bands"


def seed_governance(*, dry_run: bool = False) -> dict[str, Any]:
    """Build and (unless ``dry_run``) write every governance node + edge.

    Builds all node models first (validation fails before any write). On a real
    run each is upserted through the single arbitration writer; the governance
    edges (and the derived-metric formula edges) are drawn with ``upsert_edge``.
    All writes ``MERGE`` on identity, so re-running never duplicates.

    Args:
        dry_run: When ``True``, build + validate + print only — no DB write.

    Returns:
        A summary dict ``{metrics, policies, thresholds, edges, missing_edges}``.
    """
    data: dict[str, Any] = json.loads(GOVERNANCE_SEED.read_text())
    entries: list[dict[str, Any]] = data["metrics"]

    # Build every model up front so a malformed entry fails before any write.
    metric_models: list[tuple[models.Metric, list[dict[str, str]]]] = []
    policy_models: list[models.Policy] = []
    threshold_models: list[models.Threshold] = []
    for entry in entries:
        uid = entry["metric_uid"]
        tid = _threshold_id(uid)
        if entry.get("create_metric"):
            block = dict(entry["metric"])
            components = block.pop("components", [])
            metric_models.append((_metric_model(uid, block), components))
        for policy in _entry_policies(entry):
            policy_models.append(
                models.Policy(
                    **policy,
                    policy_id=_policy_id(uid, policy),
                    metric_id=uid,
                    applies_to_kind="Metric",
                    population_status="populated",
                    review_state="active",
                    source=_SOURCE,
                )
            )
        threshold_models.append(
            models.Threshold(
                **entry["threshold"],
                threshold_id=tid,
                metric_id=uid,
                population_status="populated",
                review_state="active",
                source=_SOURCE,
            )
        )

    if dry_run:
        for model, _ in metric_models:
            print(json.dumps(model.cypher_props(), sort_keys=True, default=str))
        for model in policy_models + threshold_models:
            print(json.dumps(model.cypher_props(), sort_keys=True, default=str))
        # 1 HAS_THRESHOLD per metric + (GOVERNS + ENFORCES_THRESHOLD) per policy.
        gov_edges = sum(1 + 2 * len(_entry_policies(e)) for e in entries)
        n_edges = gov_edges + sum(len(c) for _, c in metric_models)
        print(
            f"dry-run: built {len(metric_models)} metrics, {len(policy_models)} "
            f"policies, {len(threshold_models)} thresholds, {n_edges} edges "
            "(no DB write)"
        )
        return {
            "metrics": len(metric_models),
            "policies": len(policy_models),
            "thresholds": len(threshold_models),
            "edges": n_edges,
            "missing_edges": 0,
        }

    db = get_db()
    # 1) Missing metrics first (so the governance edges find both endpoints) +
    #    their DECOMPOSES_INTO formula edges to existing component metrics.
    edges = 0
    missing = 0
    for model, components in metric_models:
        write_node_model(db, model)
        for comp in components:
            res = upsert_edge(
                db,
                rel_type="DECOMPOSES_INTO",
                from_label="Metric",
                from_key=model.metric_uid,
                to_label="Metric",
                to_key=comp["to"],
                props={
                    "relation": "formula",
                    "role": comp["role"],
                    "confidence": 1.0,
                    "source_kind": _SOURCE,
                },
            )
            edges += 1
            missing += res.get("status") == "missing_endpoint"

    # 2) Policy + Threshold nodes.
    for model in policy_models + threshold_models:
        write_node_model(db, model)

    # 3) Governance edges: one shared HAS_THRESHOLD per metric, plus GOVERNS +
    #    ENFORCES_THRESHOLD per policy (every policy enforces the same Threshold).
    for entry in entries:
        uid = entry["metric_uid"]
        tid = _threshold_id(uid)
        triples: list[tuple[str, str, str, str, str]] = [
            ("HAS_THRESHOLD", "Metric", uid, "Threshold", tid),
        ]
        for policy in _entry_policies(entry):
            pid = _policy_id(uid, policy)
            triples.append(("GOVERNS", "Policy", pid, "Metric", uid))
            triples.append(("ENFORCES_THRESHOLD", "Policy", pid, "Threshold", tid))
        for rel_type, fl, fk, tl, tk in triples:
            res = upsert_edge(
                db,
                rel_type=rel_type,
                from_label=fl,
                from_key=fk,
                to_label=tl,
                to_key=tk,
                props={"source_kind": _SOURCE},
            )
            edges += 1
            missing += res.get("status") == "missing_endpoint"

    summary = {
        "metrics": len(metric_models),
        "policies": len(policy_models),
        "thresholds": len(threshold_models),
        "edges": edges,
        "missing_edges": missing,
    }
    print(
        f"seeded governance: +{summary['metrics']} metrics, "
        f"{summary['policies']} policies, {summary['thresholds']} thresholds, "
        f"{edges} edges ({missing} missing-endpoint)"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    """Parse args, seed the governance demo, return an exit code."""
    ap = argparse.ArgumentParser(prog="kg-governance-seed")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="build + validate + print the models WITHOUT touching the DB",
    )
    a = ap.parse_args(argv)
    seed_governance(dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
