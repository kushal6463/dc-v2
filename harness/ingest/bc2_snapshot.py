"""BC_2 offline-snapshot importer for the KG skeleton (plan §6, Phase 1).

BC_2 (``/Users/kushal/Desktop/kal/BC_2``) is the primary offline source snapshot.
Its dbt seeds are the **authoritative clean formulas**; the experimental
``metrics_catalog.json`` / ``formula_reconciliation.json`` are hints only.

This module is pure (stdlib only): it loads, hashes, normalizes, and *validates*
BC_2 rows so the skeleton builder + causal pass can enrich metrics and propose
edge candidates. It NEVER writes the graph — validated rows become proposals
downstream, through arbitration.

Two disjoint id namespaces exist and are reconciled by name/formula:
  * coded ids (``AD_013``) in ``seed_config_metrics.csv`` (clean SQL formulas);
  * slug ids (``roas``) in ``seed_config_ontology_metrics.csv`` + mappings.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BC2_PATH = Path(os.environ.get("KG_BC2_PATH", "/Users/kushal/Desktop/kal/BC_2"))
FRD_DOCS_DIR = _REPO_ROOT / "docs" / "frd-docs"

#: Seed CSVs we care about for the skeleton (others are loaded too, generically).
PRIMARY_SEEDS: tuple[str, ...] = (
    "seed_config_metrics",
    "seed_config_ontology_metrics",
    "seed_config_chart_metric_mapping",
    "seed_config_metric_relationships",
    "seed_config_ontology_causal_edges",
    "seed_config_charts",
    "seed_config_thresholds",
)
CATALOG_FILES: tuple[str, ...] = (
    "docs/metric-catalog/metrics_catalog.json",
    "docs/metric-catalog/formula_reconciliation.json",
)

# ---------------------------------------------------------------------------
# Noise rejection (plan §6.1: BC_2 ontology causal rows contain SQL tokens)
# ---------------------------------------------------------------------------
_SQL_NOISE: frozenset[str] = frozenset({
    "select", "from", "where", "as", "and", "or", "case", "when", "then", "else",
    "end", "sum", "count", "avg", "min", "max", "null", "by", "group", "order",
    "on", "join", "distinct", "over", "partition", "100", "1000", "1000000",
    "current-date", "current-timestamp", "coalesce", "nullif", "cast", "0", "1",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm(text: str | None) -> str:
    """Snake_case identity key (``"ROAS (Return..)" -> roas_return``)."""
    return "_".join(_WORD_RE.findall(text.lower())) if text else ""


def _is_truthy(val: Any) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes", "t")


def _is_noise_id(raw: str | None) -> bool:
    """A BC_2 endpoint id that is a SQL token / literal / empty (never a metric)."""
    s = str(raw or "").strip().lower()
    if not s:
        return True
    if s in _SQL_NOISE:
        return True
    if re.fullmatch(r"[0-9.]+", s):  # bare number/literal
        return True
    if not re.search(r"[a-z]", s):  # no letters at all
        return True
    return False


# ---------------------------------------------------------------------------
# Load + hash
# ---------------------------------------------------------------------------
def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_bc2_sources(bc_path: Path = DEFAULT_BC2_PATH) -> dict[str, Any]:
    """Load every primary seed CSV + catalog JSON into memory.

    Returns ``{seed_stem: [row, ...], "metrics_catalog": {...}|None,
    "formula_reconciliation": [...]|None, "_bc_path": str}``. Missing files map to
    ``[]`` / ``None`` (robust to a partial snapshot).
    """
    bc_path = Path(bc_path)
    seeds_dir = bc_path / "dbt" / "seeds"
    out: dict[str, Any] = {"_bc_path": str(bc_path)}
    for stem in PRIMARY_SEEDS:
        out[stem] = _read_csv(seeds_dir / f"{stem}.csv")
    # Catalog hints (experimental).
    cat = bc_path / "docs" / "metric-catalog" / "metrics_catalog.json"
    rec = bc_path / "docs" / "metric-catalog" / "formula_reconciliation.json"
    out["metrics_catalog"] = json.loads(cat.read_text(encoding="utf-8")) if cat.exists() else None
    out["formula_reconciliation"] = (
        json.loads(rec.read_text(encoding="utf-8")) if rec.exists() else None
    )
    return out


def _hash_file(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def hash_source_files(
    bc_path: Path = DEFAULT_BC2_PATH, frd_docs: Path = FRD_DOCS_DIR
) -> dict[str, dict[str, Any]]:
    """Hash every BC_2 seed/catalog + BC_2 openapi + the dc-kg frd-docs fixtures.

    Returns ``{relpath: {sha256, bytes, rows?}}``. Proves the "identical
    fixtures" claim and gives the coverage report a content fingerprint.
    """
    bc_path = Path(bc_path)
    out: dict[str, dict[str, Any]] = {}
    seeds_dir = bc_path / "dbt" / "seeds"
    for stem in PRIMARY_SEEDS:
        p = seeds_dir / f"{stem}.csv"
        if p.exists():
            info = _hash_file(p)
            info["rows"] = max(0, sum(1 for _ in p.open(encoding="utf-8-sig")) - 1)
            out[f"dbt/seeds/{stem}.csv"] = info
    for rel in CATALOG_FILES + ("openapi.json", "dashboard-v2/public/chart-registry.json"):
        p = bc_path / rel
        if p.exists():
            out[rel] = _hash_file(p)
    for rel in ("openapi.json", "chart-registry.json"):
        p = frd_docs / rel
        if p.exists():
            out[f"frd-docs/{rel}"] = _hash_file(p)
    return out


# ---------------------------------------------------------------------------
# Normalize: merge the two metric namespaces into one enrichment lookup
# ---------------------------------------------------------------------------
def normalize_bc2_metric_catalog(sources: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Merge coded + slug metric rows into ``{norm_key: enrichment}``.

    ``norm_key`` is the normalized metric name (``"ROAS (Return on Ad Spend)" ->
    roas_return_on_ad_spend``) so coded (``AD_013``) and slug (``roas``) rows that
    share a name/concept land together. Each enrichment carries the clean
    formula(s), category, aggregation, unit, dimensions, dashboards, and the
    contributing source ids under ``source_ids``.
    """
    catalog: dict[str, dict[str, Any]] = {}

    def _slot(key: str) -> dict[str, Any]:
        return catalog.setdefault(key, {
            "norm_key": key, "names": [], "formula": None, "formula_type": None,
            "category": None, "subcategory": None, "aggregation": None, "unit": None,
            "dimensions": [], "dashboards": [], "tags": [], "is_kpi": None,
            "threshold_direction": None,
            "source_ids": {"coded": [], "slug": []},
        })

    # Coded rows: seed_config_metrics.csv (clean SQL formulas).
    for row in sources.get("seed_config_metrics", []) or []:
        if not _is_truthy(row.get("is_active", "true")):
            continue
        name = row.get("metric_name") or row.get("metric_id") or ""
        key = _norm(name)
        if not key:
            continue
        slot = _slot(key)
        if name and name not in slot["names"]:
            slot["names"].append(name)
        slot["source_ids"]["coded"].append(row.get("metric_id"))
        if slot["formula"] is None and (f := (row.get("metric_formula") or "").strip()):
            slot["formula"] = f
            slot["formula_type"] = "sql"
        slot["category"] = slot["category"] or row.get("metric_category")
        slot["subcategory"] = slot["subcategory"] or row.get("metric_subcategory")
        slot["aggregation"] = slot["aggregation"] or row.get("default_aggregation")
        slot["unit"] = slot["unit"] or row.get("metric_unit")

    # Slug rows: seed_config_ontology_metrics.csv (formula_expression + ontology).
    for row in sources.get("seed_config_ontology_metrics", []) or []:
        if not _is_truthy(row.get("is_active", "true")):
            continue
        name = row.get("metric_name") or row.get("name") or row.get("metric_id") or ""
        key = _norm(name)
        if not key:
            continue
        slot = _slot(key)
        if name and name not in slot["names"]:
            slot["names"].append(name)
        slot["source_ids"]["slug"].append(row.get("metric_id"))
        if slot["formula"] is None and (f := (row.get("formula_expression") or "").strip()):
            slot["formula"] = f
            slot["formula_type"] = row.get("formula_type") or "expression"
        slot["category"] = slot["category"] or row.get("category")
        slot["subcategory"] = slot["subcategory"] or row.get("subcategory")
        slot["unit"] = slot["unit"] or row.get("unit")
        slot["threshold_direction"] = slot["threshold_direction"] or row.get("threshold_direction")
        for field, dest in (("dimensions", "dimensions"), ("dashboards", "dashboards"), ("tags", "tags")):
            for item in _maybe_json_list(row.get(field)):
                if item not in slot[dest]:
                    slot[dest].append(item)
        if slot["is_kpi"] is None and row.get("is_kpi"):
            slot["is_kpi"] = _is_truthy(row.get("is_kpi"))

    # Alias each slug metric_id (e.g. "spend", "blended-roas") to its slot so a
    # lookup by metric_base hits the clean BC_2 formula. Coded ids (AD_013) are
    # not useful as bases, so skip them.
    for slot in list(catalog.values()):
        for sid in slot["source_ids"]["slug"]:
            k = _norm(sid)
            if k and not re.fullmatch(r"[a-z]+_\d+", k):
                catalog.setdefault(k, slot)
    return catalog


