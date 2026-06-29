"""Read-only Snowflake reader for the runtime ``active_campaigns`` COUNT overlay.

This is the first *live*-Snowflake path in dc-kg: every existing BC_2 read
(:mod:`harness.marts.lineage`, :mod:`harness.ingest.bc2_snapshot`, the MCP
``get_bc2_sql`` tools) is FILE-based, parsing dbt ``*.sql`` offline. This module
instead opens a read-only connection to the tenant's Snowflake ``MARTS`` schema
and returns *campaign COUNT* breakdowns at query time.

The counts are a **runtime overlay ONLY** — they are NEVER written to Neo4j and
they never mutate edges. The ``DECOMPOSES_INTO`` SUM tree
(``blended.active_campaigns`` = google_ads + meta_ads + klaviyo;
``google_ads.active_campaigns`` = the seven ``google_<x>`` sub-channels) is the
*structural* part that lives in the snapshot; this reader only fills the leaf
COUNT *values* for a given date window. Changing the date range changes the
values, never the edges.

Active predicate (applied uniformly at LEAF level, per
``backend/app/repositories/campaign_matrix.py``)::

    SPEND > 0 OR <conversion_value> > 0 OR IMPRESSIONS > 0 OR CLICKS > 0

The conversion-value column name differs by mart and the predicate uses the
correct one for each: ``MART_CAMPAIGN_MATRIX`` exposes ``CONVERSION_VALUE``
(singular) while ``MART_GOOGLE_CAMPAIGN_PERFORMANCE`` /
``MART_META_CAMPAIGN_PERFORMANCE`` expose ``CONVERSIONS_VALUE`` (plural).

What it measures
----------------
* **Platform counts** (additive, from ``MART_CAMPAIGN_MATRIX``):
  ``google_ads`` / ``meta_ads`` / ``klaviyo`` ``.active_campaigns`` via
  ``COUNT(DISTINCT CAMPAIGN_ID)`` grouped by ``PLATFORM``. ``blended`` is the
  definitional SUM of the three (it is reported as that sum, NOT as a separate
  grand-total query, so it always agrees with the ``DECOMPOSES_INTO`` tree).
* **Google sub-channel counts** (additive, from
  ``MART_GOOGLE_CAMPAIGN_PERFORMANCE``): ``COUNT(DISTINCT CAMPAIGN_ID)`` grouped
  by ``CAMPAIGN_TYPE`` (= ``ADVERTISING_CHANNEL_TYPE``) and mapped to the seven
  ``google_<x>.active_campaigns`` uids. The grain is
  ``DATE_DAY × CAMPAIGN_ID × DEVICE × AD_NETWORK_TYPE`` so a campaign spans many
  rows — hence ``COUNT(DISTINCT CAMPAIGN_ID)``, never ``COUNT(*)``.
* **Overlay dimensions** (NON-additive, reported separately in
  ``overlay_dims`` and never folded into the additive tree):
  ``ad_network_type`` (placement dim from the Google mart — a campaign can serve
  on several networks, so these counts overlap and do not sum to the channel
  split) and ``objective`` (Meta campaign objective from the Meta mart).

Meta sub-channel leaves (``meta_prospecting`` / ``meta_retargeting`` /
``meta_other``) are deliberately NOT produced here: Meta funnel stage is not a
column at campaign grain (it must be derived at ad-set grain via an optional
classifier), so this reader stops at the ``meta_ads`` platform total and exposes
the Meta ``objective`` distribution as a non-additive overlay instead.

Graceful degradation
---------------------
:func:`fetch_active_campaign_breakdown` NEVER raises. When Snowflake is
unconfigured (no ``SNOWFLAKE_ACCOUNT`` / ``SNOWFLAKE_USER`` / credential) or
unreachable, it returns ``stale=True`` with empty counts and a human
``freshness_notes`` string, so the API endpoint and canvas keep working with the
structural tree alone.

Dependency note: ``snowflake-connector-python`` is an *optional* dependency (the
``counts`` extra; install with ``uv sync --extra counts``). The connector import
is LOCAL to :func:`_open_connection`, so this module — and the
``harness.marts`` package — imports cleanly even when the connector is absent.
"""

from __future__ import annotations

import logging
from typing import Any

from harness.kg.config import Settings, get_settings

logger = logging.getLogger(__name__)

# --- Mart identities (canonical ``MARTS.<table>`` form, cf. lineage.normalize_mart) ---
_MART_CAMPAIGN_MATRIX = "MART_CAMPAIGN_MATRIX"
_MART_GOOGLE_CAMPAIGN_PERFORMANCE = "MART_GOOGLE_CAMPAIGN_PERFORMANCE"
_MART_META_CAMPAIGN_PERFORMANCE = "MART_META_CAMPAIGN_PERFORMANCE"

