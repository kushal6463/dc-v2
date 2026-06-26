"""Conditioned partial-correlation layer for OBSERVATIONAL edge evidence (DEFERRED).

Design stub for the Snowflake-backed statistical layer that produces the
OBSERVATIONAL tier of the evidence ledger (FRD §5.7 / FR-SCORE-001/002). It IMPORTS
with zero heavy dependencies — ``numpy`` / ``pandas`` / ``statsmodels`` are
deliberately NOT imported at module load — and every entry point raises
:class:`NotImplementedError` until the deferred pass is built. The signatures and the
per-edge / per-event contracts below are stable; only the bodies are pending.

Intended method (PCMCI-style, on mart time-series read from Snowflake):

  1. STL-deseasonalize each metric's series -> residuals (strip trend + seasonality,
     the spurious-correlation trap that makes unrelated series look linked).
  2. For each candidate parent, compute its lagged partial correlation with ``target``
     CONDITIONED on the OTHER candidate parents (each at its own lag) + the target's
     own past — this removes common-cause confounders and autocorrelation, so a link
     that collapses once the shared drivers are regressed out is dropped as a confound.
  3. Benjamini-Hochberg FDR-adjust the p-values across ALL tested ``(parent, lag)``
     pairs (the whole batch), controlling the false-discovery rate at ``alpha_fdr``.
  4. Emit one record per surviving edge carrying the effect size, p/q values, the lag
     as an ISO-8601 duration, ``tier="observational"``, and a pseudo-count ``weight``
     in ``[2, 5]`` scaled by effect size and FDR survival.

BRIDGE NOTE — a WORKING implementation of steps 1-3 already ships:
:mod:`harness.discovery.discover_engine` runs STL / lagged Granger /
mutual-information with Benjamini-Hochberg FDR (and full PCMCI+ via tigramite) under
``--mode pcmci`` and writes ``data/discovered_edges.<tenant>.csv``. The deferred pass
does NOT reimplement that statistics; it (a) optionally re-runs the conditioned
partial correlation here against the Snowflake mart, then (b) feeds either result
through :func:`to_evidence_events` into
:func:`harness.kg.arbitration.append_edge_evidence`, landing OBSERVATIONAL events in
the SAME append-only ledger that :func:`harness.kg.evidence.fold_ledger` folds into
each ``INFLUENCES`` edge's Beta confidence. So this module is the Snowflake-native
sibling of the discovery engine, sharing one ledger and one fold.
"""

from __future__ import annotations


