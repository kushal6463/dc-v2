"""OpenAPI endpoint denylist — keeps KG-relevant routes, drops infra/admin ones.

The live OpenAPI surface (``docs/frd-docs/openapi.json``) mixes metric/chart
routes (the data the knowledge graph is built from) with operational ones —
auth, tenant administration, master-config, feature flags, health probes, etc.
Those operational groups carry no metric semantics and must never seed a Metric
node or be attached as a metric's endpoint.

A route is keyed by the first path segment of its ``/api/v1/<group>/...``
namespace: the ``<group>`` immediately after the ``/api/v1`` prefix (or the
first segment when the prefix is absent). :func:`is_kg_endpoint` returns
``False`` for any path whose group is in :data:`DENY_GROUPS` and ``True``
otherwise. Used by the ``get_metric_source`` doc-reading tool (to filter the
endpoint slice it joins onto a metric) and by node-phase endpoint assignment.

Pure: stdlib only — never imports a model, a seed, or the database.
"""

from __future__ import annotations

#: Operational route groups excluded from the knowledge graph (interview
#: decision): no metric/chart semantics, so they never seed a Metric node nor
#: become a metric's endpoint. Keyed by the first ``/api/v1/<group>/`` segment.
DENY_GROUPS: frozenset[str] = frozenset(
    {
        "admin",
        "auth",
        "master-config",
        "feature-flags",
        "tenants",
        "support",
        "audit-log",
        "health",
        "discovery",
        "alerts-config",
        "data-quality",
    }
)


def is_kg_endpoint(path: str) -> bool:
    """Return whether an OpenAPI ``path`` is KG-relevant (not an operational route).

    The path's route group is its first ``/api/v1/<group>/`` segment — the
    segment immediately after the ``/api/v1`` prefix, or the first non-empty
    segment when that prefix is absent. The path returns ``False`` iff that
    group is in :data:`DENY_GROUPS`, ``True`` otherwise. A leading ``/api/v1``
    (with or without leading/trailing slashes) and a missing prefix are both
    handled gracefully; an empty/prefix-only path is treated as KG-relevant
    (``True``) so the caller — not this filter — decides what to do with it.

    Args:
        path: An OpenAPI route path, e.g. ``/api/v1/metrics/series`` or
            ``/api/v1/auth/login`` (a leading ``/api/v1`` is optional).

    Returns:
        ``True`` if the path's route group is not in :data:`DENY_GROUPS`.
    """
    segments = [segment for segment in path.split("/") if segment]
    # Skip the ``api`` / ``v1`` prefix segments when present so the route group
    # is read from the same position whether or not the path is prefixed.
    if segments[:2] == ["api", "v1"]:
        segments = segments[2:]
    if not segments:
        return True
    return segments[0] not in DENY_GROUPS
