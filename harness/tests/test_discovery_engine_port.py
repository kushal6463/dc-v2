"""CORE-deps tests for the vendored discovery-engine port (:mod:`harness.discovery`).

The discovery engine (STL / Granger / PCMCI+) is shipped behind the optional
``[discovery]`` extra so the heavy scientific stack (numpy / pandas /
scikit-learn / statsmodels / tigramite) is NOT a core dependency. These tests
must therefore pass with CORE deps only — none of them install or import the
extra. They assert three port invariants:

1. ``import harness.discovery`` succeeds WITHOUT pulling in numpy / pandas /
   tigramite — i.e. the package ``__init__`` is lazy and never imports
   :mod:`harness.discovery.discover_engine` at module load.
2. The ``discover`` subcommand is registered in
   :func:`harness.cli.kg.build_parser` with the expected modes/defaults.
3. :func:`harness.cli.kg.cmd_discover` surfaces a friendly
   :class:`~harness.cli.kg.CLIError` (the "extra not installed" install hint)
   when the engine import fails — never a raw traceback.
"""

from __future__ import annotations

import argparse
import builtins
import importlib
import sys

import pytest

from harness.cli import kg as kg_cli

#: Heavy modules the engine pulls but the package __init__ must NOT — proves the
#: lazy boundary holds even on a machine where these happen to be installed.
_HEAVY_MODULES = ("numpy", "pandas", "sklearn", "statsmodels", "tigramite", "torch")


def test_import_harness_discovery_needs_no_heavy_deps() -> None:
    """``import harness.discovery`` works with core deps and stays lazy.

    The import itself must succeed (it does no heavy work), and it must not have
    imported the engine module or any heavy scientific dependency as a side
    effect — those only load when the consumer asks for the engine.
    """
    # Drop any cached copies so the import below actually re-executes __init__.
    for name in ("harness.discovery", "harness.discovery.discover_engine"):
        sys.modules.pop(name, None)

    # A heavy module may ALREADY be in sys.modules because an earlier test (or the
    # installed [discovery] extra) loaded it — the lazy-boundary assertion is about
    # what THIS import ADDS as a side effect, not the absolute process state.
    preloaded = {mod for mod in _HEAVY_MODULES if mod in sys.modules}

    pkg = importlib.import_module("harness.discovery")
    assert pkg is not None
    # __all__ advertises the entry points without importing them.
    assert set(pkg.__all__) >= {"run_synthetic", "run_scan", "run_pcmci"}

    # The lazy boundary: the heavy engine module is NOT imported by __init__,
    # and importing the package pulls in NO new heavy scientific dependency.
    assert "harness.discovery.discover_engine" not in sys.modules
    for mod in _HEAVY_MODULES:
        if mod in preloaded:
            continue  # already loaded elsewhere; not a side effect of THIS import
        assert mod not in sys.modules, f"{mod} must not be imported by harness.discovery"


def test_discover_subcommand_registered() -> None:
    """The ``discover`` subcommand is wired into the parser with sane defaults."""
    parser = kg_cli.build_parser()

    args = parser.parse_args(["discover"])
    assert args.func is kg_cli.cmd_discover
    assert args.command == "discover"
    # Defaults match the spec (synthetic / parcorr / rare_seeds, env-driven base).
    assert args.mode == "synthetic"
    assert args.test == "parcorr"
    assert args.tau_max == 0
    assert args.tenant == "rare_seeds"
    assert args.base is None

    # The mode choices are exactly synthetic|scan|pcmci.
    parsed = parser.parse_args(["discover", "--mode", "pcmci", "--tenant", "acme"])
    assert parsed.mode == "pcmci"
    assert parsed.tenant == "acme"
    with pytest.raises(SystemExit):
        parser.parse_args(["discover", "--mode", "bogus"])


def test_cmd_discover_friendly_error_when_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cmd_discover raises a friendly CLIError when the engine import fails.

    Simulates the ``[discovery]`` extra being absent by making the import of
    :mod:`harness.discovery.discover_engine` raise ``ImportError``; the handler
    must catch it and re-raise :class:`CLIError` with the install hint (no raw
    ImportError / traceback leaking to the user).
    """
    # Ensure the import inside cmd_discover actually executes (not served from
    # a previously cached module), then force it to fail.
    sys.modules.pop("harness.discovery.discover_engine", None)
    real_import = builtins.__import__

    def _fail_import(name, globals=None, locals=None, fromlist=(), level=0):
        # cmd_discover does `from harness.discovery import discover_engine`, which
        # resolves to __import__("harness.discovery", fromlist=["discover_engine"]).
        if name == "harness.discovery.discover_engine" or (
            name == "harness.discovery" and "discover_engine" in (fromlist or ())
        ):
            raise ImportError("No module named 'numpy'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fail_import)

    args = argparse.Namespace(
        mode="synthetic", test="parcorr", tau_max=0, tenant="rare_seeds", base=None
    )
    with pytest.raises(kg_cli.CLIError) as excinfo:
        kg_cli.cmd_discover(args)
    assert "discovery extra not installed" in str(excinfo.value)
    assert "uv sync --extra discovery" in str(excinfo.value)
