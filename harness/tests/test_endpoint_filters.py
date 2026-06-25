"""NO-DB tests for the OpenAPI endpoint denylist (``endpoint_filters``).

:func:`~harness.ingest.endpoint_filters.is_kg_endpoint` keys a route by the first
``/api/v1/<group>/`` segment and returns ``False`` iff that group is in
:data:`~harness.ingest.endpoint_filters.DENY_GROUPS` (operational routes that
carry no metric semantics), ``True`` otherwise. This module covers: every deny
group is filtered, metric/chart-style routes pass, the ``/api/v1`` prefix is
optional, and the edge cases (empty / prefix-only / no-prefix paths) behave per
the docstring. Pure: no model, no seed, no DB.
"""

from __future__ import annotations

import pytest

from harness.ingest.endpoint_filters import DENY_GROUPS, is_kg_endpoint


# ---------------------------------------------------------------------------
# Deny groups — every operational group is filtered out
# ---------------------------------------------------------------------------


def test_deny_groups_membership() -> None:
    """The denylist is exactly the 11 interview-locked operational groups."""
    assert DENY_GROUPS == frozenset(
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


@pytest.mark.parametrize("group", sorted(DENY_GROUPS))
def test_every_deny_group_is_filtered(group: str) -> None:
    """A ``/api/v1/<deny-group>/...`` path is rejected for every deny group."""
    assert is_kg_endpoint(f"/api/v1/{group}/list") is False
    # Also rejected without the /api/v1 prefix (group is the first segment).
    assert is_kg_endpoint(f"/{group}/list") is False


# ---------------------------------------------------------------------------
# Allowed routes — metric / chart / dashboard endpoints pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/metrics/series",
        "/api/v1/metrics/card",
        "/api/v1/dashboards/budget-pacing",
        "/api/v1/charts/roas",
        "/api/v1/marketing/spend",
        "/api/v1/blended/roas",
    ],
)
def test_kg_relevant_paths_pass(path: str) -> None:
    """A metric/chart/dashboard route group is not in the denylist -> kept."""
    assert is_kg_endpoint(path) is True


# ---------------------------------------------------------------------------
# Prefix handling — /api/v1 is optional
# ---------------------------------------------------------------------------


def test_prefix_optional_for_allowed_group() -> None:
    """The same allowed group is kept with and without the ``/api/v1`` prefix."""
    assert is_kg_endpoint("/api/v1/metrics/series") is True
    assert is_kg_endpoint("/metrics/series") is True
    assert is_kg_endpoint("metrics/series") is True


def test_prefix_optional_for_denied_group() -> None:
    """The same denied group is dropped with and without the ``/api/v1`` prefix."""
    assert is_kg_endpoint("/api/v1/auth/login") is False
    assert is_kg_endpoint("/auth/login") is False
    assert is_kg_endpoint("auth/login") is False


# ---------------------------------------------------------------------------
# Edge cases — empty / prefix-only / trailing slashes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["", "/", "/api/v1", "/api/v1/", "api/v1", "///"]
)
def test_empty_or_prefix_only_paths_are_kept(path: str) -> None:
    """An empty / prefix-only path defers to the caller (treated as KG-relevant)."""
    assert is_kg_endpoint(path) is True


def test_trailing_slash_does_not_change_group() -> None:
    """A trailing slash never alters the resolved route group."""
    assert is_kg_endpoint("/api/v1/metrics/") is True
    assert is_kg_endpoint("/api/v1/auth/") is False


def test_group_is_first_segment_not_a_substring_match() -> None:
    """A deny token appearing deeper in the path does not trigger the filter."""
    # ``admin`` is a deny group, but only as the FIRST segment after the prefix.
    assert is_kg_endpoint("/api/v1/metrics/admin-summary") is True
    assert is_kg_endpoint("/api/v1/admin/metrics") is False
