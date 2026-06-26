"""Deterministic metric->metric edge *candidates* derived from mart lineage.

This is a **pure, zero-IO-side-effect** producer (stdlib only — ``re`` /
``pathlib`` / ``collections``; no DB, no network, no LLM). It reads dbt mart
``*.sql`` files and a list of metric records and emits *candidate* edges that a
later enrichment phase scores, validates, and (maybe) promotes to real
``INFLUENCES`` / ``DECOMPOSES_INTO`` edges. Nothing here writes to Neo4j or
mutates state; every function returns plain dicts/lists/sets.

Three independent bases produce candidates:

* ``"shared_mart"`` — two metrics read the same canonical mart (unordered).
* ``"shared_column"`` — two metrics read the same source column (unordered).
* ``"mart_lineage"`` — metric ``A``'s mart ``ref()``-depends on metric ``B``'s
  mart, per the parsed dbt graph (directed ``A -> B``).

Every candidate carries ``cross_domain`` — ``True`` when the two metrics share
no domain (set-disjoint ``domain_ids``; note metrics with empty ``domain_ids``
are disjoint from everything, hence ``cross_domain == True``).

To bound combinatorial blow-up, a mart/column shared by *more than*
``hub_cap`` metrics is treated as a non-discriminating "hub": it yields no pairs
and is reported separately as a skipped hub so the caller can see what was
dropped (and why) without it flooding the candidate list.

Each metric record is a mapping with the keys::

    {
        "metric_uid":      str,        # stable id, used for from/to
        "mart_sources":    list[str],  # raw mart identifiers (normalized here)
        "source_columns":  list[str],  # mart column names (used verbatim)
        "domain_ids":      list[str],  # FRD domain ids (for cross_domain)
    }

Missing/``None`` list fields are tolerated (treated as empty), and duplicate
``metric_uid`` records collapse to the first occurrence.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Leading ``DB_<TENANT>.`` qualifier (Snowflake-style, uppercase) stripped by
#: :func:`normalize_mart`. ``<TENANT>`` is ``[A-Z0-9_]+`` and the match stops at
#: the first ``.`` (dot is outside the class), so only the database qualifier is
#: removed — the schema/table that follow are left for the rsplit step.
_DB_PREFIX_RE = re.compile(r"^DB_[A-Z0-9_]+\.")

#: dbt ``ref('model')`` / ``ref("model")`` (and the optional two-arg
#: ``ref('package', 'model')``) call. ``\bref`` avoids matching ``xref(`` and the
#: optional non-capturing group swallows a leading package arg so the single
#: capture group is always the *model* name. Case-sensitive on purpose: dbt's
#: ``ref`` is lowercase, so a commented ``-- REF(...)`` never matches.
_REF_RE = re.compile(r"""\bref\s*\(\s*(?:['"][^'"]+['"]\s*,\s*)?['"]([^'"]+)['"]\s*\)""")


def normalize_mart(name: str) -> str:
    """Return the canonical ``MARTS.<table>`` form of a raw mart identifier.

    The rule, applied in order:

    1. Strip a leading ``DB_<TENANT>.`` database qualifier via
       :data:`_DB_PREFIX_RE` (``^DB_[A-Z0-9_]+\\.``). Only the tenant database is
       removed; any schema/table qualifier that follows is untouched here.
    2. Take the final dotted segment as the *table* (this drops the remaining
       schema qualifier, e.g. ``MARTS.``), so the canonical schema is always the
       literal ``MARTS`` regardless of the input's own schema.
    3. Lowercase the table and prefix it with ``MARTS.``.

    Examples:
        ``DB_RARE_SEEDS.MARTS.mart_x`` -> ``MARTS.mart_x``;
        ``MART_GOOGLE_AD_PERFORMANCE`` -> ``MARTS.mart_google_ad_performance``;
        ``creative_performance`` -> ``MARTS.creative_performance``.

    Args:
        name: A raw mart identifier (fully qualified, schema-qualified, or a
            bare table/file-stem name).

    Returns:
        The canonical ``MARTS.<table>`` identifier (table lowercased).
    """
    stripped = _DB_PREFIX_RE.sub("", name.strip())
    table = stripped.rsplit(".", 1)[-1]
    return f"MARTS.{table.lower()}"


def parse_mart_refs(marts_dir: Path) -> dict[str, set[str]]:
    """Parse mart->mart (``ref()``) lineage from dbt ``*.sql`` files.

    Each ``*.sql`` file found under ``marts_dir`` (recursively, via ``rglob``)
    contributes one mart, keyed by :func:`normalize_mart` of its file stem. Its
    value is the set of upstream marts it ``ref()``-depends on: every
    ``ref('...')`` / ``ref("...")`` target whose **raw** name starts with
    ``mart_`` (mart->mart edges only — non-mart refs and ``source()`` calls are
    ignored), each normalized. Self-references are dropped (no self-loops).

    Every discovered mart appears as a key even when it has no upstream marts
    (its value is then an empty set), so the result doubles as the mart
    inventory. Files whose stems normalize to the same mart are merged (union).

    Args:
        marts_dir: Directory containing the dbt mart SQL files.

    Returns:
        ``{normalized_mart: {normalized_upstream_marts}}``. A missing or
        non-directory ``marts_dir`` yields ``{}``.
    """
    if not marts_dir.is_dir():
        return {}

    refs: dict[str, set[str]] = defaultdict(set)
    for sql_path in sorted(marts_dir.rglob("*.sql")):
        mart = normalize_mart(sql_path.stem)
        upstream = refs[mart]  # ensures the mart is a key even with no refs
        text = sql_path.read_text(encoding="utf-8")
        for target in _REF_RE.findall(text):
            if not target.startswith("mart_"):
                continue
            normalized = normalize_mart(target)
            if normalized != mart:  # skip self-reference
                upstream.add(normalized)
    return dict(refs)


@dataclass(frozen=True)
class _MetricView:
    """Normalized, hashable projection of one metric record (internal)."""

    uid: str
    #: Canonical ``MARTS.<table>`` marts this metric reads.
    marts: frozenset[str]
    #: Verbatim source column names this metric reads.
    columns: frozenset[str]
    #: FRD domain ids this metric belongs to (for ``cross_domain``).
    domains: frozenset[str]


def _metric_views(metrics: Sequence[Mapping[str, Any]]) -> list[_MetricView]:
    """Project metric records into deduped, normalized :class:`_MetricView`s.

    Marts are normalized via :func:`normalize_mart`; columns/domains are kept
    verbatim. Empty/``None`` entries are dropped and duplicate ``metric_uid``
    records collapse to their first occurrence (stable).
    """
    views: dict[str, _MetricView] = {}
    for metric in metrics:
        uid = metric["metric_uid"]
        if uid in views:
            continue
        views[uid] = _MetricView(
            uid=uid,
            marts=frozenset(normalize_mart(s) for s in (metric.get("mart_sources") or []) if s),
            columns=frozenset(c for c in (metric.get("source_columns") or []) if c),
            domains=frozenset(d for d in (metric.get("domain_ids") or []) if d),
        )
    return list(views.values())


def _cross_domain(a: _MetricView, b: _MetricView) -> bool:
    """Return ``True`` when ``a`` and ``b`` share no domain (set-disjoint)."""
    return a.domains.isdisjoint(b.domains)


def _shared_candidates(
    metrics: Sequence[Mapping[str, Any]],
    *,
    basis: str,
    key_of: Callable[[_MetricView], Iterable[str]],
    hub_cap: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build unordered "shared X" pair candidates grouped by a per-metric key.

    Generic engine behind :func:`shared_mart_candidates` /
    :func:`shared_column_candidates`. Metrics are grouped by each key returned
    from ``key_of``; every key shared by ``2..=hub_cap`` distinct metrics emits
    one candidate per unordered metric pair, while a key shared by *more than*
    ``hub_cap`` metrics is skipped and reported as a hub.

    Returns:
        ``(candidates, skipped_hubs)`` where each candidate is
        ``{"from", "to", "basis", "via", "cross_domain"}`` (``from < to``) and
        each skipped hub is ``{"basis", "via", "metric_count"}``.
    """
    groups: dict[str, list[_MetricView]] = defaultdict(list)
    for view in _metric_views(metrics):
        for key in key_of(view):
            groups[key].append(view)

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda v: v.uid)
        if len(members) > hub_cap:
            skipped.append({"basis": basis, "via": key, "metric_count": len(members)})
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                candidates.append(
                    {
                        "from": a.uid,
                        "to": b.uid,
                        "basis": basis,
                        "via": key,
                        "cross_domain": _cross_domain(a, b),
                    }
                )
    return candidates, skipped