def _maybe_json_list(val: Any) -> list[str]:
    """Parse a JSON-list cell (``["a","b"]``) or comma string into a list."""
    if not val:
        return []
    s = str(val).strip()
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            return [str(x) for x in parsed if x]
        except (ValueError, TypeError):
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


def normalize_bc2_chart_mapping(
    sources: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``(dashboard, chart_id) -> [{metric_id, relationship, confidence, match_type,
    trustworthy}]``. ``trustworthy`` flags ``match_type == 'exact_id'``."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in sources.get("seed_config_chart_metric_mapping", []) or []:
        dash = str(row.get("dashboard") or "").strip()
        chart = str(row.get("chart_id") or "").strip()
        if not dash or not chart:
            continue
        out.setdefault((dash, chart), []).append({
            "metric_id": row.get("metric_id"),
            "relationship": row.get("relationship"),
            "confidence": row.get("confidence"),
            "match_type": row.get("match_type"),
            "trustworthy": str(row.get("match_type") or "").strip() == "exact_id",
        })
    return out


# ---------------------------------------------------------------------------
# Validate relationship / causal-edge candidate rows
# ---------------------------------------------------------------------------
#: BC_2 relationship_type -> (rel_type, relation, extra_props). Used by both
#: metric_relationships and ontology_causal_edges.
REL_TYPE_MAP: dict[str, tuple[str, str, dict[str, Any]]] = {
    "component_of": ("DECOMPOSES_INTO", "component", {}),
    "derived_from": ("DECOMPOSES_INTO", "component", {}),
    "computes": ("DECOMPOSES_INTO", "formula", {}),
    "correlated_with": ("INFLUENCES", "statistical", {}),
    "correlates_with": ("INFLUENCES", "statistical", {}),
    "impacts": ("INFLUENCES", "curated_rule", {}),
    "leads_to": ("INFLUENCES", "curated_rule", {}),
    "influences": ("INFLUENCES", "curated_rule", {}),
    "causes": ("INFLUENCES", "curated_rule", {}),
    "inverse_of": ("INFLUENCES", "curated_rule", {"sign": "negative"}),
}


def validate_bc2_relationship_rows(
    sources: dict[str, Any],
    resolve: Callable[[str], str | None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate every relationship/causal row → ``(valid, rejected)``.

    Structural rejection (always): SQL-token / empty / self-loop / unknown type.
    Resolution rejection (when ``resolve`` is given): an endpoint that resolves to
    no live metric_uid. Every rejected row carries a ``reason`` code; nothing is
    silently dropped (plan invariant).

    Valid rows are normalized to ``{from_id, to_id, from_uid?, to_uid?, rel_type,
    relation, source_kind, source_ref, mechanism?, strength?, direction?,
    temporal_lag?, sign?}``.
    """
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    def _emit(row_src: str, src_id: str, dst_id: str, rtype: str, extra: dict[str, Any]) -> None:
        reason: str | None = None
        if not _is_truthy(extra.pop("_active", "true")):
            reason = "inactive"
        elif _is_noise_id(src_id):
            reason = "sql_token_source"
        elif _is_noise_id(dst_id):
            reason = "sql_token_target"
        elif _norm(src_id) == _norm(dst_id):
            reason = "self_loop"
        mapping = REL_TYPE_MAP.get(str(rtype or "").strip().lower())
        if reason is None and mapping is None:
            reason = "unknown_relationship_type"

        if reason is not None:
            rejected.append({"source_file": row_src, "from_id": src_id, "to_id": dst_id,
                             "relationship_type": rtype, "reason": reason})
            return

        rel_type, relation, base_extra = mapping  # type: ignore[misc]
        rec: dict[str, Any] = {
            "from_id": src_id, "to_id": dst_id, "rel_type": rel_type, "relation": relation,
            "relationship_type": str(rtype or "").strip().lower(),
            "source_kind": f"bc2_{row_src}", "source_ref": f"bc2:{row_src}:{extra.get('_edge_id','')}",
            **base_extra, **{k: v for k, v in extra.items() if not k.startswith("_")},
        }
        if resolve is not None:
            fu, tu = resolve(src_id), resolve(dst_id)
            if fu is None:
                rejected.append({"source_file": row_src, "from_id": src_id, "to_id": dst_id,
                                 "relationship_type": rtype, "reason": "unresolved_source"})
                return
            if tu is None:
                rejected.append({"source_file": row_src, "from_id": src_id, "to_id": dst_id,
                                 "relationship_type": rtype, "reason": "unresolved_target"})
                return
            rec["from_uid"], rec["to_uid"] = fu, tu
        valid.append(rec)

    for row in sources.get("seed_config_metric_relationships", []) or []:
        _emit(
            "metric_relationship",
            str(row.get("source_metric_id") or ""),
            str(row.get("target_metric_id") or ""),
            row.get("relationship_type"),
            {"_active": row.get("is_active", "true"),
             "_edge_id": row.get("relationship_id", ""),
             "strength": row.get("relationship_strength"),
             "mechanism": row.get("description")},
        )
    for row in sources.get("seed_config_ontology_causal_edges", []) or []:
        lag_min = row.get("lag_hours_min")
        _emit(
            "ontology_edge",
            str(row.get("from_metric_id") or ""),
            str(row.get("to_metric_id") or ""),
            row.get("relationship_type"),
            {"_active": row.get("is_active", "true"),
             "_edge_id": row.get("edge_id", ""),
             "strength": row.get("strength"),
             "direction": row.get("direction"),
             "mechanism": row.get("mechanism"),
             "temporal_lag": _hours_to_iso(lag_min),
             "is_inferred": row.get("is_inferred")},
        )
    return valid, rejected


def _hours_to_iso(hours: Any) -> str | None:
    """Convert an hours value to an ISO-8601 duration (``P0D``/``PT6H``)."""
    try:
        h = int(float(hours))
    except (TypeError, ValueError):
        return None
    if h <= 0:
        return "P0D"
    if h % 24 == 0:
        return f"P{h // 24}D"
    return f"PT{h}H"


def inventory_summary(sources: dict[str, Any], hashes: dict[str, Any]) -> dict[str, Any]:
    """A compact source-inventory record for the coverage report / CLI."""
    return {
        "bc_path": sources.get("_bc_path"),
        "row_counts": {stem: len(sources.get(stem, []) or []) for stem in PRIMARY_SEEDS},
        "has_metrics_catalog": sources.get("metrics_catalog") is not None,
        "has_formula_reconciliation": sources.get("formula_reconciliation") is not None,
        "file_hashes": hashes,
    }
