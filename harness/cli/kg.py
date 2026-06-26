"""Command-line interface for the ThoughtWire Causal Knowledge Graph.

This is the ``kg`` console-script entry point (``[project.scripts] kg``). It
exposes both the Milestone-1 spine-bootstrap workflow and the Milestone-2
metric / UIComponent ingestion engine.

Milestone 1 — spine bootstrap:

* ``schema-init`` — apply the Neo4j constraints and indexes.
* ``bootstrap-spine`` — load ``harness/seed/spine_seed.json`` and upsert the
  Business / Domain / IntelligenceProduct spine (plus the ``HAS_DOMAIN`` /
  ``HAS_PRODUCT`` edges) through the single arbitration writer.
* ``status`` — print node counts per label and whether constraints exist.
* ``lookup`` — fetch one node by label + key.

Milestone 2 — ingestion engine:

* ``prepass`` — run the deterministic (zero-LLM) pre-pass and print draft counts.
* ``ingest-dashboard`` — propose nodes/edges for one dashboard (optionally
  auto-approve + apply).
* ``ingest-all`` — propose across many dashboards concurrently.
* ``ingest-dashboards`` — ingest Dashboard surfaces over the existing live
  metrics: deterministic in-repo SHOWN_ON edges (every edge targets one of the
  live 317 metric uids) + parallel LLM enrichment of the descriptive fields.
* ``proposals list|approve|reject`` — inspect and triage the proposal queue.
* ``apply`` — replay approved proposals through the arbitration writer.
* ``reconcile`` — collapse duplicate nodes (e.g. concept metrics) via the
  reconcile pass.

Milestone 3 — edge model:

* ``migrate-metric-edges`` — rewrite legacy ``ROLLS_UP_TO`` / ``CORRELATES_WITH``
  / ``CAUSES`` edges onto the V1 ``DECOMPOSES_INTO`` + ``INFLUENCES`` model
  (originals deprecated, never deleted) through arbitration.

Agentic build:

* ``build`` — construct the metric/edge layer with the LLM agentic builder
  (:mod:`harness.agentic`). The deterministic skeleton / causal / edge-seed
  construction is gone; an LLM reads every metric and builds nodes + edges
  itself (currently a stub pending the agentic engine).

Every write flows through :mod:`harness.kg.arbitration`; the CLI never touches
the driver for mutations directly. Database subcommands fail with a friendly
message when ``NEO4J_PASSWORD`` is unset (the password is empty by default).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from harness.agent import engine
from harness.ingest import apply as apply_mod
from harness.ingest import dashboard_prepass as dashboard_prepass_mod
from harness.ingest import prepass as prepass_mod
from harness.ingest.dashboard_proposer import propose_dashboard_with_cost
from harness.ingest.orchestrator import DEFAULT_CONCURRENCY, ingest_dashboards
from harness.kg import arbitration
from harness.kg import reconcile as reconcile_mod
from harness.kg.config import REPO_ROOT, get_settings
from harness.kg.driver import GraphDB, get_db
from harness.kg.models import (
    NODE_KEY_FIELDS,
    NODE_LABELS,
    Business,
    Domain,
    IntelligenceProduct,
    Platform,
    UIComponent,
)
from harness.kg.schema import init_schema
from harness.store import proposals as proposals_store

#: Default spine-seed file (Business + Domains + IntelligenceProducts).
SEED_PATH: Path = REPO_ROOT / "harness" / "seed" / "spine_seed.json"
#: Generalised chart-TYPE UIComponent seed (M2 product decision — see below).
#: We seed a small fixed set of chart-type UIComponent nodes once at bootstrap
#: (like the spine) instead of one UIComponent per chart-registry entry (646).
#: Metrics fold in the per-chart registry semantics and link to their chart-type
#: node via VISUALIZES. This intentionally deviates from schema §4's "one
#: UIComponent per registry entry" — an approved product decision.
COMPONENT_TYPES_PATH: Path = REPO_ROOT / "harness" / "seed" / "component_types.json"
#: Platform spine-axis seed (the five source/action vendors: ga4, google_ads,
#: meta_ads, klaviyo, magento). Seeded at bootstrap like the Domains/Products so
#: the SOURCES / USES_PLATFORM edges have a target before metric ingestion.
PLATFORMS_PATH: Path = REPO_ROOT / "harness" / "seed" / "platforms.json"


class CLIError(Exception):
    """A user-facing CLI error rendered as a friendly message (no traceback)."""


def _require_password() -> None:
    """Raise :class:`CLIError` if the Neo4j password is unset.

    Raises:
        CLIError: When ``NEO4J_PASSWORD`` resolves to an empty string.
    """
    settings = get_settings()
    if not settings.neo4j_password:
        raise CLIError(
            "NEO4J_PASSWORD is not set. Add it to harness/.env "
            "(NEO4J_PASSWORD=...) so the CLI can connect to Neo4j."
        )


def _connected_db() -> GraphDB:
    """Return the shared :class:`GraphDB`, verifying connectivity first.

    Raises:
        CLIError: If the password is unset or the server is unreachable / auth
            fails (the underlying :class:`RuntimeError` is rewrapped).
    """
    _require_password()
    db = get_db()
    try:
        db.verify()
    except RuntimeError as exc:
        raise CLIError(str(exc)) from exc
    return db


def _load_seed(path: Path | None = None) -> dict[str, Any]:
    """Load and JSON-parse the spine-seed file.

    Args:
        path: Optional override; defaults to :data:`SEED_PATH`.

    Returns:
        The parsed seed mapping (``{"business": {...}, "domains": [...],
        "products": [...]}``).

    Raises:
        CLIError: If the seed file is missing or is not valid JSON.
    """
    target = path or SEED_PATH
    if not target.exists():
        raise CLIError(f"Seed file not found: {target}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CLIError(f"Seed file {target} is not valid JSON: {exc}") from exc


def _build_spine_models(
    seed: dict[str, Any],
) -> tuple[Business, list[Domain], list[IntelligenceProduct]]:
    """Validate the seed payload into Pydantic spine models.

    Args:
        seed: The parsed seed mapping.

    Returns:
        ``(business, domains, products)``.

    Raises:
        CLIError: If validation fails for any node.
    """
    try:
        business = Business(**seed["business"])
        domains = [Domain(**d) for d in seed.get("domains", [])]
        products = [IntelligenceProduct(**p) for p in seed.get("products", [])]
    except (KeyError, TypeError, ValueError) as exc:
        raise CLIError(f"Seed payload does not match the spine models: {exc}") from exc
    return business, domains, products


def _build_component_types(path: Path | None = None) -> list[UIComponent]:
    """Load and validate the generalised chart-type UIComponent seed.

    These are the 17 *generalised* chart-TYPE nodes (15 ``ChartType`` values plus
    ``kpi_card`` / ``alert_panel``) seeded once at bootstrap — **not** one
    UIComponent per chart-registry entry (M2 product decision; see
    :data:`COMPONENT_TYPES_PATH`).

    Returns:
        The validated :class:`UIComponent` type nodes.

    Raises:
        CLIError: If the seed file is missing, not valid JSON, or fails model
            validation.
    """
    target = path or COMPONENT_TYPES_PATH
    if not target.exists():
        raise CLIError(f"Component-types seed file not found: {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CLIError(f"Component-types seed {target} is not valid JSON: {exc}") from exc
    try:
        return [UIComponent(**c) for c in data.get("component_types", [])]
    except (TypeError, ValueError) as exc:
        raise CLIError(
            f"Component-types seed does not match the UIComponent model: {exc}"
        ) from exc


def _build_platforms(path: Path | None = None) -> list[Platform]:
    """Load and validate the Platform spine-axis seed.

    The five source/action vendors (ga4, google_ads, meta_ads, klaviyo, magento)
    seeded once at bootstrap like the Domains/Products, so the metric ``SOURCES``
    edges resolve to a real Platform node (:data:`PLATFORMS_PATH`).

    Returns:
        The validated :class:`Platform` nodes.

    Raises:
        CLIError: If the seed file is missing, not valid JSON, or fails model
            validation.
    """
    target = path or PLATFORMS_PATH
    if not target.exists():
        raise CLIError(f"Platforms seed file not found: {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CLIError(f"Platforms seed {target} is not valid JSON: {exc}") from exc
    try:
        return [Platform(**p) for p in data.get("platforms", [])]
    except (TypeError, ValueError) as exc:
        raise CLIError(
            f"Platforms seed does not match the Platform model: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_schema_init(_args: argparse.Namespace) -> int:
    """Apply the Neo4j constraints and indexes, then print a summary.

    Returns:
        Process exit code (0 on success).
    """
    db = _connected_db()
    summary = init_schema(db)
    print(
        f"Schema initialized: {summary['constraints']} constraints, "
        f"{summary['indexes']} indexes applied."
    )
    return 0


def cmd_bootstrap_spine(args: argparse.Namespace) -> int:
    """Bootstrap the Business/Domain/IntelligenceProduct/Platform spine + chart-types.

    Loads ``spine_seed.json``, ``platforms.json`` and ``component_types.json``,
    validates all into models, then (unless ``--dry-run``) upserts every node —
    Business, Domains, IntelligenceProducts, the five Platforms, and the 17
    generalised chart-type UIComponent nodes — plus the ``HAS_DOMAIN`` /
    ``HAS_PRODUCT`` / ``USES_PLATFORM`` edges through the arbitration writer,
    printing a created/updated table. The Platform spine axis is seeded here (like
    the Domains/Products) so the metric ``SOURCES`` edges resolve, and the
    chart-type UIComponents are seeded here (not per chart-registry entry) so they
    exist before ingestion.

    Returns:
        Process exit code (0 on success).
    """
    seed = _load_seed()
    business, domains, products = _build_spine_models(seed)
    # Generalised chart-type UIComponent nodes (M2 product decision): seeded once
    # at bootstrap like the spine, so the 17 type nodes exist before ingestion
    # (ingested metrics link to them via VISUALIZES). Deviates from schema §4's
    # one-UIComponent-per-registry-entry — an approved product decision.
    component_types = _build_component_types()
    # Platform spine axis (the five source/action vendors): seeded here like the
    # Domains/Products so the metric SOURCES edges resolve to a real Platform.
    platforms = _build_platforms()

    if args.dry_run:
        print("[dry-run] Validated spine seed (no writes):")
        print(f"  Business            : 1 ({business.business_id})")
        print(f"  Domain              : {len(domains)}")
        print(f"  IntelligenceProduct : {len(products)}")
        print(f"  Platform            : {len(platforms)}")
        print(f"  UIComponent (types) : {len(component_types)}")
        print(f"  HAS_DOMAIN edges    : {len(domains)}")
        print(f"  HAS_PRODUCT edges   : {len(products)}")
        print(f"  USES_PLATFORM edges : {len(platforms)}")
        return 0

    db = _connected_db()
    rows: list[tuple[str, str, str]] = []

    result = arbitration.write_node_model(db, business)
    rows.append(("Business", business.business_id, result["status"]))

    for domain in domains:
        result = arbitration.write_node_model(db, domain)
        rows.append(("Domain", domain.domain_id, result["status"]))

    for product in products:
        result = arbitration.write_node_model(db, product)
        rows.append(("IntelligenceProduct", product.product_id, result["status"]))

    for platform in platforms:
        result = arbitration.write_node_model(db, platform)
        rows.append(("Platform", platform.platform_id, result["status"]))

    for component in component_types:
        result = arbitration.write_node_model(db, component)
        rows.append(("UIComponent", component.component_id, result["status"]))

    for domain in domains:
        edge = arbitration.upsert_edge(
            db,
            rel_type="HAS_DOMAIN",
            from_label="Business",
            from_key=business.business_id,
            to_label="Domain",
            to_key=domain.domain_id,
        )
        rows.append(("HAS_DOMAIN", f"{business.business_id}->{domain.domain_id}", edge["status"]))

    for product in products:
        edge = arbitration.upsert_edge(
            db,
            rel_type="HAS_PRODUCT",
            from_label="Business",
            from_key=business.business_id,
            to_label="IntelligenceProduct",
            to_key=product.product_id,
        )
        rows.append(
            ("HAS_PRODUCT", f"{business.business_id}->{product.product_id}", edge["status"])
        )

    for platform in platforms:
        edge = arbitration.upsert_edge(
            db,
            rel_type="USES_PLATFORM",
            from_label="Business",
            from_key=business.business_id,
            to_label="Platform",
            to_key=platform.platform_id,
        )
        rows.append(
            ("USES_PLATFORM", f"{business.business_id}->{platform.platform_id}", edge["status"])
        )

    _print_table(("KIND", "KEY", "STATUS"), rows)
    created = sum(1 for _, _, status in rows if status == "created")
    updated = sum(1 for _, _, status in rows if status == "updated")
    print(f"\nBootstrap complete: {created} created, {updated} updated.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """Print node counts per label and whether constraints exist.

    Returns:
        Process exit code (0 on success).
    """
    db = _connected_db()

    print("Node counts per label:")
    count_rows: list[tuple[str, str]] = []
    for label in sorted(NODE_LABELS):
        # label is from the allowlist; safe to interpolate.
        result = db.read(f"MATCH (n:{label}) RETURN count(n) AS c")
        count = result[0]["c"] if result else 0
        count_rows.append((label, str(count)))
    _print_table(("LABEL", "COUNT"), count_rows)

    constraints = db.read("SHOW CONSTRAINTS YIELD name RETURN name ORDER BY name")
    names = [r["name"] for r in constraints]
    print(f"\nConstraints present: {len(names)}")
    for name in names:
        print(f"  - {name}")
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    """Fetch and print a single node by label + key.

    Returns:
        Process exit code (0 if found, 1 if not found).
    """
    label = args.label
    if label not in NODE_LABELS:
        raise CLIError(
            f"Unknown node label {label!r}; expected one of {sorted(NODE_LABELS)}"
        )
    key_field = NODE_KEY_FIELDS[label]

    db = _connected_db()
    # label/key_field are from the allowlist; value is parameterized.
    rows = db.read(
        f"MATCH (n:{label} {{{key_field}: $key}}) RETURN n",
        key=args.key,
    )
    if not rows:
        print(f"No {label} found with {key_field}={args.key!r}.")
        return 1
    print(json.dumps(rows[0]["n"], indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Milestone-2 ingestion subcommands
# ---------------------------------------------------------------------------


def cmd_prepass(args: argparse.Namespace) -> int:
    """Run the deterministic pre-pass and print the draft counts.

    With ``--json`` the full :func:`harness.ingest.prepass.run_prepass` result is
    dumped as JSON; otherwise only the count summary is printed. No Neo4j access
    is performed (the pre-pass is purely file-driven).

    Returns:
        Process exit code (0 on success).
    """
    result = prepass_mod.run_prepass()
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    counts = result["counts"]
    rows = [
        ("dashboards", str(counts["dashboards"])),
        ("components", str(counts["components"])),
        ("metrics", str(counts["metrics"])),
        ("excluded_endpoints", str(counts["excluded_endpoints"])),
    ]
    print("Pre-pass draft counts:")
    _print_table(("KIND", "COUNT"), rows)
    return 0


def _auto_approve_and_apply(db: GraphDB, run_id: str) -> None:
    """Approve every proposal in ``run_id`` then apply it through arbitration.

    Used by the ``--auto-approve`` flag of the ingest subcommands: it flips each
    proposal's ``review_state`` to ``approved`` via
    :func:`harness.store.proposals.set_review_state`, then replays the approved
    set with :func:`harness.ingest.apply.apply_approved`, printing an apply
    summary.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        run_id: The run whose proposals should be approved and applied.
    """
    proposals = proposals_store.load_proposals(run_id=run_id)
    approved = 0
    for proposal in proposals:
        if proposals_store.set_review_state(run_id, proposal["proposal_id"], "approved"):
            approved += 1
    print(f"Auto-approved {approved} proposal(s) in run {run_id}.")

    summary = apply_mod.apply_approved(db, run_id=run_id)
    _print_apply_summary(summary)


def _print_apply_summary(summary: dict[str, Any]) -> None:
    """Print the aggregate counts returned by :func:`apply_approved`."""
    rows = [
        ("nodes_created", str(summary["nodes_created"])),
        ("nodes_updated", str(summary["nodes_updated"])),
        ("edges", str(summary["edges"])),
        ("skipped_missing_endpoint", str(summary["skipped_missing_endpoint"])),
        ("skipped_invalid", str(summary.get("skipped_invalid", 0))),
    ]
    print("\nApply summary:")
    _print_table(("METRIC", "COUNT"), rows)


def _print_ingest_summary(summary: dict[str, Any]) -> None:
    """Print the per-run summary returned by :func:`ingest_dashboards`."""
    print(
        f"Ingest run {summary['run_id']}: {summary['dashboards']} dashboard(s), "
        f"{summary['proposals']} proposal(s), {len(summary['errors'])} error(s)."
    )
    for err in summary["errors"]:
        print(f"  ! {err['dashboard_id']}: {err['error']}", file=sys.stderr)


def cmd_ingest_dashboard(args: argparse.Namespace) -> int:
    """Propose nodes/edges for a single dashboard.

    Validates the dashboard id against the pre-pass, drives the async
    orchestrator for that one dashboard (writing its proposals to a fresh run),
    and — when ``--auto-approve`` is set — approves and applies the run.

    Returns:
        Process exit code (0 on success).
    """
    dashboard_id = args.dashboard_id
    if dashboard_id not in prepass_mod.all_dashboard_ids():
        raise CLIError(
            f"Unknown dashboard_id {dashboard_id!r}. Run `kg prepass --json` or "
            "check the chart registry for valid ids."
        )

    db = _connected_db()
    summary = engine.run_sync(ingest_dashboards([dashboard_id], db=db))
    _print_ingest_summary(summary)

    if args.auto_approve:
        _auto_approve_and_apply(db, summary["run_id"])
    return 0


def cmd_ingest_all(args: argparse.Namespace) -> int:
    """Propose nodes/edges across many dashboards concurrently.

    Selects ``all_dashboard_ids()[:limit]`` (all when ``--limit`` is omitted),
    runs the bounded-concurrency orchestrator, and — when ``--auto-approve`` is
    set — approves and applies the resulting run.

    Returns:
        Process exit code (0 on success).
    """
    dashboard_ids = prepass_mod.all_dashboard_ids()
    if args.limit is not None:
        dashboard_ids = dashboard_ids[: args.limit]
    if not dashboard_ids:
        print("No dashboards to ingest.")
        return 0

    db = _connected_db()
    summary = engine.run_sync(
        ingest_dashboards(dashboard_ids, concurrency=args.concurrency, db=db)
    )
    _print_ingest_summary(summary)

    if args.auto_approve:
        _auto_approve_and_apply(db, summary["run_id"])
    return 0


def cmd_ingest_dashboards(args: argparse.Namespace) -> int:
    """Ingest Dashboard surfaces over the existing live metrics.

    Deterministically merges the in-repo chart registry + metric catalog into the
    full dashboard set and the ground-truth ``SHOWN_ON`` edges (every edge targets
    one of the live 317 metric uids), then fans out one LLM proposer subagent per
    dashboard (bounded concurrency) to enrich the descriptive fields. The LLM
    cannot add or retarget edges. With ``--auto-approve`` the run is approved and
    applied through the single arbitration writer.

    ``--dry-run`` prints the deterministic plan counts (dashboards / edges /
    metrics covered) without calling the LLM or touching Neo4j.

    Returns:
        Process exit code (0 on success).
    """
    plan = dashboard_prepass_mod.run_prepass()
    counts = plan["counts"]
    if args.dry_run:
        rows = [
            ("dashboards", str(counts["dashboards"])),
            ("edges (SHOWN_ON)", str(counts["edges"])),
            ("metrics_covered", f"{counts['metrics_covered']}/{counts['live_metrics']}"),
            ("unlinked_dashboards", str(counts["unlinked_dashboards"])),
        ]
        print("Dashboard ingestion plan (deterministic, no LLM/DB):")
        _print_table(("KIND", "COUNT"), rows)
        return 0

    dashboard_ids = dashboard_prepass_mod.all_dashboard_ids()
    if args.limit is not None:
        dashboard_ids = dashboard_ids[: args.limit]
    if not dashboard_ids:
        print("No dashboards to ingest.")
        return 0

    db = _connected_db()
    summary = engine.run_sync(
        ingest_dashboards(
            dashboard_ids,
            concurrency=args.concurrency,
            db=db,
            propose_fn=propose_dashboard_with_cost,
        )
    )
    _print_ingest_summary(summary)

    if args.auto_approve:
        _auto_approve_and_apply(db, summary["run_id"])
    return 0


def cmd_proposals_list(args: argparse.Namespace) -> int:
    """List proposals for a run (latest run when ``--run`` is omitted).

    Handles the no-run case gracefully (prints a hint and returns 0). Otherwise
    prints a per-proposal table (id, target label/id, review state).

    Returns:
        Process exit code (0 on success).
    """
    run_id = args.run or proposals_store.latest_run_id()
    if run_id is None:
        print("No proposal runs found. Run `kg ingest-dashboard <id>` first.")
        return 0

    proposals = proposals_store.load_proposals(run_id=run_id)
    if not proposals:
        print(f"Run {run_id} has no proposals.")
        return 0

    rows = [
        (
            str(p.get("proposal_id", "")),
            str(p.get("target_label", "")),
            str(p.get("target_id", "")),
            str(p.get("review_state", "")),
        )
        for p in proposals
    ]
    print(f"Proposals in run {run_id} ({len(proposals)} total):")
    _print_table(("PROPOSAL_ID", "LABEL", "TARGET_ID", "STATE"), rows)
    return 0


def cmd_proposals_approve(args: argparse.Namespace) -> int:
    """Approve one proposal by id, or all proposals in a run with ``--all``.

    Returns:
        Process exit code (0 on success, 1 if a single id was not found).
    """
    run_id = args.run or proposals_store.latest_run_id()
    if run_id is None:
        raise CLIError("No proposal runs found; nothing to approve.")

    if args.all:
        proposals = proposals_store.load_proposals(run_id=run_id, state="proposed")
        approved = 0
        for proposal in proposals:
            if proposals_store.set_review_state(
                run_id, proposal["proposal_id"], "approved"
            ):
                approved += 1
        print(f"Approved {approved} proposal(s) in run {run_id}.")
        return 0

    if not args.proposal_id:
        raise CLIError("Provide a proposal id or use --all.")
    ok = proposals_store.set_review_state(run_id, args.proposal_id, "approved")
    if not ok:
        print(f"Proposal {args.proposal_id} not found in run {run_id}.")
        return 1
    print(f"Approved proposal {args.proposal_id} in run {run_id}.")
    return 0


def cmd_proposals_reject(args: argparse.Namespace) -> int:
    """Reject one proposal by id, recording a reason.

    Returns:
        Process exit code (0 on success, 1 if the id was not found).
    """
    run_id = args.run or proposals_store.latest_run_id()
    if run_id is None:
        raise CLIError("No proposal runs found; nothing to reject.")

    ok = proposals_store.set_review_state(
        run_id, args.proposal_id, "rejected", reason=args.reason
    )
    if not ok:
        print(f"Proposal {args.proposal_id} not found in run {run_id}.")
        return 1
    print(f"Rejected proposal {args.proposal_id} in run {run_id}.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply every approved proposal in a run through the arbitration writer.

    Returns:
        Process exit code (0 on success).
    """
    db = _connected_db()
    summary = apply_mod.apply_approved(db, run_id=args.run)
    run_label = args.run or proposals_store.latest_run_id() or "(none)"
    print(f"Applied approved proposals from run {run_label}.")
    _print_apply_summary(summary)
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Collapse duplicate nodes for a label (default ``Metric``).

    Finds every identity-key value shared by more than one node of ``label`` and
    merges each cluster into a single node via
    :func:`harness.kg.reconcile.merge_duplicates` (APOC strategy). With
    ``--dry-run`` the duplicate clusters are reported but never merged.

    Returns:
        Process exit code (0 on success).
    """
    label = args.label
    if label not in NODE_LABELS:
        raise CLIError(
            f"Unknown node label {label!r}; expected one of {sorted(NODE_LABELS)}"
        )
    key_field = NODE_KEY_FIELDS[label]

    db = _connected_db()
    # Find identity values that appear on more than one node of this label.
    # label/key_field come from the model allowlist; safe to interpolate.
    dup_rows = db.read(
        f"MATCH (n:{label}) "
        f"WITH n.{key_field} AS kv, count(*) AS c "
        "WHERE c > 1 RETURN kv AS key, c AS count ORDER BY key"
    )
    if not dup_rows:
        print(f"No duplicate {label} nodes found (keyed on {key_field}).")
        return 0

    print(f"Found {len(dup_rows)} duplicate {label} cluster(s):")
    _print_table(
        ("KEY", "COUNT"),
        [(str(r["key"]), str(r["count"])) for r in dup_rows],
    )

    if args.dry_run:
        print("\n[dry-run] No merges performed.")
        return 0

    merged_total = 0
    for row in dup_rows:
        result = reconcile_mod.merge_duplicates(db, label, key_field, str(row["key"]))
        merged_total += int(result.get("merged_count", 0))
    print(
        f"\nReconcile complete: {len(dup_rows)} cluster(s) collapsed, "
        f"{merged_total} duplicate node(s) merged."
    )
    return 0


def cmd_migrate_metric_edges(args: argparse.Namespace) -> int:
    """Rewrite legacy metric->metric edge types onto the V1 two-edge model.

    The V1 metric edge model is exactly ``DECOMPOSES_INTO`` (structural) +
    ``INFLUENCES`` (causal). This migrates any legacy edge still in the graph
    onto it, **deprecating** the original (never deleting) and writing the
    replacement through the single arbitration writer:

    * ``ROLLS_UP_TO``    -> ``DECOMPOSES_INTO {relation: rollup}`` (orientation
      flipped: a finer metric rolling UP to a coarser one becomes the coarser
      metric decomposing DOWN into the finer one).
    * ``CORRELATES_WITH``-> ``DECOMPOSES_INTO {relation: formula}`` when the edge
      is deterministic (``deterministic = true``), else
      ``INFLUENCES {relation: statistical}``.
    * ``CAUSES``         -> ``INFLUENCES {relation: promoted}``.

    With ``--dry-run`` the planned rewrites are printed but nothing is written.

    Returns:
        Process exit code (0 on success).
    """
    db = _connected_db()
    legacy = db.read(
        "MATCH (a:Metric)-[r:ROLLS_UP_TO|CORRELATES_WITH|CAUSES]->(b:Metric) "
        "RETURN a.metric_uid AS from_id, type(r) AS rel_type, "
        "b.metric_uid AS to_id, properties(r) AS props"
    )
    if not legacy:
        print("No legacy metric edges (ROLLS_UP_TO / CORRELATES_WITH / CAUSES) found.")
        return 0

    plan: list[tuple[str, ...]] = []
    for row in legacy:
        rel_type = str(row["rel_type"])
        from_id, to_id = str(row["from_id"]), str(row["to_id"])
        props = row.get("props") or {}
        new_from, new_to, new_type, new_relation = _migrated_edge(
            rel_type, from_id, to_id, props
        )
        plan.append((rel_type, f"{from_id}->{to_id}",
                     f"{new_type}{{{new_relation}}}", f"{new_from}->{new_to}"))

        if args.dry_run:
            continue

        # Write the replacement through arbitration (carries forward the legacy
        # edge's properties + a migration provenance stamp), then DEPRECATE the
        # original in place (never delete — append-only audit).
        new_props = {k: v for k, v in props.items()
                     if k not in ("relation", "status")}
        new_props["relation"] = new_relation
        new_props.setdefault("source_kind", "edge_migration")
        new_props["migrated_from"] = rel_type
        arbitration.upsert_edge(
            db,
            rel_type=new_type,
            from_label="Metric",
            from_key=new_from,
            to_label="Metric",
            to_key=new_to,
            props=new_props,
        )
        _deprecate_legacy_edge(db, rel_type, from_id, to_id)

    print(f"{'[dry-run] ' if args.dry_run else ''}Migration plan "
          f"({len(plan)} legacy edge(s)):")
    _print_table(("LEGACY_TYPE", "LEGACY", "NEW_TYPE", "NEW"), plan)
    if args.dry_run:
        print("\n[dry-run] No edges written or deprecated.")
    else:
        print(f"\nMigrated {len(plan)} legacy edge(s) "
              "(originals deprecated, never deleted).")
    return 0


def _migrated_edge(
    rel_type: str, from_id: str, to_id: str, props: dict[str, Any]
) -> tuple[str, str, str, str]:
    """Map a legacy edge to its ``(from, to, new_rel_type, new_relation)`` target.

    * ``ROLLS_UP_TO`` flips orientation (finer->coarser becomes coarser
      DECOMPOSES_INTO finer) with ``relation=rollup``.
    * ``CORRELATES_WITH`` becomes a deterministic ``DECOMPOSES_INTO {formula}``
      when the legacy edge is marked deterministic, else
      ``INFLUENCES {statistical}``.
    * ``CAUSES`` becomes ``INFLUENCES {promoted}``.
    """
    if rel_type == "ROLLS_UP_TO":
        return to_id, from_id, "DECOMPOSES_INTO", "rollup"
    if rel_type == "CORRELATES_WITH":
        if bool(props.get("deterministic")):
            return from_id, to_id, "DECOMPOSES_INTO", "formula"
        return from_id, to_id, "INFLUENCES", "statistical"
    if rel_type == "CAUSES":
        return from_id, to_id, "INFLUENCES", "promoted"
    raise CLIError(f"Unsupported legacy edge type {rel_type!r}.")


def _deprecate_legacy_edge(
    db: GraphDB, rel_type: str, from_id: str, to_id: str
) -> None:
    """Mark a migrated legacy edge ``status='deprecated'`` (never deletes).

    ``rel_type`` is validated against the legacy allowlist before interpolation
    (injection guard); endpoint ids are parameterized.
    """
    if rel_type not in ("ROLLS_UP_TO", "CORRELATES_WITH", "CAUSES"):
        raise CLIError(f"Refusing to deprecate non-legacy edge {rel_type!r}.")
    db.write(
        f"MATCH (a:Metric {{metric_uid: $from_id}})-[r:{rel_type}]->"
        "(b:Metric {metric_uid: $to_id}) "
        "SET r.status = 'deprecated', r.deprecated_at = datetime(), "
        "r.deprecation_reason = 'migrated_to_v1_edge_model'",
        from_id=from_id,
        to_id=to_id,
    )


def cmd_prune_empty(_args: argparse.Namespace) -> int:
    """Delete Domains and chart-type UIComponents that no Metric uses.

    Data-driven spine cleanup to run after a full ingest (e.g. drops ``hr`` when
    no HR dashboards exist). Business and IntelligenceProduct are never pruned.
    """
    db = _connected_db()
    pruned = reconcile_mod.prune_empty_spine(db)
    print(
        f"Pruned {len(pruned['domains'])} empty domain(s): {pruned['domains']}"
    )
    print(
        f"Pruned {len(pruned['components'])} unused chart-type(s): "
        f"{pruned['components']}"
    )
    return 0


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _print_table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    """Print a simple fixed-width text table.

    Args:
        header: Column titles.
        rows: Row tuples (each the same arity as ``header``).
    """
    columns = [header, *rows]
    widths = [max(len(str(row[i])) for row in columns) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def cmd_discover(args: argparse.Namespace) -> int:
    """Run the causal relationship-discovery engine (optional ``[discovery]`` extra).

    Dispatches to the vendored :mod:`harness.discovery.discover_engine` entry
    points by ``--mode``:

    * ``synthetic`` — self-test on a planted causal panel (no API/data needed);
      proves the engine recovers true lagged edges and FDR-rejects trend/season
      spurious pairs.
    * ``scan`` — fetch every kept node's series from the product API and persist
      the tenant series cache (``data/series/<tenant>.jsonl``).
    * ``pcmci`` — full PCMCI+ (tigramite) multivariate causal discovery; writes a
      tenant-scoped ``data/discovered_edges*.<tenant>.csv``.

    The heavy scientific stack (numpy / pandas / scikit-learn / statsmodels /
    tigramite) is imported LAZILY here so ``import harness.discovery`` and the
    rest of the CLI keep working with core deps only. When the extra is not
    installed the import fails and we surface a friendly install hint instead of
    a traceback.

    Each non-synthetic mode writes a TENANT-SCOPED CSV
    (``discovered_edges.<tenant>.csv``); a run for one tenant never clobbers
    another tenant's file.

    Returns:
        Process exit code (0 on success).

    Raises:
        CLIError: When the ``[discovery]`` extra is not installed.
    """
    try:
        from harness.discovery import discover_engine as engine_mod
    except ImportError as exc:
        raise CLIError(
            "discovery extra not installed — run: uv sync --extra discovery"
        ) from exc

    # API credentials come from the environment (no secrets baked into the CLI);
    # TW_API_BASE provides the default API base when --base is omitted.
    base = args.base or os.environ.get("TW_API_BASE") or "http://localhost:8005"
    email = os.environ.get("TW_API_EMAIL", "")
    password = os.environ.get("TW_API_PASSWORD", "")

    if args.mode == "synthetic":
        engine_mod.run_synthetic()
    elif args.mode == "scan":
        engine_mod.run_scan(args.tenant, base, email, password)
    elif args.mode == "pcmci":
        engine_mod.run_pcmci(
            args.tenant, base, email, password,
            test=args.test, tau_max=args.tau_max,
        )
    else:  # pragma: no cover - argparse choices guard this
        raise CLIError(f"Unknown discover mode {args.mode!r}.")
    return 0


def _parse_namespaces(raw: str | None) -> list[str] | None:
    """Parse the ``--namespaces a|b|c`` flag into a list (``None`` when absent)."""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    return parts or None


def _print_build_plan(plan: dict[str, Any]) -> None:
    """Pretty-print the offline build plan: phases, slices, prompts, SDK options.

    Renders everything :func:`harness.agentic.orchestrator.plan` produced plus the
    resolved :class:`ClaudeAgentOptions` preview — without ever importing the SDK
    or touching Neo4j.
    """
    from harness.agentic.runner import build_agent_options_preview

    print("=" * 78)
    print("AGENTIC BUILD — DRY PLAN (offline; no SDK, no Neo4j)")
    print("=" * 78)
    print(
        f"mode: {'smoke' if plan['smoke'] else 'full'}    "
        f"namespaces: {plan['namespaces'] or 'all'}"
    )
    print()

    slices = plan["slices"]
    print(f"NODE-PHASE SLICES ({len(slices)} buckets):")
    _print_table(
        ("bucket", "metrics"),
        [(s["label"], str(s["count"])) for s in slices],
    )
    print()

    print("PHASE PLAN:")
    for entry in plan["phases"]:
        agents = entry.get("parallel_agents")
        agent_note = f" — {agents} parallel agent(s)" if agents else ""
        barrier = " [BARRIER after]" if entry.get("barrier_after") else ""
        print(f"  Phase {entry['phase']} ({entry['label']}){agent_note}{barrier}")
        if entry.get("description"):
            print(f"      {entry['description']}")
    print()

    print("RESOLVED ClaudeAgentOptions:")
    print(json.dumps(build_agent_options_preview(), indent=2, default=str))
    print()

    print("SYSTEM PROMPTS (per phase):")
    for entry in plan["phases"]:
        if not entry.get("system_prompt"):
            continue
        print("-" * 78)
        print(f"# Phase {entry['phase']} — {entry['label']} (system)")
        print("-" * 78)
        print(entry["system_prompt"])
        print()
        print(f"# Phase {entry['phase']} — {entry['label']} (sample user prompt)")
        print(entry["sample_user_prompt"])
        print()

    print("=" * 78)
    print(
        "NOTE: a LIVE build WIPES + REBUILDS the graph. Back up first:\n"
        "      python -m harness.store.backup export"
    )
    print("=" * 78)


def cmd_build(args: argparse.Namespace) -> int:
    """Build the metric/edge layer with the LLM agentic builder.

    Drives the agentic build engine (:mod:`harness.agentic`): an LLM reads every
    metric and constructs the graph (nodes + edges) itself through the
    ``mcp__graph__*`` write tools, replacing the removed deterministic skeleton /
    causal / edge-seed construction.

    Flags:

    * ``--dry-plan`` — print the phase plan, node slices, system prompts, and the
      resolved :class:`ClaudeAgentOptions` WITHOUT importing the SDK or touching
      Neo4j (fully offline). The build engine itself is never started.
    * ``--smoke`` — build only one small namespace (the ``blended.*`` chain) and
      skip the destructive wipe (a non-destructive subset build).
    * ``--namespaces a|b`` — restrict the node phase to these ``source``
      namespaces (pipe-delimited).

    A live build WIPES + REBUILDS the graph; it should be preceded by
    ``python -m harness.store.backup export`` (the only safety net — dc-kg is not
    a git repo). The engine performs its own Phase-0 backup as well.

    Returns:
        Process exit code (0 on success).
    """
    namespaces = _parse_namespaces(getattr(args, "namespaces", None))
    smoke = bool(getattr(args, "smoke", False))
    resume = bool(getattr(args, "resume", False))

    # Offline plan path: import only the orchestrator's pure planner + the
    # options preview — never the SDK, never the Neo4j driver.
    if getattr(args, "dry_plan", False):
        from harness.agentic.orchestrator import plan as build_plan

        _print_build_plan(build_plan(smoke=smoke, namespaces=namespaces))
        return 0

    # Live build. Backup reminder up front (the engine also exports in Phase 0).
    # --resume keeps the existing (partial) graph and only fills the gaps.
    print(
        "NOTE: a live build wipes + rebuilds the graph (skipped with --resume / "
        "--smoke). It is preceded by an automatic Phase-0 backup, but you may "
        "also run `python -m harness.store.backup export` first.",
        file=sys.stderr,
    )
    from harness.agentic import runner

    report = engine.run_sync(
        runner.run(smoke=smoke, namespaces=namespaces, dry_plan=False, resume=resume)
    )
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    """Deterministically enrich the metric layer (mart/SQL/freshness + ledger).

    Runs the no-LLM enrichment against the LIVE graph (additive, idempotent):
    removes causal edges that parallel a formula edge (``critique_dedupe``),
    populates ``mart_sources`` / ``sql_query_real`` / ``source_columns`` /
    freshness from BC_2 + the registry (``run_deterministic_enrich``), and folds
    each legacy causal edge onto the Beta evidence ledger
    (``migrate_edge_ledger``). No graph wipe; back up first with
    ``python -m harness.store.backup export`` if desired.

    Returns:
        Process exit code (0 on success).
    """
    from harness.agentic import enrich

    dry = bool(getattr(args, "dry_run", False))
    if not getattr(args, "no_dedupe", False):
        print("critique_dedupe:", json.dumps(enrich.critique_dedupe(dry_run=dry), default=str))
    print("enrich:", json.dumps(
        enrich.run_deterministic_enrich(dry_run=dry, limit=getattr(args, "limit", None)),
        default=str,
    ))
    if not getattr(args, "no_migrate", False):
        print("migrate_edge_ledger:", json.dumps(
            enrich.migrate_edge_ledger(dry_run=dry), default=str
        ))
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with all subcommands.

    Returns:
        The configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="kg",
        description="ThoughtWire Causal Knowledge Graph — spine bootstrap CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_schema = subparsers.add_parser(
        "schema-init", help="Apply Neo4j constraints and indexes."
    )
    p_schema.set_defaults(func=cmd_schema_init)

    p_boot = subparsers.add_parser(
        "bootstrap-spine",
        help="Upsert the Business/Domain/IntelligenceProduct spine from the seed.",
    )
    p_boot.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the seed and report counts without writing to Neo4j.",
    )
    p_boot.set_defaults(func=cmd_bootstrap_spine)

    p_status = subparsers.add_parser(
        "status", help="Print node counts per label and existing constraints."
    )
    p_status.set_defaults(func=cmd_status)

    p_lookup = subparsers.add_parser("lookup", help="Fetch one node by label + key.")
    p_lookup.add_argument("label", help="Node label (e.g. Business, Domain).")
    p_lookup.add_argument("key", help="Identity value for that label's key field.")
    p_lookup.set_defaults(func=cmd_lookup)

    # --- Milestone-2 ingestion subcommands ---------------------------------

    p_prepass = subparsers.add_parser(
        "prepass",
        help="Run the deterministic pre-pass and print draft counts.",
    )
    p_prepass.add_argument(
        "--json",
        action="store_true",
        help="Dump the full pre-pass result as JSON instead of a summary.",
    )
    p_prepass.set_defaults(func=cmd_prepass)

    p_ingest_one = subparsers.add_parser(
        "ingest-dashboard",
        help="Propose nodes/edges for one dashboard.",
    )
    p_ingest_one.add_argument("dashboard_id", help="The dashboard slug to ingest.")
    p_ingest_one.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve and apply the run's proposals immediately.",
    )
    p_ingest_one.set_defaults(func=cmd_ingest_dashboard)

    p_ingest_all = subparsers.add_parser(
        "ingest-all",
        help="Propose nodes/edges across many dashboards concurrently.",
    )
    p_ingest_all.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only ingest the first N dashboards (default: all).",
    )
    p_ingest_all.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Max concurrent proposer agents (default: 6).",
    )
    p_ingest_all.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve and apply the run's proposals immediately.",
    )
    p_ingest_all.set_defaults(func=cmd_ingest_all)

    p_ingest_dash = subparsers.add_parser(
        "ingest-dashboards",
        help="Ingest Dashboard surfaces over the existing live metrics "
        "(deterministic edges + parallel LLM enrichment).",
    )
    p_ingest_dash.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the deterministic plan counts without calling the LLM or DB.",
    )
    p_ingest_dash.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only ingest the first N dashboards (default: all).",
    )
    p_ingest_dash.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Max concurrent proposer agents (default: 6).",
    )
    p_ingest_dash.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve and apply the run's proposals immediately.",
    )
    p_ingest_dash.set_defaults(func=cmd_ingest_dashboards)

    p_proposals = subparsers.add_parser(
        "proposals",
        help="Inspect and triage the proposal queue.",
    )
    proposals_sub = p_proposals.add_subparsers(dest="proposals_command", required=True)

    p_prop_list = proposals_sub.add_parser("list", help="List a run's proposals.")
    p_prop_list.add_argument(
        "--run", default=None, help="Run id (default: latest run)."
    )
    p_prop_list.set_defaults(func=cmd_proposals_list)

    p_prop_approve = proposals_sub.add_parser(
        "approve", help="Approve one proposal (by id) or all (--all)."
    )
    p_prop_approve.add_argument(
        "proposal_id", nargs="?", default=None, help="Proposal id to approve."
    )
    p_prop_approve.add_argument(
        "--all", action="store_true", help="Approve every proposed proposal in the run."
    )
    p_prop_approve.add_argument(
        "--run", default=None, help="Run id (default: latest run)."
    )
    p_prop_approve.set_defaults(func=cmd_proposals_approve)

    p_prop_reject = proposals_sub.add_parser(
        "reject", help="Reject one proposal with a reason."
    )
    p_prop_reject.add_argument("proposal_id", help="Proposal id to reject.")
    p_prop_reject.add_argument(
        "--reason", required=True, help="Why the proposal is rejected."
    )
    p_prop_reject.add_argument(
        "--run", default=None, help="Run id (default: latest run)."
    )
    p_prop_reject.set_defaults(func=cmd_proposals_reject)

    p_apply = subparsers.add_parser(
        "apply",
        help="Apply approved proposals through the arbitration writer.",
    )
    p_apply.add_argument("--run", default=None, help="Run id (default: latest run).")
    p_apply.set_defaults(func=cmd_apply)

    p_reconcile = subparsers.add_parser(
        "reconcile",
        help="Collapse duplicate nodes (e.g. concept metrics).",
    )
    p_reconcile.add_argument(
        "--label", default="Metric", help="Node label to reconcile (default: Metric)."
    )
    p_reconcile.add_argument(
        "--dry-run",
        action="store_true",
        help="Report duplicate clusters without merging.",
    )
    p_reconcile.set_defaults(func=cmd_reconcile)

    # --- Agentic build subcommand ------------------------------------------

    p_build = subparsers.add_parser(
        "build",
        help="Build the metric/edge layer with the LLM agentic builder "
        "(harness.agentic): an LLM reads every metric and constructs nodes + "
        "edges itself. A live run WIPES + REBUILDS the graph.",
    )
    p_build.add_argument(
        "--smoke",
        action="store_true",
        help="Build only one small namespace (the blended.* ROAS chain) and skip "
        "the destructive wipe (non-destructive subset build).",
    )
    p_build.add_argument(
        "--namespaces",
        default=None,
        help="Restrict the node phase to these source namespaces, pipe-delimited "
        "(e.g. 'google_ads|meta_ads'). Default: all.",
    )
    p_build.add_argument(
        "--resume",
        action="store_true",
        help="Resume a partial build: skip the Phase-0 wipe, skip node buckets "
        "already fully materialized, and re-run the edge phases (idempotent) to "
        "fill the gaps. Use after a rate-limited build left the graph incomplete.",
    )
    p_build.add_argument(
        "--dry-plan",
        dest="dry_plan",
        action="store_true",
        help="Print the phase plan, node slices, system prompts, and resolved "
        "ClaudeAgentOptions WITHOUT importing the SDK or touching Neo4j (offline).",
    )
    p_build.set_defaults(func=cmd_build)

    p_enrich = subparsers.add_parser(
        "enrich",
        help="Deterministically enrich metrics with mart_sources / SQL / columns / "
        "freshness (BC_2-grounded), remove causal edges that parallel a formula "
        "edge, and migrate causal edges onto the Beta evidence ledger. Additive, "
        "idempotent, no LLM.",
    )
    p_enrich.add_argument("--dry-run", action="store_true",
                          help="Compute + report only; write nothing.")
    p_enrich.add_argument("--limit", type=int, default=None,
                          help="Cap how many metrics are processed (smoke).")
    p_enrich.add_argument("--no-dedupe", action="store_true",
                          help="Skip removing causal/structural parallel edges.")
    p_enrich.add_argument("--no-migrate", action="store_true",
                          help="Skip the evidence-ledger migration.")
    p_enrich.set_defaults(func=cmd_enrich)

    p_migrate = subparsers.add_parser(
        "migrate-metric-edges",
        help="Rewrite legacy ROLLS_UP_TO / CORRELATES_WITH / CAUSES edges onto "
        "the V1 DECOMPOSES_INTO + INFLUENCES model (originals deprecated, never "
        "deleted), via arbitration.",
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the migration plan without writing or deprecating edges.",
    )
    p_migrate.set_defaults(func=cmd_migrate_metric_edges)

    p_discover = subparsers.add_parser(
        "discover",
        help="Run the causal relationship-discovery engine (optional [discovery] "
        "extra): synthetic self-test, API series scan, or PCMCI+ discovery. Writes "
        "tenant-scoped data/discovered_edges.<tenant>.csv.",
    )
    p_discover.add_argument(
        "--mode",
        choices=["synthetic", "scan", "pcmci"],
        default="synthetic",
        help="synthetic self-test (no API), API series scan, or PCMCI+ discovery "
        "(default: synthetic).",
    )
    p_discover.add_argument(
        "--test",
        default="parcorr",
        help="PCMCI+ conditional-independence test for --mode pcmci "
        "(parcorr / cmiknn / cmiknn-gpu / gpdc; default: parcorr).",
    )
    p_discover.add_argument(
        "--tau-max",
        type=int,
        default=0,
        help="Cap the max lag for --mode pcmci (smaller = tractable for slow "
        "nonlinear tests; writes a _tau<N> file). 0 = per-grain default.",
    )
    p_discover.add_argument(
        "--tenant",
        default="rare_seeds",
        help="Tenant slug — scopes the output CSV / series cache (default: "
        "rare_seeds). Never clobbers another tenant's file.",
    )
    p_discover.add_argument(
        "--base",
        default=None,
        help="API base URL for scan/pcmci (default: $TW_API_BASE or the BC_2 "
        "local http://localhost:8005).",
    )
    p_discover.set_defaults(func=cmd_discover)

    p_prune = subparsers.add_parser(
        "prune-empty",
        help="Delete Domains / chart-types that no Metric uses (post-ingest cleanup).",
    )
    p_prune.set_defaults(func=cmd_prune_empty)

    return parser


def main() -> None:
    """Console-script entry point for the ``kg`` command.

    Parses arguments, dispatches to the selected subcommand, and exits with its
    return code. :class:`CLIError` is rendered as a friendly stderr message
    (no traceback).
    """
    parser = build_parser()
    args = parser.parse_args()
    try:
        exit_code = args.func(args)
    except CLIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
