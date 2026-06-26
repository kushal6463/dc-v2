"""NO-DB tests for the deferred conditioned partial-correlation stub.

:mod:`harness.stats.correlation` is the design stub for the Snowflake-backed
OBSERVATIONAL evidence layer (FRD §5.7 / FR-SCORE-001/002). Its contract is that
it (a) imports with *zero* heavy dependencies -- ``numpy`` / ``pandas`` /
``statsmodels`` are deliberately not imported at module load -- and (b) every
entry point raises :class:`NotImplementedError` until the deferred pass is built.
These tests pin exactly that: a clean import, a stable signature (schema-fit),
and the deferred ``NotImplementedError`` on both entry points.

Pure: nothing here touches Neo4j, Snowflake, or any statistical dependency.
"""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys

import pytest

from harness.stats import correlation


# ---------------------------------------------------------------------------
# Import smoke + signature schema-fit
# ---------------------------------------------------------------------------


def test_module_imports_cleanly_and_fits_schema() -> None:
    """The stub imports and exposes its two entry points with their stable signatures."""
    mod = importlib.import_module("harness.stats.correlation")
    assert callable(mod.conditioned_partial_correlation)
    assert callable(mod.to_evidence_events)

    # conditioned_partial_correlation: positional (target, candidate_parents, data)
    # plus the keyword-only tau_max / alpha_fdr defaults documented in the stub.
    cpc = inspect.signature(mod.conditioned_partial_correlation)
    assert list(cpc.parameters)[:3] == ["target", "candidate_parents", "data"]
    assert cpc.parameters["tau_max"].default == 14
    assert cpc.parameters["alpha_fdr"].default == pytest.approx(0.1)

    # to_evidence_events: a result plus keyword-only attribution / timestamp.
    tee = inspect.signature(mod.to_evidence_events)
    assert "attribution" in tee.parameters
    assert "timestamp" in tee.parameters


def test_import_pulls_no_heavy_dependencies() -> None:
    """Importing the stub in a clean interpreter loads no numpy/pandas/statsmodels/scipy."""
    code = (
        "import sys; import harness.stats.correlation;"
        "bad=[m for m in ('numpy','pandas','statsmodels','scipy') if m in sys.modules];"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Deferred entry points raise NotImplementedError
# ---------------------------------------------------------------------------


def test_conditioned_partial_correlation_raises_not_implemented() -> None:
    """The deferred conditioned partial-correlation pass raises until it is built."""
    with pytest.raises(NotImplementedError):
        correlation.conditioned_partial_correlation(
            "metric:target", ["metric:parent_a", "metric:parent_b"], None
        )


def test_to_evidence_events_raises_not_implemented() -> None:
    """The deferred result-to-ledger mapping raises until it is built."""
    with pytest.raises(NotImplementedError):
        correlation.to_evidence_events(
            {}, attribution="pcmci+/parcorr", timestamp="2026-06-25T00:00:00Z"
        )
