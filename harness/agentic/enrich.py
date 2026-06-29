"""Deterministic mart / SQL / freshness enrichment for metric nodes.

Pure, **no-LLM** helpers that derive a metric's backing mart tables, its real
backend SQL, source columns, and data-freshness window from the local catalogs
(``data/metric_nodes.rare_seeds.json`` + ``metric_registry`` CSV) and the
sibling ``BC_2`` repo. These populate ``Metric.mart_sources`` / ``sql_query_real``
/ ``source_columns`` / ``history_*`` / ``data_stale`` WITHOUT any model call.

**Mart binding (BC_2-informed).** A metric's ``source_code_ref`` names the
backend repository file that computes it; that file's repository class declares
its mart(s) authoritatively as ``MART_NAME = "..."`` (94.6% of repos) plus
``_*_TABLE``/module constants/inline ``MART_*`` literals for multi-mart repos.
So we scan the WHOLE referenced file (not just the cited line-slice) for every
mart-shaped token and keep the ones that exist in the real **dbt mart
inventory** (``BC_2/dbt/models/marts/**/*.sql``) — that inventory filter turns
the broad scan into a precise result. Metrics with no resolvable repo file fall
back to ``DASHBOARD_MART_MAPPING`` (``query_builder.py``) via their dashboards.

Also hosts the mart-derived edge-candidate filter
(:func:`filter_structural_dups` / :func:`build_enrich_candidates`) that drops
candidates already linked by a structural ``DECOMPOSES_INTO`` edge.

Everything except :func:`run_deterministic_enrich` (which lazily acquires the DB)
is pure and unit-testable: file reads only — no Neo4j, no network, no LLM.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from harness.marts import lineage

#: Repo roots resolved relative to this file (``.../dc-kg/harness/agentic``).
_DCKG_ROOT: Path = Path(__file__).resolve().parents[2]
_BC2_ROOT: Path = _DCKG_ROOT.parent / "BC_2"
_CATALOG: Path = _DCKG_ROOT / "data" / "metric_nodes.rare_seeds.json"
_REGISTRY: Path = _DCKG_ROOT / "data" / "metric_registry.rare_seeds.csv"
_MARTS_DIR: Path = _BC2_ROOT / "dbt" / "models" / "marts"
_QUERY_BUILDER: Path = _BC2_ROOT / "backend" / "app" / "core" / "query_builder.py"

#: A metric whose latest data is older than this many days is flagged stale.
STALE_SLA_DAYS: int = 7

#: Any mart table token — uppercase ``MART_<NAME>``, lowercase ``mart_<name>``,
#: or ``MARTS.<table>`` — optionally ``DB_<TENANT>.``-qualified, matched ANYWHERE
#: (FROM clauses, ``MART_NAME`` class attrs, module constants, f-string-var
#: assignments). ``\bMART_``/``\bmart_`` excludes vars like ``GOOGLE_MART``;
#: spurious hits are removed by the dbt-inventory filter in
#: :func:`enrich_metric_fields`.
_ANY_MART_RE = re.compile(
    r"\b(?:DB_[A-Z0-9_]+\.)?(?:MARTS\.)?(MART_[A-Z0-9_]+|mart_[a-z0-9_]+)\b"
)
#: Aggregate-wrapped column args (heuristic source columns).
_AGG_RE = re.compile(
    r"\b(?:SUM|COUNT|AVG|MIN|MAX|MEDIAN|PERCENTILE_CONT)\s*\(\s*(?:DISTINCT\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
#: SQL types / functions / keywords that must NOT be treated as columns — e.g.
#: ``CAST(x AS FLOAT)`` must not yield ``FLOAT``, ``NVL(...)`` not ``NVL``. Keeps
#: source_columns (and the shared-column / column-impact logic) meaningful.
_COLUMN_STOPWORDS: frozenset[str] = frozenset({
    "FLOAT", "INT", "INTEGER", "BIGINT", "SMALLINT", "NUMERIC", "DECIMAL", "DOUBLE",
    "NUMBER", "REAL", "VARCHAR", "CHAR", "STRING", "TEXT", "BOOLEAN", "BOOL", "DATE",
    "DATETIME", "TIMESTAMP", "TIME", "NULL", "TRUE", "FALSE", "NVL", "COALESCE",
    "NULLIF", "IFNULL", "IFF", "CAST", "CASE", "WHEN", "THEN", "ELSE", "END", "SUM",
    "COUNT", "AVG", "MIN", "MAX", "MEDIAN", "ROUND", "ABS", "CEIL", "FLOOR",
    "GREATEST", "LEAST", "DISTINCT", "DATEDIFF", "DATE_TRUNC", "EXTRACT", "AND", "OR",
    "NOT", "AS", "IN", "IS", "LIKE", "BETWEEN", "FROM", "WHERE", "SELECT", "JOIN",
    "ON", "GROUP", "ORDER", "BY", "ASC", "DESC", "LIMIT", "OVER", "PARTITION",
    "WITHIN", "FILTER", "DIV", "MOD", "ROW", "ROWS",
})


def _clean_columns(raw: Any) -> list[str]:
    """Upper-case, drop SQL keywords/types/functions + junk, dedupe, sort.

    Accepts any iterable of candidate tokens; keeps only real-looking columns: a
    2+-char identifier containing a letter, not in :data:`_COLUMN_STOPWORDS`.
    """
    out: set[str] = set()
    for token in raw or []:
        col = str(token).strip().upper()
        if len(col) < 2 or col in _COLUMN_STOPWORDS:
            continue
        if not any(ch.isalpha() for ch in col):
            continue
        out.add(col)
    return sorted(out)
#: ``file.py:start-end`` (or bare ``:start-end`` reusing the previous file).
_SLICE_RE = re.compile(r"([A-Za-z0-9_./-]+\.py)?\s*:(\d+)-(\d+)")
#: ``FROM {VAR}`` / ``{self.MART}`` parameterized table placeholder.
_FROM_VAR_RE = re.compile(r"\b(?:FROM|JOIN)\s+\{([\w.]+)\}", re.IGNORECASE)
#: ``GOOGLE_MART = "MART_..."`` (or ``= f"{SCHEMA}.MART_..."``) constant/dict
#: assignment naming a mart. The optional ``[a-zA-Z]`` swallows an f/r/b string
#: prefix so f-string mart constants resolve.
_ASSIGN_MART_RE = re.compile(
    r"""([\w.]+)\s*[:=]\s*[a-zA-Z]?['"]([^'"]*MART[A-Za-z0-9_.{}]*)['"]"""
)
#: A ``"dashboard-slug": "mart_table"`` entry inside ``DASHBOARD_MART_MAPPING``.
_DASH_MART_RE = re.compile(r"""['"]([\w-]+)['"]\s*:\s*['"](mart_[a-z0-9_]+)['"]""")
#: The repository class's primary mart, ``MART_NAME = "..."`` (base-class binding).
_MART_NAME_RE = re.compile(r"""MART_NAME\s*=\s*['"]([^'"]+)['"]""")


# ---------------------------------------------------------------------------
# Pure token extraction
# ---------------------------------------------------------------------------


def marts_from_text(text: str | None) -> list[str]:
    """Return every mart table token in ``text``, normalized + de-duplicated.

    Scans for uppercase ``MART_<NAME>``, lowercase ``mart_<name>``, and
    ``MARTS.<table>`` tokens (optionally DB-qualified) — wherever they appear,
    so it catches ``FROM`` clauses, ``MART_NAME`` class attributes, module
    constants, and assignment literals alike. Each hit is canonicalized via
    :func:`harness.marts.lineage.normalize_mart`. A falsy ``text`` yields ``[]``.
    """
    if not text:
        return []
    return sorted({lineage.normalize_mart(tok) for tok in _ANY_MART_RE.findall(text)})


def extract_marts_from_sql(sql_real: str | None) -> list[str]:
    """Mart tokens referenced by a SQL string (thin alias of :func:`marts_from_text`)."""
    return marts_from_text(sql_real)


def extract_source_columns_from_sql(sql_real: str | None) -> list[str]:
    """Best-effort heuristic list of aggregated source columns in a SQL string."""
    if not sql_real:
        return []
    return _clean_columns(_AGG_RE.findall(sql_real))


# ---------------------------------------------------------------------------
# BC_2 source assembly
# ---------------------------------------------------------------------------


def read_real_sql(source_code_ref: str | None, *, bc2_root: Path = _BC2_ROOT) -> str:
    """Assemble a metric's real backend SQL from its ``source_code_ref`` slices.

    ``source_code_ref`` looks like ``"backend/app/repositories/foo.py:432-489
    (fn), :924-956 (fn)"``: a file named once, then ``start-end`` line ranges (a
    bare ``:a-b`` reuses the previously-named file). Each range is read from
    ``bc2_root`` and concatenated; missing files/ranges are skipped (best-effort,
    so an unresolved ref yields ``""``).
    """
    if not source_code_ref:
        return ""
    parts: list[str] = []
    last_file: str | None = None
    for match in _SLICE_RE.finditer(source_code_ref):
        named, start_s, end_s = match.group(1), match.group(2), match.group(3)
        if named:
            last_file = named
        if not last_file:
            continue
        path = bc2_root / last_file
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        start, end = int(start_s), int(end_s)
        slice_text = "\n".join(lines[max(start - 1, 0) : end])
        if slice_text.strip():
            parts.append(f"# {last_file}:{start}-{end}\n{slice_text}")
    return "\n\n".join(parts)


def referenced_files_text(
    source_code_ref: str | None, *, bc2_root: Path = _BC2_ROOT
) -> str:
    """Return the FULL text of every distinct ``.py`` file named in a ref.

    Unlike :func:`read_real_sql` (only the cited ranges), this reads each
    referenced repository file whole — so class-level ``MART_NAME`` / module
    constants that sit outside the slice are visible to the mart scan.
    """
    if not source_code_ref:
        return ""
    texts: list[str] = []
    seen: set[str] = set()
    for match in _SLICE_RE.finditer(source_code_ref):
        named = match.group(1)
        if not named or named in seen:
            continue
        seen.add(named)
        path = bc2_root / named
        if path.is_file():
            try:
                texts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(texts)


def resolve_mart_vars(slice_sql: str | None, file_text: str | None) -> list[str]:
    """Resolve ``FROM {VAR}`` placeholders to marts via file constants."""
    used = {v.split(".")[-1] for v in _FROM_VAR_RE.findall(slice_sql or "")}
    if not used:
        return []
    assigns: dict[str, str] = {}
    for name, value in _ASSIGN_MART_RE.findall(file_text or ""):
        assigns[name.split(".")[-1]] = value
    resolved: set[str] = set()
    for var in used:
        value = assigns.get(var)
        if value and "mart" in value.lower():
            resolved.add(lineage.normalize_mart(value))
    return sorted(resolved)


# ---------------------------------------------------------------------------
# Catalog / registry / dbt-inventory / dashboard-map loaders
# ---------------------------------------------------------------------------


def load_catalog(path: Path = _CATALOG) -> dict[str, dict[str, Any]]:
    """Load ``{metric_id: catalog_entry}`` (merging ``metrics`` + ``input_nodes``)."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    merged: dict[str, dict[str, Any]] = {}
    merged.update(data.get("metrics", {}))
    merged.update(data.get("input_nodes", {}))
    return merged


def load_registry_index(path: Path = _REGISTRY) -> dict[str, dict[str, str]]:
    """Load ``{node_id: row}`` from the metric-registry CSV (``{}`` if absent)."""
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["node_id"]: row for row in csv.DictReader(fh) if row.get("node_id")}


#: Graph metric namespaces -> registry platform namespaces. The graph keys
#: channel-granular (``google_ads.spend``); the registry keys platform-level
#: (``google.spend``). ``magento`` maps to two candidate registry platforms.
_NS_ALIAS: dict[str, tuple[str, ...]] = {
    "google_ads": ("google",),
    "google_youtube": ("google",),
    "google_pmax": ("google",),
    "google_search": ("google",),
    "google_shopping": ("google",),
    "meta_ads": ("meta",),
    "meta_retargeting": ("meta",),
    "meta_creative": ("meta",),
    "meta_prospecting": ("meta",),
    "email": ("klaviyo",),
    "sms": ("klaviyo",),
    "ga4": ("web",),
    "magento": ("ecom", "store"),
}


def registry_row(metric_id: str, reg_index: dict[str, dict[str, str]]) -> dict[str, str]:
    """Resolve a metric's registry row by exact id, else by namespace alias.

    Tries the metric id verbatim first; failing that, rewrites the channel
    namespace to its registry platform (e.g. ``google_ads.spend`` ->
    ``google.spend``) per :data:`_NS_ALIAS` and returns the first hit. ``{}`` when
    nothing resolves.
    """
    if metric_id in reg_index:
        return reg_index[metric_id]
    if "." in metric_id:
        namespace, concept = metric_id.split(".", 1)
        for alias in _NS_ALIAS.get(namespace, ()):
            row = reg_index.get(f"{alias}.{concept}")
            if row:
                return row
    return {}


def load_mart_inventory(marts_dir: Path = _MARTS_DIR) -> set[str]:
    """Return the set of REAL dbt marts (normalized ``MARTS.<table>``).

    Keys of :func:`harness.marts.lineage.parse_mart_refs` — i.e. every
    ``*.sql`` mart model under ``marts_dir``. Used to filter scanned tokens down
    to actual marts (drops logical names / typos / staging tables). Empty when
    the directory is absent.
    """
    return set(lineage.parse_mart_refs(marts_dir))


def load_dashboard_mart_mapping(path: Path = _QUERY_BUILDER) -> dict[str, str]:
    """Parse ``DASHBOARD_MART_MAPPING`` (dashboard slug -> mart) from query_builder.

    Best-effort regex over the file for ``"slug": "mart_table"`` entries; an
    absent file yields ``{}``. The fallback when a metric has no resolvable repo
    file but is associated with a dashboard.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {slug: mart for slug, mart in _DASH_MART_RE.findall(text)}


def _parse_iso_date(value: str | None) -> date | None:
    """Parse ``YYYY-MM-DD`` (or ISO datetime) into a ``date``; else ``None``."""
    if not value:
        return None
    text = value.strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def registry_freshness(
    metric_id: str,
    reg_index: dict[str, dict[str, str]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Return the registry-sourced freshness window for a metric (all ``None`` if absent)."""
    row = registry_row(metric_id, reg_index)

    def _clean(key: str) -> str | None:
        val = (row.get(key) or "").strip()
        return val or None

    n_periods_raw = _clean("n_periods")
    try:
        n_periods = int(float(n_periods_raw)) if n_periods_raw else None
    except ValueError:
        n_periods = None

    history_end = _clean("history_end")
    data_stale: bool | None = None
    end_date = _parse_iso_date(history_end)
    if end_date is not None:
        ref = today or date.today()
        data_stale = (ref - end_date).days > STALE_SLA_DAYS

    return {
        "history_start": _clean("history_start"),
        "history_end": history_end,
        "n_periods": n_periods,
        "availability": _clean("availability"),
        "data_stale": data_stale,
    }


# ---------------------------------------------------------------------------
# Per-metric deterministic field bundle
# ---------------------------------------------------------------------------


def _metric_dashboards(entry: dict[str, Any]) -> list[str]:
    """Extract a metric's dashboard slugs from a catalog entry (list or str)."""
    raw = entry.get("dashboards") or entry.get("dashboard") or []
    if isinstance(raw, str):
        return [d.strip() for d in re.split(r"[;,]", raw) if d.strip()]
    if isinstance(raw, list):
        return [str(d).strip() for d in raw if str(d).strip()]
    return []


def enrich_metric_fields(
    metric_id: str,
    catalog: dict[str, dict[str, Any]],
    reg_index: dict[str, dict[str, str]],
    *,
    today: date | None = None,
    bc2_root: Path = _BC2_ROOT,
    mart_inventory: set[str] | None = None,
    dashboard_map: dict[str, str] | None = None,
    dashboards: list[str] | None = None,
) -> dict[str, Any]:
    """Compute the deterministic enrichment props for one metric (no DB, no LLM).

    Mart binding: scans the metric's referenced repository file(s) AND its SQL
    slice for every mart token (``MART_NAME``, ``_*_TABLE``, constants, ``FROM``
    literals, resolved ``{VAR}`` placeholders), filters to the real dbt
    ``mart_inventory`` when supplied, and falls back to ``dashboard_map`` via the
    metric's dashboards when nothing resolved. Also extracts ``sql_query_real``,
    source columns (registry-preferred), and the registry freshness window.
    Returns only keys that have a value (safe to merge with ``SET +=``).
    """
    entry = catalog.get(metric_id, {})
    ref = entry.get("source_code_ref")
    sql_real = read_real_sql(ref, bc2_root=bc2_root)
    file_text = referenced_files_text(ref, bc2_root=bc2_root)

    reg_row = registry_row(metric_id, reg_index)

    # The registry's explicit ``mart_source`` is the authoritative binding for
    # channel/raw metrics (e.g. ``google_ads.spend`` -> mart_google_campaign_*)
    # that have no dedicated backend method. Pipe/semicolon-delimited (multi-mart).
    reg_marts: set[str] = set()
    reg_mart_raw = (reg_row.get("mart_source") or "").strip()
    if reg_mart_raw and reg_mart_raw.lower() not in {"none", "null", "nan"}:
        for part in re.split(r"[|;,]", reg_mart_raw):
            if part.strip():
                reg_marts.add(lineage.normalize_mart(part.strip()))

    # Method-precise marts: what THIS metric's SQL slice queries (literal MART_*
    # tokens + resolved ``FROM {VAR}`` placeholders). Scanning the slice (not the
    # whole file) avoids attributing a sibling metric's marts. Unioned with the
    # registry binding above.
    marts: set[str] = (
        set(marts_from_text(sql_real))
        | set(resolve_mart_vars(sql_real, file_text))
        | reg_marts
    )
    if mart_inventory:
        marts = {m for m in marts if m in mart_inventory}
    # Base-class-driven metric (its method names no mart): fall back to the
    # repository class's primary ``MART_NAME``.
    if not marts:
        for mart_name in _MART_NAME_RE.findall(file_text):
            normalized = lineage.normalize_mart(mart_name)
            if not mart_inventory or normalized in mart_inventory:
                marts.add(normalized)
    # Last resort: the dashboard -> mart mapping (dashboards from the caller —
    # e.g. the live node's dashboard_ids — or the catalog entry).
    if not marts and dashboard_map:
        for dash in (dashboards or _metric_dashboards(entry)):
            mart = dashboard_map.get(dash)
            if not mart:
                continue
            normalized = lineage.normalize_mart(mart)
            if not mart_inventory or normalized in mart_inventory:
                marts.add(normalized)

    # Prefer the registry's explicit source_columns (pipe-delimited) when present.
    reg_cols_raw = (reg_row.get("source_columns") or "").strip()
    if reg_cols_raw and reg_cols_raw.lower() not in {"none", "null", "nan"}:
        source_columns = _clean_columns(re.split(r"[|;,]", reg_cols_raw))
    else:
        source_columns = extract_source_columns_from_sql(sql_real)

    props: dict[str, Any] = {}
    if sql_real:
        props["sql_query_real"] = sql_real
    if marts:
        props["mart_sources"] = sorted(marts)
    if source_columns:
        props["source_columns"] = source_columns
    for key, val in registry_freshness(metric_id, reg_index, today=today).items():
        if val is not None:
            props[key] = val
    return props


# ---------------------------------------------------------------------------
# Mart-derived edge candidates (structural-dedup filter)
# ---------------------------------------------------------------------------


def structural_pairs_from_edges(edges: list[tuple[str, str]]) -> set[frozenset]:
    """Build a set of unordered ``{from, to}`` pairs from DECOMPOSES_INTO edges."""
    return {frozenset((a, b)) for a, b in edges if a and b}


def filter_structural_dups(
    candidates: list[dict[str, Any]],
    structural_pairs: set[frozenset],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split candidates into ``(kept, dropped)`` — drop pairs already structural.

    A candidate is *dropped* when its unordered ``{from, to}`` pair already has a
    ``DECOMPOSES_INTO`` edge: the formula edge subsumes the causal one, so no
    parallel ``INFLUENCES`` should be proposed for it.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for cand in candidates:
        pair = frozenset((cand.get("from"), cand.get("to")))
        (dropped if pair in structural_pairs else kept).append(cand)
    return kept, dropped


def build_enrich_candidates(
    metrics: list[dict[str, Any]],
    marts_dir: Path,
    structural_pairs: set[frozenset],
    *,
    hub_cap: int = 12,
) -> dict[str, Any]:
    """Generate mart-derived edge candidates and drop structural duplicates."""
    generated = lineage.generate_candidates(metrics, marts_dir, hub_cap=hub_cap)
    kept, dropped = filter_structural_dups(generated["candidates"], structural_pairs)
    counts = dict(generated["counts"])
    counts["kept"] = len(kept)
    counts["dropped_structural"] = len(dropped)
    return {
        "candidates": kept,
        "dropped_structural": dropped,
        "skipped_hubs": generated["skipped_hubs"],
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# Live-graph deterministic enrichment pass (the only DB-touching function)
# ---------------------------------------------------------------------------


def run_deterministic_enrich(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Enrich every live ``:Metric`` node with mart/SQL/freshness props.

    For each metric, computes :func:`enrich_metric_fields` (with the dbt mart
    inventory + dashboard map loaded once) and — unless ``dry_run`` — writes ONLY
    those props with an additive ``MATCH (m:Metric) SET m += $props`` (provenance
    untouched, no node created, no LLM). Idempotent. Returns a coverage summary.
    """
    from harness.kg.driver import get_db

    db = get_db()
    catalog = load_catalog()
    reg_index = load_registry_index()
    mart_inventory = load_mart_inventory()
    dashboard_map = load_dashboard_mart_mapping()

    rows = db.read(
        "MATCH (m:Metric) RETURN m.metric_uid AS uid, m.metric_id AS mid, "
        "m.dashboard_ids AS dashboards ORDER BY uid"
    )
    if limit is not None:
        rows = rows[:limit]

    processed = with_marts = with_sql = with_freshness = written = 0
    for row in rows:
        uid = row["uid"]
        mid = row.get("mid") or uid
        dashboards = row.get("dashboards") or None
        props = enrich_metric_fields(
            mid, catalog, reg_index, today=today, mart_inventory=mart_inventory,
            dashboard_map=dashboard_map, dashboards=dashboards,
        )
        if not props and mid != uid:
            props = enrich_metric_fields(
                uid, catalog, reg_index, today=today, mart_inventory=mart_inventory,
                dashboard_map=dashboard_map, dashboards=dashboards,
            )
        processed += 1
        with_marts += "mart_sources" in props
        with_sql += "sql_query_real" in props
        with_freshness += any(
            k in props for k in ("history_start", "history_end", "n_periods")
        )
        if props and not dry_run:
            db.write(
                "MATCH (m:Metric {metric_uid: $uid}) SET m += $props",
                uid=uid,
                props=props,
            )
            written += 1

    return {
        "processed": processed,
        "with_marts": with_marts,
        "with_sql": with_sql,
        "with_freshness": with_freshness,
        "written": 0 if dry_run else written,
        "dry_run": dry_run,
        "stamped_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


#: Fixed timestamp for the one-time ledger migration, so re-running it is a
#: no-op (the derived ``event_id`` stays stable -> append dedupes).
_MIGRATION_TS: str = "2026-06-26T00:00:00Z"


def critique_dedupe(*, dry_run: bool = False) -> dict[str, Any]:
    """Remove every ``INFLUENCES`` edge that PARALLELS a ``DECOMPOSES_INTO`` pair.

    A formula edge subsumes the causal one (it is definitional, pinned 1.0, and
    already walked by traversal), so a parallel causal edge double-counts. Finds
    each ``(a)-[:INFLUENCES]->(b)`` where ``(a)`` and ``(b)`` are also linked by a
    ``DECOMPOSES_INTO`` (either direction) and deletes the ``INFLUENCES`` edge.
    ``dry_run`` reports the pairs without deleting.
    """
    from harness.kg.driver import get_db

    db = get_db()
    pairs = db.read(
        "MATCH (a:Metric)-[i:INFLUENCES]->(b:Metric) "
        "WHERE (a)-[:DECOMPOSES_INTO]-(b) "
        "RETURN a.metric_uid AS f, b.metric_uid AS t"
    )
    if not dry_run and pairs:
        db.write(
            "MATCH (a:Metric)-[i:INFLUENCES]->(b:Metric) "
            "WHERE (a)-[:DECOMPOSES_INTO]-(b) DELETE i"
        )
    return {
        "found": len(pairs),
        "removed": 0 if dry_run else len(pairs),
        "pairs": [[p["f"], p["t"]] for p in pairs],
        "dry_run": dry_run,
    }


def migrate_edge_ledger(*, dry_run: bool = False) -> dict[str, Any]:
    """Seed the evidence ledger on legacy ``INFLUENCES`` edges (flat confidence).

    For every ``INFLUENCES`` edge that carries a flat ``confidence`` but no
    ``evidence_ledger``, converts that tier into a pair of PRIOR evidence events
    (:func:`harness.kg.evidence.seed_prior_event`) and folds them in through
    :func:`harness.kg.arbitration.append_edge_evidence` — so the edge's
    confidence becomes a reproducible Beta posterior (FR-SCORE-001). The fold
    writer's structural-dedup guard skips any edge that parallels a formula edge
    (those should already be gone via :func:`critique_dedupe`). Idempotent via a
    fixed migration timestamp. ``dry_run`` counts without writing.
    """
    from harness.kg import arbitration, evidence
    from harness.kg.driver import get_db

    db = get_db()
    rows = db.read(
        "MATCH (a:Metric)-[i:INFLUENCES]->(b:Metric) "
        "WHERE i.evidence_ledger IS NULL AND i.confidence IS NOT NULL "
        "RETURN a.metric_uid AS f, b.metric_uid AS t, i.confidence AS c, "
        "i.relation AS rel"
    )
    migrated = skipped = 0
    for row in rows:
        if dry_run:
            migrated += 1
            continue
        events = evidence.seed_prior_event(
            float(row["c"]), attribution="migrate:flat_confidence",
            timestamp=_MIGRATION_TS,
        )
        result: dict[str, Any] | None = None
        for event in events:
            result = arbitration.append_edge_evidence(
                db, from_key=row["f"], to_key=row["t"], event=event,
                edge_props={"relation": row.get("rel") or "llm_causal"},
            )
        if result and result.get("status") == "skipped_structural_dup":
            skipped += 1
        else:
            migrated += 1
    return {
        "candidates": len(rows),
        "migrated": 0 if dry_run else migrated,
        "skipped_structural": skipped,
        "dry_run": dry_run,
    }


#: Fixed timestamp for the one-time mart-lineage promotion, so re-running it is a
#: no-op (the derived ``event_id`` stays stable -> the ledger append dedupes).
_LINEAGE_TS: str = "2026-06-26T00:00:00Z"


def promote_lineage_edges(
    *, dry_run: bool = False, limit: int | None = None
) -> dict[str, Any]:
    """Promote deterministic mart-lineage candidates to HELD ``INFLUENCES`` edges.

    Reads every live ``:Metric`` (its ``mart_sources`` / ``source_columns`` /
    ``domain_ids`` from the deterministic enrichment pass), runs the pure
    candidate producers — :func:`harness.marts.lineage.shared_mart_candidates`,
    :func:`~harness.marts.lineage.shared_column_candidates`, and
    :func:`~harness.marts.lineage.lineage_candidates` (orchestrated via
    :func:`build_enrich_candidates`) — drops any pair already linked by a formula
    (``DECOMPOSES_INTO``) edge, and writes the survivors as causal *candidates*:
    ``INFLUENCES`` edges with ``relation='mart_lineage'`` and
    ``review_state='held'`` so they are PARKED for human review and excluded from
    active causal traversal (see :func:`harness.kg.models.active_edge_predicate`).

    Each held edge is seeded with a low-mass PRIOR
    (:func:`harness.kg.evidence.seed_prior_event` at the FIXED
    :data:`_LINEAGE_TS`, so the derived ``event_id`` is stable) and folded onto
    the Beta ledger via :func:`harness.kg.arbitration.append_edge_evidence` —
    exactly mirroring :func:`migrate_edge_ledger`, so re-running adds no duplicate
    evidence (idempotent). A pair that already carries a NON-held ``INFLUENCES``
    edge (a human-approved / measured causal link) is left untouched; the writer's
    structural-dedup guard additionally skips any formula-subsumed pair.

    ``dry_run`` counts what would be written without touching the graph.

    Returns:
        ``{"candidates", "written", "skipped_existing", "skipped_structural",
        "skipped_hubs", "dry_run"}``.
    """
    from harness.ingest import edge_scoring
    from harness.kg import arbitration, evidence
    from harness.kg.driver import get_db
    from harness.kg.models import HELD_REVIEW_STATE

    db = get_db()
    rows = db.read(
        "MATCH (m:Metric) RETURN m.metric_uid AS metric_uid, "
        "m.mart_sources AS mart_sources, m.source_columns AS source_columns, "
        "m.domain_ids AS domain_ids ORDER BY metric_uid"
    )
    if limit is not None:
        rows = rows[:limit]
    metrics = [dict(row) for row in rows]

    struct = db.read(
        "MATCH (a:Metric)-[:DECOMPOSES_INTO]-(b:Metric) "
        "RETURN a.metric_uid AS f, b.metric_uid AS t"
    )
    structural_pairs = structural_pairs_from_edges([(r["f"], r["t"]) for r in struct])
    built = build_enrich_candidates(metrics, _MARTS_DIR, structural_pairs)

    # Collapse the per-basis candidates to one directed pair (keep the first
    # basis seen) so a pair shared via BOTH mart and column is written once.
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for cand in built["candidates"]:
        f, t = cand.get("from"), cand.get("to")
        if f and t:
            pairs.setdefault((f, t), cand)

    # Never clobber a non-held (active / human-blessed) causal edge; a pair that
    # is already our own 'held' edge re-folds idempotently (the fixed-timestamp
    # event dedupes in the ledger).
    existing = db.read(
        "MATCH (a:Metric)-[r:INFLUENCES]->(b:Metric) "
        "RETURN a.metric_uid AS f, b.metric_uid AS t, r.review_state AS rs"
    )
    existing_state = {(r["f"], r["t"]): r.get("rs") for r in existing}

    score = edge_scoring.score_edge("INFLUENCES:mart_lineage")
    written = skipped_existing = 0
    for (f, t), cand in pairs.items():
        if (f, t) in existing_state and existing_state[(f, t)] != HELD_REVIEW_STATE:
            skipped_existing += 1
            continue
        if dry_run:
            written += 1
            continue
        events = evidence.seed_prior_event(
            score.confidence,
            attribution="promote:mart_lineage",
            timestamp=_LINEAGE_TS,
            prior_mass=score.evidence_mass,
        )
        edge_props = {
            "relation": "mart_lineage",
            "review_state": HELD_REVIEW_STATE,
            "source_kind": "mart_lineage",
            "candidate_basis": cand.get("basis"),
            "cross_domain": cand.get("cross_domain"),
        }
        result: dict[str, Any] | None = None
        for event in events:
            result = arbitration.append_edge_evidence(
                db, from_key=f, to_key=t, event=event, edge_props=edge_props
            )
        if result and result.get("status") in ("skipped_structural_dup", "missing_endpoint"):
            skipped_existing += 1
        else:
            written += 1

    return {
        "candidates": len(pairs),
        "written": 0 if dry_run else written,
        "skipped_existing": skipped_existing,
        "skipped_structural": built["counts"].get("dropped_structural", 0),
        "skipped_hubs": len(built["skipped_hubs"]),
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Mart-binding drift audit (read-only; reuses graph_server._mart_sql_path)
# ---------------------------------------------------------------------------


def _mart_drift_rows(
    metric_marts: Mapping[str, Sequence[str]],
    resolver: Callable[[str], object],
) -> dict[str, Any]:
    """Pure mart-drift computation: which declared marts no longer resolve to SQL.

    For each ``{metric_uid: [mart, ...]}`` entry, every declared mart (a canonical
    ``MARTS.<table>`` or bare token) is resolved through ``resolver`` — a
    ``token -> path-or-None`` callable (e.g.
    :func:`harness.mcp.graph_server._mart_sql_path`). A mart that resolves to
    ``None`` is DRIFT: the dbt model the metric claims to read no longer exists in
    the BC_2 mart inventory. Pure — no DB and no filesystem of its own (the
    lookup lives entirely inside ``resolver``), so it is unit-testable with a fake
    resolver.

    Args:
        metric_marts: ``{metric_uid: declared mart identifiers}``. ``None``/empty
            mart lists are skipped (the metric is not counted).
        resolver: Maps a bare mart table token to a truthy path (resolved) or
            ``None`` (drift).

    Returns:
        ``{metrics_checked, marts_checked, resolved, drifted, drift}`` where
        ``drift`` is a sorted list of ``{metric_uid, mart}`` unresolved bindings.
    """
    drift: list[dict[str, str]] = []
    metrics_checked = marts_checked = resolved = 0
    for uid in sorted(metric_marts):
        marts = [m for m in (metric_marts[uid] or []) if m]
        if not marts:
            continue
        metrics_checked += 1
        for mart in marts:
            marts_checked += 1
            # _mart_sql_path keys off the bare table stem; live nodes store the
            # canonical ``MARTS.<table>`` form, so strip the schema qualifier.
            token = str(mart).rsplit(".", 1)[-1]
            if resolver(token) is None:
                drift.append({"metric_uid": uid, "mart": str(mart)})
            else:
                resolved += 1
    return {
        "metrics_checked": metrics_checked,
        "marts_checked": marts_checked,
        "resolved": resolved,
        "drifted": len(drift),
        "drift": sorted(drift, key=lambda d: (d["metric_uid"], d["mart"])),
    }


def audit_mart_drift(*, limit: int | None = None) -> dict[str, Any]:
    """Audit live metric mart bindings against the BC_2 dbt mart inventory.

    Read-only drift check: every live ``:Metric``'s ``mart_sources`` is resolved
    to its dbt ``*.sql`` model via
    :func:`harness.mcp.graph_server._mart_sql_path` (reused, not re-implemented).
    A binding that no longer resolves is reported as DRIFT — the metric points at
    a mart model that was renamed/removed in BC_2, so its runtime count overlay
    would silently break. Never writes the graph (counts are a runtime overlay;
    this only inspects the stored bindings).

    ``limit`` caps the number of metrics scanned (ordered by ``metric_uid``).

    Returns:
        The :func:`_mart_drift_rows` summary plus a ``stamped_at`` timestamp.
    """
    from harness.kg.driver import get_db
    from harness.mcp.graph_server import _mart_sql_path

    db = get_db()
    rows = db.read(
        "MATCH (m:Metric) WHERE m.mart_sources IS NOT NULL "
        "RETURN m.metric_uid AS uid, m.mart_sources AS marts ORDER BY uid"
    )
    if limit is not None:
        rows = rows[:limit]
    metric_marts = {r["uid"]: (r.get("marts") or []) for r in rows}
    summary = _mart_drift_rows(metric_marts, _mart_sql_path)
    summary["stamped_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return summary