#: Marts this overlay derives from (reported in every result for provenance).
SOURCE_MARTS: tuple[str, ...] = (
    f"MARTS.{_MART_CAMPAIGN_MATRIX}",
    f"MARTS.{_MART_GOOGLE_CAMPAIGN_PERFORMANCE}",
    f"MARTS.{_MART_META_CAMPAIGN_PERFORMANCE}",
)

# --- Additive metric uids (the DECOMPOSES_INTO leaves/parents this reader fills) ---
_BLENDED_UID = "blended.active_campaigns"

#: ``MART_CAMPAIGN_MATRIX.PLATFORM`` value -> platform ``active_campaigns`` uid.
PLATFORM_TO_UID: dict[str, str] = {
    "Google Ads": "google_ads.active_campaigns",
    "Meta Ads": "meta_ads.active_campaigns",
    "Klaviyo": "klaviyo.active_campaigns",
}
#: The three platform addends of ``blended.active_campaigns`` (stable order).
PLATFORM_UIDS: tuple[str, ...] = tuple(PLATFORM_TO_UID.values())

#: Bucket for Google ``CAMPAIGN_TYPE`` values outside the explicit channel map
#: (e.g. ``UNKNOWN`` / ``MULTI_CHANNEL`` / ``SMART`` / ``LOCAL`` / ``HOTEL``).
GOOGLE_OTHER_UID = "google_other.active_campaigns"

#: ``MART_GOOGLE_CAMPAIGN_PERFORMANCE.CAMPAIGN_TYPE`` (= ``ADVERTISING_CHANNEL_TYPE``)
#: -> sub-channel ``active_campaigns`` uid. Keys are upper-case; lookups upper the
#: incoming value first. ``AD_NETWORK_TYPE`` is a placement dim and is NOT used
#: for this split (a SEARCH campaign can still serve on YouTube partners).
CAMPAIGN_TYPE_TO_UID: dict[str, str] = {
    "SEARCH": "google_search.active_campaigns",
    "VIDEO": "google_youtube.active_campaigns",
    "SHOPPING": "google_shopping.active_campaigns",
    "DISPLAY": "google_display.active_campaigns",
    "DEMAND_GEN": "google_demand_gen.active_campaigns",
    "PERFORMANCE_MAX": "google_pmax.active_campaigns",
}
#: All seven Google sub-channel addends of ``google_ads.active_campaigns`` — the
#: explicit six plus the catch-all — in a stable order (used to zero-fill).
GOOGLE_SUBCHANNEL_UIDS: tuple[str, ...] = (
    *CAMPAIGN_TYPE_TO_UID.values(),
    GOOGLE_OTHER_UID,
)


def _is_configured(cfg: Settings) -> bool:
    """Return ``True`` when the minimum Snowflake credentials are present.

    Requires an account, a user, and at least one credential (password or a
    key-pair path). Anything less means the overlay degrades gracefully without
    even attempting a connection.
    """
    has_credential = bool(cfg.snowflake_password or cfg.snowflake_private_key_path)
    return bool(cfg.snowflake_account and cfg.snowflake_user and has_credential)


def _load_private_key(path: str) -> bytes:
    """Load a PEM private key and return it as DER PKCS8 bytes for the connector.

    Mirrors the BC_2 connector pattern. ``cryptography`` is imported locally (it
    ships as a transitive dependency of ``snowflake-connector-python``, so it is
    always available whenever the ``counts`` extra is installed).
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as handle:
        key = serialization.load_pem_private_key(
            handle.read(), password=None, backend=default_backend()
        )
    return key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _open_connection(cfg: Settings):
    """Open a read-only Snowflake connection from ``cfg`` and set the UTC session.

    The ``import snowflake.connector`` is intentionally LOCAL so that importing
    this module (and the ``harness.marts`` package) never requires the optional
    ``counts`` extra. No DDL is issued — the reader is strictly read-only, so the
    ``CREATE SCHEMA`` step from the BC_2 loader is dropped; only the timezone is
    pinned for deterministic ``DATE_DAY`` comparisons.

    Tests monkeypatch this function to inject a fake connection, so no live
    Snowflake (and no installed connector) is needed to exercise the reader.
    """
    import snowflake.connector  # local: optional ``counts`` extra, keep import lazy

    params: dict[str, Any] = {
        "account": cfg.snowflake_account,
        "user": cfg.snowflake_user,
    }
    if cfg.snowflake_role:
        params["role"] = cfg.snowflake_role
    if cfg.snowflake_warehouse:
        params["warehouse"] = cfg.snowflake_warehouse
    if cfg.snowflake_database:
        params["database"] = cfg.snowflake_database
    if cfg.snowflake_schema:
        params["schema"] = cfg.snowflake_schema
    if cfg.snowflake_private_key_path:
        params["private_key"] = _load_private_key(cfg.snowflake_private_key_path)
    else:
        params["password"] = cfg.snowflake_password

    conn = snowflake.connector.connect(**params)
    cur = conn.cursor()
    try:
        cur.execute("ALTER SESSION SET TIMEZONE = 'UTC'")
    finally:
        cur.close()
    return conn


def _active_predicate(conversion_value_col: str) -> str:
    """Return the 4-way leaf activity predicate for the given mart's value column."""
    return (
        f"(SPEND > 0 OR {conversion_value_col} > 0 "
        "OR IMPRESSIONS > 0 OR CLICKS > 0)"
    )