def conditioned_partial_correlation(
    target: str,
    candidate_parents: list[str],
    data,
    *,
    tau_max: int = 14,
    alpha_fdr: float = 0.1,
) -> dict:
    """Lagged, confounder-conditioned partial correlation of each parent on ``target``.

    DEFERRED design stub — raises :class:`NotImplementedError`. The working statistics
    ship today in :mod:`harness.discovery.discover_engine` (``--mode pcmci``); see the
    module BRIDGE NOTE.

    Intended behaviour:

    * STL-deseasonalize ``target`` and every metric in ``candidate_parents`` to
      residual series (removing trend + weekly/annual seasonality), then test each
      parent at lags ``1 .. tau_max``.
    * For each ``(parent, lag)`` compute the PARTIAL correlation of ``parent(t-lag)``
      with ``target(t)`` CONDITIONED on the other candidate parents (each at its
      selected lag) AND on ``target``'s own past (``target(t-1)``). Conditioning on
      the co-parents removes common-cause confounders; conditioning on the target's
      lag removes autocorrelation. This is the single-pass, PCMCI-style discipline
      already used by :func:`harness.discovery.discover_engine.conditional_edge`.
    * Benjamini-Hochberg FDR-adjust the partial-correlation p-values across ALL tested
      ``(parent, lag)`` pairs at level ``alpha_fdr``, yielding a q-value per pair.

    Args:
        target: ``node_id`` of the effect (downstream) metric.
        candidate_parents: ``node_id`` s of the admissible upstream metrics to test as
            lagged causes of ``target``.
        data: The metrics' mart time-series — e.g. a date-indexed wide frame (or a
            ``{node_id -> series}`` mapping) of daily/weekly values pulled from the
            Snowflake mart, one column per ``target`` / parent. (Left un-annotated so
            this stub imports without pandas; the deferred body fixes the concrete
            type.)
        tau_max: Maximum lag (in series periods) to scan; defaults to 14, matching the
            discovery engine's ``MAX_LAG``.
        alpha_fdr: Target false-discovery rate for the Benjamini-Hochberg step.

    Returns:
        A mapping keyed by parent ``node_id`` (one entry per surviving edge), each
        value a dict with:

        * ``effect_size`` -- signed conditioned partial correlation (``[-1, 1]``);
        * ``p_value`` -- raw partial-correlation p-value;
        * ``q_value`` -- Benjamini-Hochberg FDR-adjusted p-value;
        * ``lag`` -- the selected lag as an ISO-8601 duration (e.g. ``"P2D"``);
        * ``tier`` -- the literal ``"observational"``;
        * ``weight`` -- pseudo-count in ``[2, 5]`` scaled by ``abs(effect_size)`` for
          edges passing FDR (``q_value <= alpha_fdr``), shrunk toward the floor (or
          dropped) otherwise — the OBSERVATIONAL band of FRD §5.7.

    Raises:
        NotImplementedError: always (deferred layer).
    """
    raise NotImplementedError(
        "conditioned_partial_correlation is a deferred-layer design stub; the working "
        "STL / partial-correlation / BH-FDR pass ships in "
        "harness.discovery.discover_engine (run `--mode pcmci`)."
    )


def to_evidence_events(
    pcmci_result: dict,
    *,
    attribution: str,
    timestamp: str,
) -> list[dict]:
    """Map conditioned-correlation results to ledger evidence events (DEFERRED stub).

    Raises :class:`NotImplementedError`. Translates the per-edge records produced by
    :func:`conditioned_partial_correlation` (or the equivalent rows parsed from
    :mod:`harness.discovery.discover_engine`'s ``data/discovered_edges.<tenant>.csv``,
    ``--mode pcmci``) into OBSERVATIONAL evidence events shaped for
    :func:`harness.kg.arbitration.append_edge_evidence` and the deterministic fold in
    :func:`harness.kg.evidence.fold_ledger` (FRD §5.7).

    Each emitted event carries:

    * ``tier`` -- the literal ``"observational"``;
    * ``direction`` -- ``"supports"`` when ``effect_size >= 0``, else ``"refutes"``
      (a negative conditioned partial correlation is evidence AGAINST a positive
      causal link), so it adds to the edge's β rather than its α in the fold;
    * ``weight`` -- the ``[2, 5]`` pseudo-count carried on the source record;
    * ``attribution`` -- the passed source label (e.g. ``"pcmci+/parcorr"``);
    * ``timestamp`` -- the passed ISO-8601 event time;

    plus the edge identity (source/target ``node_id`` + ``lag``) and the supporting
    statistics (``effect_size`` / ``q_value``) as the auditable payload, so any folded
    confidence is traceable to the observation that produced it (FR-SCORE-002).

    Args:
        pcmci_result: The :func:`conditioned_partial_correlation` return value (or the
            parsed discovery-engine CSV rows) to convert.
        attribution: Source attribution recorded on every event (the test / run id).
        timestamp: ISO-8601 timestamp stamped on every event.

    Returns:
        A list of evidence-event dicts ready to hand to
        :func:`harness.kg.arbitration.append_edge_evidence`.

    Raises:
        NotImplementedError: always (deferred layer).
    """
    raise NotImplementedError(
        "to_evidence_events is a deferred-layer design stub; it will map "
        "conditioned_partial_correlation / discover_engine results into OBSERVATIONAL "
        "evidence events for harness.kg.arbitration.append_edge_evidence."
    )
