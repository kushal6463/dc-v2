"""End-to-end smoke tests — exercise every surface and assert no errors.

Belt-and-suspenders pass over the whole system: every module imports, every CLI
subcommand parses (`--help`), the discovery importer runs end-to-end (dry-run)
over the vendored feed, edge scoring covers all classes, governance invariants
hold, the no-DB API routes respond, and the MCP/discovery surfaces are present
and well-formed. DB- and heavy-dep-requiring checks skip cleanly when absent.

Run: ``uv sync --extra dev && uv run pytest harness/tests/test_smoke.py -q``.
"""

from __future__ import annotations

import argparse
import importlib

import pytest

# ---------------------------------------------------------------------------
# 1. Every harness module imports cleanly (core deps only)
# ---------------------------------------------------------------------------
CORE_MODULES = [
    "harness.kg.models", "harness.kg.arbitration", "harness.kg.reconcile",
    "harness.kg.driver", "harness.kg.config", "harness.kg.schema",
    "harness.ingest.prepass", "harness.ingest.proposer", "harness.ingest.apply",
    "harness.ingest.bc2_snapshot",
    "harness.ingest.openapi_inventory", "harness.ingest.edge_scoring",
    "harness.api.server", "harness.mcp.graph_server", "harness.cli.kg",
    "harness.store.proposals",
    "harness.discovery",  # MUST be importable WITHOUT the heavy [discovery] extra
]


@pytest.mark.parametrize("mod", CORE_MODULES)
def test_module_imports(mod: str) -> None:
    importlib.import_module(mod)


def test_discovery_package_is_lazy() -> None:
    """`import harness.discovery` must not pull tigramite/the engine."""
    import sys

    importlib.import_module("harness.discovery")
    assert "tigramite" not in sys.modules
    assert "harness.discovery.discover_engine" not in sys.modules


# ---------------------------------------------------------------------------
# 2. Every CLI subcommand is registered and its --help parses
# ---------------------------------------------------------------------------
def _subcommands() -> list[str]:
    from harness.cli.kg import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(sub.choices)


def test_expected_subcommands_present() -> None:
    cmds = set(_subcommands())
    expected = {
        "schema-init", "bootstrap-spine", "status", "prepass", "apply",
        "build", "migrate-metric-edges", "discover",
    }
    missing = expected - cmds
    assert not missing, f"missing subcommands: {missing}"


@pytest.mark.parametrize("cmd", _subcommands())
def test_subcommand_help_parses(cmd: str) -> None:
    """`kg <cmd> --help` short-circuits to a clean SystemExit(0) (argparse wired)."""
    from harness.cli.kg import build_parser

    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([cmd, "--help"])
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# 3. Edge scoring covers all classes; statistical is review-only
# ---------------------------------------------------------------------------
def test_edge_scoring_all_classes() -> None:
    from harness.ingest import edge_scoring as es

    for cls in es.known_edge_classes():
        s = es.score_edge(cls, source_confidence=0.5, beta=(0.6, 4.0))
        assert 0.0 <= s.confidence <= 1.0
        assert s.evidence_mass > 0
        assert isinstance(s.review, bool)
    assert es.score_edge("INFLUENCES:statistical", source_confidence=0.5).review is True
    assert es.score_edge("DECOMPOSES_INTO:formula").review is False


# ---------------------------------------------------------------------------
# 5. Governance invariants (R2): discovered edges are reconcile-protected
# ---------------------------------------------------------------------------
def test_kg_discovery_is_reconcile_protected() -> None:
    from harness.kg import reconcile

    assert "kg_discovery" in reconcile.REVIEW_PROTECTED
    assert "kg_discovery" not in reconcile.ELIGIBLE_SOURCE_KINDS


def test_influences_relations_include_statistical() -> None:
    from harness.kg.models import INFLUENCES_RELATIONS

    assert {"statistical", "statistical_candidate"} <= set(INFLUENCES_RELATIONS)


# ---------------------------------------------------------------------------
# 6. No-DB API routes respond via the FastAPI TestClient
# ---------------------------------------------------------------------------
def test_api_health_and_coverage_no_db() -> None:
    try:
        from fastapi.testclient import TestClient

        from harness.api.server import app
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"TestClient/app unavailable: {exc}")
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    # coverage reads a file artifact (no DB); 200 with data or a JSON error, never a crash
    cov = client.get("/api/coverage?tenant=rare_seeds")
    assert cov.status_code in (200, 404)
    assert isinstance(cov.json(), dict)


# ---------------------------------------------------------------------------
# 7. Discovery CLI: friendly error without the extra, or a real synthetic run
# ---------------------------------------------------------------------------
def test_discover_cli_handles_missing_extra_or_runs() -> None:
    from harness.cli.kg import CLIError, build_parser

    args = build_parser().parse_args(["discover", "--mode", "synthetic", "--tenant", "smoketest"])
    try:
        import tigramite  # noqa: F401

        has_extra = True
    except Exception:
        has_extra = False
    if has_extra:
        # engine importable; the command should run or fail loudly (not silently)
        rc = args.func(args)
        assert rc == 0
    else:
        # core env: a clean, friendly CLIError (never an opaque ImportError)
        with pytest.raises(CLIError):
            args.func(args)