def _grouped_counts(
    cursor: Any,
    *,
    table: str,
    group_col: str,
    conversion_value_col: str,
    date_from: str,
    date_to: str,
) -> dict[str, int]:
    """Run ``COUNT(DISTINCT CAMPAIGN_ID) GROUP BY <group_col>`` over a date window.

    Applies the 4-way active predicate (with the mart-correct conversion-value
    column) and returns ``{group_value: count}`` with ``None`` group values
    normalized to ``"UNKNOWN"``. The grain of every supported mart can repeat a
    ``CAMPAIGN_ID`` across rows, so the count is always ``DISTINCT``.
    """
    sql = (
        f"SELECT {group_col} AS DIM, COUNT(DISTINCT CAMPAIGN_ID) AS N\n"
        f"FROM {table}\n"
        "WHERE DATE_DAY >= %(date_from)s AND DATE_DAY <= %(date_to)s\n"
        f"  AND {_active_predicate(conversion_value_col)}\n"
        f"GROUP BY {group_col}"
    )
    cursor.execute(sql, {"date_from": date_from, "date_to": date_to})
    counts: dict[str, int] = {}
    for row in cursor.fetchall():
        key = row[0]
        label = str(key) if key is not None else "UNKNOWN"
        counts[label] = counts.get(label, 0) + int(row[1] or 0)
    return counts


def _table(cfg: Settings, table: str) -> str:
    """Schema-qualify a mart table name (defaults to ``MARTS`` when unset)."""
    schema = cfg.snowflake_schema or "MARTS"
    return f"{schema}.{table}"


