"""Causal relationship-discovery engine (optional ``[discovery]`` extra).

This package vendors the Phase-1 discovery engine — STL deseasonalization,
lagged Granger / mutual-information edge tests with Benjamini-Hochberg FDR and
stability selection, a poor-man's conditioning pass, and full PCMCI+ (tigramite)
multivariate causal discovery. Those modules pull heavy scientific dependencies
(``numpy`` / ``pandas`` / ``scikit-learn`` / ``statsmodels`` / ``tigramite``,
plus optional ``torch`` for the GPU CMIknn variant) that are intentionally NOT
core dependencies of the harness.

To keep ``import harness.discovery`` cheap and dependency-free, this
``__init__`` does **not** import :mod:`harness.discovery.discover_engine` (or any
heavy dependency) at module load. The engine entry points are imported lazily by
the consumer (the ``kg discover`` CLI command imports them inside the handler so
the friendly "extra not installed" message can be shown). Install the engine on
demand with ``uv sync --extra discovery`` (or ``--extra discovery-gpu``).
"""

from __future__ import annotations

#: The entry-point functions the CLI dispatches to once the heavy engine is
#: imported. Listed here for discoverability only — they are NOT imported at
#: package load (that would pull numpy/pandas/statsmodels/tigramite).
__all__ = ["run_synthetic", "run_scan", "run_pcmci", "run_api"]