def shared_mart_candidates(
    metrics: Sequence[Mapping[str, Any]],
    *,
    hub_cap: int = 12,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair metrics that read the same canonical mart (``basis="shared_mart"``).

    Args:
        metrics: Metric records (see module docstring for the expected shape).
        hub_cap: A mart shared by more than this many distinct metrics is
            treated as a non-discriminating hub: it emits no pairs and is
            returned among the skipped hubs instead.

    Returns:
        ``(candidates, skipped_hubs)``. Each candidate is an unordered pair
        ``{"from", "to", "basis": "shared_mart", "via": <mart>, "cross_domain"}``
        with ``from < to``; each skipped hub is
        ``{"basis": "shared_mart", "via": <mart>, "metric_count": int}``.
    """
    return _shared_candidates(
        metrics, basis="shared_mart", key_of=lambda v: v.marts, hub_cap=hub_cap
    )


def shared_column_candidates(
    metrics: Sequence[Mapping[str, Any]],
    *,
    hub_cap: int = 12,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair metrics that read the same source column (``basis="shared_column"``).

    Args:
        metrics: Metric records (see module docstring for the expected shape).
        hub_cap: A column shared by more than this many distinct metrics is
            treated as a non-discriminating hub: it emits no pairs and is
            returned among the skipped hubs instead.

    Returns:
        ``(candidates, skipped_hubs)``. Each candidate is an unordered pair
        ``{"from", "to", "basis": "shared_column", "via": <column>,
        "cross_domain"}`` with ``from < to``; each skipped hub is
        ``{"basis": "shared_column", "via": <column>, "metric_count": int}``.
    """
    return _shared_candidates(
        metrics, basis="shared_column", key_of=lambda v: v.columns, hub_cap=hub_cap
    )


def lineage_candidates(
    metrics: Sequence[Mapping[str, Any]],
    mart_refs: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    """Directed candidates from dbt mart->mart lineage (``basis="mart_lineage"``).

    For every ordered pair of distinct metrics ``(A, B)``, if some mart ``M_a``
    read by ``A`` ``ref()``-depends on some mart ``M_b`` read by ``B`` (i.e.
    ``M_b in mart_refs[M_a]``), then ``A`` depends on ``B`` and a directed
    candidate ``A -> B`` is emitted. Both marts are reported in ``via`` as
    ``[M_a, M_b]`` (the dependent mart first, the dependency second).

    Args:
        metrics: Metric records (see module docstring for the expected shape).
        mart_refs: Mart lineage as returned by :func:`parse_mart_refs`
            (``{mart: {upstream_marts}}``), with canonical ``MARTS.<table>`` keys.

    Returns:
        Directed candidates ``{"from", "to", "basis": "mart_lineage",
        "via": [M_a, M_b], "cross_domain"}``.
    """
    views = _metric_views(metrics)
    candidates: list[dict[str, Any]] = []
    for a in views:
        for b in views:
            if a.uid == b.uid:
                continue
            for m_a in sorted(a.marts):
                upstream = mart_refs.get(m_a)
                if not upstream:
                    continue
                for m_b in sorted(b.marts):
                    if m_b in upstream:
                        candidates.append(
                            {
                                "from": a.uid,
                                "to": b.uid,
                                "basis": "mart_lineage",
                                "via": [m_a, m_b],
                                "cross_domain": _cross_domain(a, b),
                            }
                        )
    return candidates


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """Hashable, sortable identity of a candidate (for dedup + stable order)."""
    via = candidate["via"]
    via_key = tuple(via) if isinstance(via, list) else (via,)
    return (candidate["basis"], candidate["from"], candidate["to"], via_key)


def generate_candidates(
    metrics: Sequence[Mapping[str, Any]],
    marts_dir: Path,
    *,
    hub_cap: int = 12,
) -> dict[str, Any]:
    """Run all producers and return the merged, deterministic candidate set.

    Orchestrates :func:`parse_mart_refs`, :func:`shared_mart_candidates`,
    :func:`shared_column_candidates`, and :func:`lineage_candidates`, then
    unions the candidates (dedup by ``(basis, from, to, via)`` and sorted for
    reproducibility). Because ``basis`` is part of the dedup key, the three
    producers never collide, so the per-producer ``counts`` sum to the size of
    the returned candidate list.

    Args:
        metrics: Metric records (see module docstring for the expected shape).
        marts_dir: Directory of dbt mart ``*.sql`` files (a missing directory
            simply yields no lineage candidates).
        hub_cap: Hub threshold forwarded to the shared-mart/column producers.

    Returns:
        ``{"candidates": [...deduped, sorted union...], "skipped_hubs": [...],
        "counts": {"shared_mart", "shared_column", "lineage", "skipped"}}``.
    """
    mart_refs = parse_mart_refs(marts_dir)
    shared_marts, mart_hubs = shared_mart_candidates(metrics, hub_cap=hub_cap)
    shared_columns, column_hubs = shared_column_candidates(metrics, hub_cap=hub_cap)
    lineage = lineage_candidates(metrics, mart_refs)

    skipped_hubs = sorted(mart_hubs + column_hubs, key=lambda h: (h["basis"], h["via"]))

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for candidate in (*shared_marts, *shared_columns, *lineage):
        deduped.setdefault(_candidate_key(candidate), candidate)
    candidates = [deduped[key] for key in sorted(deduped)]

    return {
        "candidates": candidates,
        "skipped_hubs": skipped_hubs,
        "counts": {
            "shared_mart": len(shared_marts),
            "shared_column": len(shared_columns),
            "lineage": len(lineage),
            "skipped": len(skipped_hubs),
        },
    }