def _query_breakdown(
    conn: Any, cfg: Settings, date_from: str, date_to: str
) -> tuple[dict[str, int], dict[str, dict[str, int]], list[str]]:
    """Run the overlay queries on ``conn`` and assemble the additive + overlay maps.

    Returns ``(counts_by_metric_uid, overlay_dims, zero_count_metric_uids)``:

    * ``counts_by_metric_uid`` — the additive tree: the three platform totals,
      the seven Google sub-channels, and ``blended`` (= the platform sum).
    * ``overlay_dims`` — ``{"ad_network_type": {...}, "objective": {...}}``, the
      non-additive placement/objective distributions (never summed into the tree).
    * ``zero_count_metric_uids`` — every additive uid measured as exactly 0
      (so the consumer can distinguish "measured zero" from "not measured").
    """
    cursor = conn.cursor()
    try:
        platform_raw = _grouped_counts(
            cursor,
            table=_table(cfg, _MART_CAMPAIGN_MATRIX),
            group_col="PLATFORM",
            conversion_value_col="CONVERSION_VALUE",  # singular in the matrix
            date_from=date_from,
            date_to=date_to,
        )
        google_raw = _grouped_counts(
            cursor,
            table=_table(cfg, _MART_GOOGLE_CAMPAIGN_PERFORMANCE),
            group_col="CAMPAIGN_TYPE",
            conversion_value_col="CONVERSIONS_VALUE",  # plural in the google mart
            date_from=date_from,
            date_to=date_to,
        )
        network_raw = _grouped_counts(
            cursor,
            table=_table(cfg, _MART_GOOGLE_CAMPAIGN_PERFORMANCE),
            group_col="AD_NETWORK_TYPE",
            conversion_value_col="CONVERSIONS_VALUE",
            date_from=date_from,
            date_to=date_to,
        )
        objective_raw = _grouped_counts(
            cursor,
            table=_table(cfg, _MART_META_CAMPAIGN_PERFORMANCE),
            group_col="OBJECTIVE",
            conversion_value_col="CONVERSIONS_VALUE",  # plural in the meta mart
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        cursor.close()

    # Additive platform totals (zero-filled), then blended = their definitional sum.
    platforms = dict.fromkeys(PLATFORM_UIDS, 0)
    for platform_value, n in platform_raw.items():
        uid = PLATFORM_TO_UID.get(platform_value)
        if uid is not None:  # ignore platforms with no node (e.g. LinkedIn Ads)
            platforms[uid] += n

    # Additive Google sub-channels (zero-filled); unknown CAMPAIGN_TYPE -> _other.
    google = dict.fromkeys(GOOGLE_SUBCHANNEL_UIDS, 0)
    for campaign_type, n in google_raw.items():
        uid = CAMPAIGN_TYPE_TO_UID.get(campaign_type.upper(), GOOGLE_OTHER_UID)
        google[uid] += n

    counts: dict[str, int] = {
        **platforms,
        **google,
        _BLENDED_UID: sum(platforms.values()),
    }
    overlay_dims = {"ad_network_type": network_raw, "objective": objective_raw}
    zero_count_metric_uids = sorted(uid for uid, n in counts.items() if n == 0)
    return counts, overlay_dims, zero_count_metric_uids


def _empty_result(
    anchor_metric_uid: str, date_from: str, date_to: str, *, freshness_notes: str
) -> dict[str, Any]:
    """Return the graceful ``stale=True`` payload with empty counts/overlay."""
    return {
        "anchor_metric_uid": anchor_metric_uid,
        "date_from": date_from,
        "date_to": date_to,
        "counts_by_metric_uid": {},
        "overlay_dims": {"ad_network_type": {}, "objective": {}},
        "zero_count_metric_uids": [],
        "stale": True,
        "freshness_notes": freshness_notes,
        "source_marts": list(SOURCE_MARTS),
    }


def fetch_active_campaign_breakdown(
    anchor_metric_uid: str,
    date_from: str,
    date_to: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Return the live ``active_campaigns`` COUNT breakdown for a date window.

    This is the agreed reader seam behind the HTTP overlay endpoint. The
    frontend calls the endpoint, not this function; the endpoint imports and
    calls this. The result is a runtime overlay and is never persisted to Neo4j.

    Args:
        anchor_metric_uid: The metric whose breakdown is requested (typically
            ``"blended.active_campaigns"``). Echoed back on the result; the full
            measurable tree is always returned regardless of the anchor.
        date_from: Inclusive start of the window, ``"YYYY-MM-DD"``.
        date_to: Inclusive end of the window, ``"YYYY-MM-DD"``.
        settings: Optional :class:`~harness.kg.config.Settings` for injection;
            falls back to the cached :func:`~harness.kg.config.get_settings`
            (mirrors :meth:`harness.kg.driver.GraphDB.from_settings`).

    Returns:
        A dict with the agreed shape::

            {
                "anchor_metric_uid": str,
                "date_from": str,
                "date_to": str,
                "counts_by_metric_uid": dict[str, int],
                "overlay_dims": {"ad_network_type": dict[str, int],
                                 "objective": dict[str, int]},
                "zero_count_metric_uids": list[str],
                "stale": bool,
                "freshness_notes": str,
                "source_marts": list[str],
            }

        On success ``stale`` is ``False`` and ``counts_by_metric_uid`` carries the
        additive tree. When Snowflake is unconfigured or unreachable, ``stale`` is
        ``True`` with empty counts/overlay — this function NEVER raises.
    """
    cfg = settings or get_settings()

    if not _is_configured(cfg):
        return _empty_result(
            anchor_metric_uid,
            date_from,
            date_to,
            freshness_notes=(
                "Snowflake not configured (set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, and "
                "SNOWFLAKE_PASSWORD); active_campaigns count overlay unavailable."
            ),
        )

    conn = None
    try:
        conn = _open_connection(cfg)
        counts, overlay_dims, zero_count_metric_uids = _query_breakdown(
            conn, cfg, date_from, date_to
        )
        return {
            "anchor_metric_uid": anchor_metric_uid,
            "date_from": date_from,
            "date_to": date_to,
            "counts_by_metric_uid": counts,
            "overlay_dims": overlay_dims,
            "zero_count_metric_uids": zero_count_metric_uids,
            "stale": False,
            "freshness_notes": (
                f"Live Snowflake read of {date_from}..{date_to} from "
                f"{cfg.snowflake_schema or 'MARTS'}."
            ),
            "source_marts": list(SOURCE_MARTS),
        }
    except Exception as exc:  # graceful degrade: never raise to the caller
        logger.warning(
            "active_campaigns Snowflake overlay degraded (%s): %s",
            type(exc).__name__,
            exc,
        )
        return _empty_result(
            anchor_metric_uid,
            date_from,
            date_to,
            freshness_notes=(
                f"Snowflake read failed ({type(exc).__name__}); "
                "serving empty active_campaigns count overlay."
            ),
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # closing must never mask the result
                logger.debug("Snowflake connection close failed", exc_info=True)
