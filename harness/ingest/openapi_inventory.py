"""OpenAPI endpoint inventory for the KG skeleton (plan §5, §6).

Two jobs:

1. **Exclusion / inclusion** — decide which OpenAPI GET paths and which
   chart-registry entries are in scope for metric ingestion. We exclude the
   config/admin/system/ml surfaces and keep only the metric/chart surface.
2. **Dynamic-metric inventory** — the dynamic endpoints encode their allowed
   values in the endpoint *description* (``**Available metrics:**`` / ``Available
   charts:``), NOT in a parameter ``enum``. :func:`parse_available_lists` is
   tolerant of the 6+ observed format variants; :func:`build_endpoint_inventory`
   rolls them up per dashboard slug with a ``has_metric_list`` flag so the
   coverage report can show the dashboards that fall through to the registry/seed
   fallback.

This module is intentionally self-contained (stdlib only) so it is unit-testable
without a DB and reusable by the skeleton builder.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Templated path patterns (mirror prepass.py; kept local to avoid a back-import)
# ---------------------------------------------------------------------------
_TEMPLATED_METRIC_RE = re.compile(r"^/api/v1/([^/]+)/metrics/\{")
_TEMPLATED_CHART_RE = re.compile(r"^/api/v1/([^/]+)/charts/\{")
_DEFAULT_PATH_RE = re.compile(r"^/api/v1/([^/]+)/$")
_METADATA_PATH_RE = re.compile(r"^/api/v1/([^/]+)/metadata/?$")
_SLUG_RE = re.compile(r"^/api/v1/([^/]+)")

# ---------------------------------------------------------------------------
# Exclusions (plan §5)
# ---------------------------------------------------------------------------
#: Any of these substrings anywhere in a lowercased path excludes the endpoint.
EXCLUDED_SEGMENTS: frozenset[str] = frozenset({
    "master-config", "/master/", "/auth", "/settings", "/health", "/docs",
    "/redoc", "/chat", "/support", "/discovery", "/admin", "/decision-canvas",
    "/tenant-management", "/compliance", "/audit-log", "/admin-billing",
})
#: Dashboard slugs excluded wholesale (ml-* matched by prefix).
EXCLUDED_SLUGS: frozenset[str] = frozenset({"master-config"})
ML_PREFIX = "ml-"
#: Registry scope that marks ML/prediction surfaces (excluded for now).
EXCLUDED_SCOPES: frozenset[str] = frozenset({"prediction"})


def path_slug(path: str) -> str | None:
    """Return the ``{slug}`` of ``/api/v1/{slug}/...`` or ``None``."""
    m = _SLUG_RE.match(path)
    return m.group(1) if m else None


def is_excluded_path(path: str, method: str = "get") -> bool:
    """Return ``True`` if an OpenAPI path/method must never become a node/edge.

    GET-only; excludes the config/admin/system surfaces (segment match) and the
    ``master-config`` + ``ml-*`` dashboard slugs.
    """
    if method.lower() != "get":
        return True
    low = path.lower()
    if any(seg in low for seg in EXCLUDED_SEGMENTS):
        return True
    slug = path_slug(low)
    if slug and (slug in EXCLUDED_SLUGS or slug.startswith(ML_PREFIX)):
        return True
    return False


def is_excluded_registry_entry(entry: dict[str, Any]) -> bool:
    """Return ``True`` if a chart-registry entry must be skipped for ingestion."""
    dashboard_id = str(entry.get("dashboard_id") or "").lower()
    if dashboard_id.startswith(ML_PREFIX) or dashboard_id in EXCLUDED_SLUGS:
        return True
    if str(entry.get("scope") or "").lower() in EXCLUDED_SCOPES:
        return True
    return False


# ---------------------------------------------------------------------------
# "Available metrics / charts" description parser
# ---------------------------------------------------------------------------
# A header line: optional markdown markers, one of the known headers, optional
# parenthetical "(accepts both kebab-case and snake_case)", a colon, then any
# inline remainder on the same line.
_HEADER_RE = re.compile(
    r"^[^\S\n]*[*_>\s]*"
    r"(?P<kind>available\s+metrics|valid\s+metric\s+ids|available\s+charts|"
    r"valid\s+chart\s+ids|metric_id|chart_id)"
    r"[^:\n]*:[^\S\n]*(?P<inline>.*?)\s*$",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^[\s>]*[-*]\s+(?P<body>.+?)\s*$")
_ONE_OF_RE = re.compile(r"^one of\s+", re.IGNORECASE)


def _clean_id(raw: str) -> str | None:
    """Normalize one candidate id (strip backticks/quotes/punctuation).

    Returns ``None`` for prose (contains a space after cleaning), empties, or
    pure-punctuation. Preserves the raw kebab/snake case so both forms survive
    into the metric aliases; the consumer normalizes for matching.
    """
    s = raw.strip().strip("`'\"").strip().rstrip(".,;:").strip()
    if not s or " " in s:
        return None
    if not re.search(r"[a-zA-Z0-9]", s):
        return None
    return s


def _split_inline(inline: str) -> list[str]:
    """Parse an inline ``a, b, c`` id list (also handles ``One of a, b``)."""
    inline = _ONE_OF_RE.sub("", inline).strip(" .")
    parts = inline.split(",") if "," in inline else [inline]
    out: list[str] = []
    for part in parts:
        cid = _clean_id(part)
        if cid:
            out.append(cid)
    return out


def parse_available_lists(description: str | None) -> dict[str, list[tuple[str, str | None]]]:
    """Extract allowed metric/chart ids from one endpoint ``description``.

    Returns ``{"metrics": [(id, desc|None), ...], "charts": [...]}``. Handles the
    observed variants: bold/plain headers, backticked ids, inline CSV, the
    ``(accepts both kebab-case and snake_case)`` parenthetical, the ``Valid
    metric IDs:`` header, and the ``metric_id: One of a, b, c`` param form.
    """
    out: dict[str, list[tuple[str, str | None]]] = {"metrics": [], "charts": []}
    if not description:
        return out
    lines = description.splitlines()
    i = 0
    while i < len(lines):
        m = _HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        kind = m.group("kind").lower()
        bucket = "charts" if "chart" in kind else "metrics"
        inline = m.group("inline").strip()
        # For the bare param headers (metric_id/chart_id) only treat as a list
        # when it is clearly an enumeration ("One of ..." or a comma list).
        is_param_header = kind in ("metric_id", "chart_id")
        collected: list[tuple[str, str | None]] = []

        looks_listy = bool(inline) and (
            "," in inline or _ONE_OF_RE.match(inline) or not is_param_header
        )
        if looks_listy:
            for cid in _split_inline(inline):
                collected.append((cid, None))

        # If the header line had no usable inline ids, consume a following bullet
        # block (blank line or non-bullet ends it).
        j = i + 1
        if not collected:
            while j < len(lines):
                b = _BULLET_RE.match(lines[j])
                if b:
                    body = b.group("body")
                    head, _, desc = body.partition(":")
                    cid = _clean_id(head)
                    if cid:
                        collected.append((cid, desc.strip() or None))
                    j += 1
                elif lines[j].strip() == "":
                    break
                else:
                    break

        for cid, desc in collected:
            if cid not in {existing for existing, _ in out[bucket]}:
                out[bucket].append((cid, desc))
        i = j if j > i else i + 1
    return out


def build_endpoint_inventory(openapi: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Roll up the non-excluded GET metric/chart endpoints per dashboard slug.

    Returns ``{slug: {metric_ids, metric_descs, chart_ids, chart_descs,
    metric_template, chart_template, default_path, metadata_path,
    has_metric_list, has_chart_list}}``.
    """
    paths = openapi.get("paths", {}) or {}
    inv: dict[str, dict[str, Any]] = {}

    def _entry(slug: str) -> dict[str, Any]:
        return inv.setdefault(slug, {
            "slug": slug,
            "metric_ids": [], "metric_descs": {},
            "chart_ids": [], "chart_descs": {},
            "metric_template": None, "chart_template": None,
            "default_path": None, "metadata_path": None,
            "has_metric_list": False, "has_chart_list": False,
        })

    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        get = item.get("get")
        m_metric = _TEMPLATED_METRIC_RE.match(path)
        m_chart = _TEMPLATED_CHART_RE.match(path)
        m_default = _DEFAULT_PATH_RE.match(path)
        m_meta = _METADATA_PATH_RE.match(path)
        slug_m = m_metric or m_chart or m_default or m_meta
        if not slug_m:
            continue
        slug = slug_m.group(1)
        if is_excluded_path(path, "get"):
            continue
        entry = _entry(slug)
        desc = (get or {}).get("description") if isinstance(get, dict) else None

        if m_metric and get is not None:
            entry["metric_template"] = path
            lists = parse_available_lists(desc)
            for mid, mdesc in lists["metrics"]:
                if mid not in entry["metric_descs"]:
                    entry["metric_ids"].append(mid)
                    entry["metric_descs"][mid] = mdesc
            if lists["metrics"]:
                entry["has_metric_list"] = True
        elif m_chart and get is not None:
            entry["chart_template"] = path
            lists = parse_available_lists(desc)
            for cid, cdesc in lists["charts"]:
                if cid not in entry["chart_descs"]:
                    entry["chart_ids"].append(cid)
                    entry["chart_descs"][cid] = cdesc
            if lists["charts"]:
                entry["has_chart_list"] = True
        elif m_default:
            entry["default_path"] = path
        elif m_meta:
            entry["metadata_path"] = path

    return inv
