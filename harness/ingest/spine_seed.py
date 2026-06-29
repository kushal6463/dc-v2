"""Idempotent tri-axis spine seeder — Phase 0 of the agentic build.

Seeds the reusable, client-portable backbone the LLM build attaches metrics to:
the single :class:`~harness.kg.models.Business` root, the
:class:`~harness.kg.models.Domain` functional columns, the
:class:`~harness.kg.models.IntelligenceProduct` IQ apps, and the
:class:`~harness.kg.models.Platform` source/action vendors. It also draws the
**Business root edges** (``HAS_DOMAIN`` / ``HAS_PRODUCT`` / ``USES_PLATFORM``) and
the platform ``PARENT_OF`` hierarchy, so the tri-axis spine is fully rooted (not a
set of disconnected islands). The spine is seeded **deterministically first**
(this script) so the per-metric LLM job only has to create each metric node and
wire it onto an already-present, already-rooted spine.

Sources are two LOCAL seed files (never BC_2): ``harness/seed/spine_seed.json``
(``business`` + ``domains[]`` + ``products[]``) and
``harness/seed/platforms.json`` (``platforms[]``). Each entry is built into its
Pydantic node model (so a malformed entry fails validation before any write)
and upserted through the single arbitration writer
(:func:`~harness.kg.arbitration.write_node_model`), which ``MERGE``\\ s on the
node's identity field. Re-running is therefore a **no-op on identity** — the
spine never duplicates (verify via ``kg_status``).

Run it as a module::

    uv run python -m harness.ingest.spine_seed            # seed into Neo4j
    uv run python -m harness.ingest.spine_seed --dry-run  # build + print only

``--dry-run`` builds and validates every model and prints them WITHOUT touching
the database (so the seeder can be smoke-tested offline, with no Neo4j running).

Pure inputs: stdlib + :mod:`harness.kg.models` + the arbitration writer; the DB
singleton is only acquired on a real (non-dry-run) seed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness.kg import models
from harness.kg.arbitration import upsert_edge, write_node_model
from harness.kg.driver import get_db

#: Spine seed file — the Business root plus the Domain and IntelligenceProduct
#: axes. LOCAL (never a BC_2 seed).
SPINE_SEED: Path = Path(__file__).resolve().parents[1] / "seed" / "spine_seed.json"

#: Platform seed file — the source/action vendor axis. LOCAL (never a BC_2 seed).
PLATFORMS_SEED: Path = Path(__file__).resolve().parents[1] / "seed" / "platforms.json"


def build_models() -> list[models.GraphNode]:
    """Build every spine node model from the LOCAL seed files (no DB touch).

    Reads :data:`SPINE_SEED` (``business`` + ``domains[]`` + ``products[]``) and
    :data:`PLATFORMS_SEED` (``platforms[]``) and constructs the corresponding
    Pydantic node models — one :class:`~harness.kg.models.Business`, then the
    :class:`~harness.kg.models.Domain`, :class:`~harness.kg.models.\
IntelligenceProduct`, and :class:`~harness.kg.models.Platform` nodes in seed
    order. A malformed entry raises a Pydantic ``ValidationError`` here, before
    any write happens.

    Returns:
        The spine node models in upsert order (Business first).
    """
    spine: dict[str, Any] = json.loads(SPINE_SEED.read_text())
    platforms: dict[str, Any] = json.loads(PLATFORMS_SEED.read_text())

    built: list[models.GraphNode] = [models.Business(**spine["business"])]
    built += [models.Domain(**entry) for entry in spine.get("domains", [])]
    built += [
        models.IntelligenceProduct(**entry) for entry in spine.get("products", [])
    ]
    built += [models.Platform(**entry) for entry in platforms.get("platforms", [])]
    return built


def seed_spine(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """Build and (unless ``dry_run``) upsert every spine node, returning a summary.

    Builds all models first via :func:`build_models` (so validation fails before
    any write). On a real run each model is upserted through
    :func:`~harness.kg.arbitration.write_node_model` — a ``MERGE`` on identity,
    so re-running never duplicates. On a ``dry_run`` the database is never
    touched (the DB singleton is not even acquired): each model is reported with
    a ``"dry_run"`` status and its validated property map is printed.

    Args:
        dry_run: When ``True``, build + validate + print only — no DB write.

    Returns:
        One result dict per node (the :func:`write_node_model` result, or a
        ``{"status": "dry_run", "label", "key"}`` stub on a dry run).
    """
    built = build_models()

    if dry_run:
        results: list[dict[str, Any]] = []
        for model in built:
            print(json.dumps(model.cypher_props(), sort_keys=True, default=str))
            results.append(
                {"status": "dry_run", "label": model.LABEL, "key": model.key_value}
            )
        print(f"dry-run: built {len(built)} spine nodes (no DB write)")
        return results

    db = get_db()
    results = [write_node_model(db, model) for model in built]
    created = sum(1 for r in results if r.get("status") == "created")
    updated = sum(1 for r in results if r.get("status") == "updated")

    # Business ROOT edges: connect the single Business node to every Domain
    # (HAS_DOMAIN), IntelligenceProduct (HAS_PRODUCT), and Platform
    # (USES_PLATFORM) so the tri-axis spine is actually ROOTED. Without these the
    # Business node is an island and the canvas spine is disconnected. Idempotent
    # (MERGE on the edge); mirrors ``kg bootstrap-spine``.
    business = built[0]  # build_models() always emits the Business root first
    _ROOT_REL = {
        "Domain": "HAS_DOMAIN",
        "IntelligenceProduct": "HAS_PRODUCT",
        "Platform": "USES_PLATFORM",
    }
    root_edges = 0
    for model in built[1:]:
        rel = _ROOT_REL.get(model.LABEL)
        if rel is None:
            continue
        upsert_edge(
            db, rel_type=rel, from_label="Business", from_key=business.key_value,
            to_label=model.LABEL, to_key=model.key_value,
            props={"source_kind": "spine_seed"},
        )
        root_edges += 1

    # Platform hierarchy: draw PARENT_OF for every sub-platform that names a
    # parent (e.g. google_youtube -> google_ads), so a rebuild recreates the
    # google/meta sub-channel tree. Idempotent (MERGE on the edge).
    platforms: dict[str, Any] = json.loads(PLATFORMS_SEED.read_text())
    parent_edges = 0
    for entry in platforms.get("platforms", []):
        parent = entry.get("parent_platform_id")
        if parent:
            upsert_edge(
                db, rel_type="PARENT_OF", from_label="Platform", from_key=parent,
                to_label="Platform", to_key=entry["platform_id"],
                props={"source_kind": "spine_seed"},
            )
            parent_edges += 1
    print(
        f"seeded {len(results)} spine nodes ({created} created, {updated} updated)"
        f"; drew {root_edges} Business root edges "
        f"(HAS_DOMAIN/HAS_PRODUCT/USES_PLATFORM) + {parent_edges} platform PARENT_OF edges"
    )
    return results


def main(argv: list[str] | None = None) -> int:
    """Parse args, seed the spine, return an exit code."""
    ap = argparse.ArgumentParser(prog="kg-spine-seed")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="build + validate + print the spine models WITHOUT touching the DB",
    )
    a = ap.parse_args(argv)
    seed_spine(dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
