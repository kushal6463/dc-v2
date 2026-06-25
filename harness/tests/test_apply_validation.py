"""NO-DB tests for the apply-layer Pydantic payload re-validation (M2 FIX 1).

The apply stage re-validates every agent-produced node payload through the
matching Pydantic model BEFORE it reaches the (single) arbitration writer. This
must (a) drop invented / unknown field names and (b) reject out-of-vocabulary
enum values per-proposal — returning an ``invalid_payload`` result instead of
crashing the run. None of this needs Neo4j: the bad-enum path short-circuits
before any DB access, and the unknown-field path is asserted via the model the
apply stage uses.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.ingest.apply import _PAYLOAD_MODELS, apply_proposal
from harness.kg.models import Metric


class _ExplodingDB:
    """A stand-in GraphDB that fails loudly if the writer is ever reached.

    The bad-payload path must return BEFORE any DB write, so any call here is a
    test failure (the validation guard leaked an invalid payload to the writer).
    """

    def write(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("writer reached for an invalid payload")

    def read(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("writer reached for an invalid payload")


def _metric_payload(**overrides: Any) -> dict[str, Any]:
    """A minimal-but-valid Metric payload, with optional field overrides."""
    base: dict[str, Any] = {
        "metric_uid": "metric:x:y",
        "canonical_id": "x-y",
        "metric_id": "y",
        "display_name": "Y",
        "product_ids": [],
        "domain_ids": [],
        "scope_key": "x",
        "metric_base": "y",
        "is_derived": False,
        "data_classification": "internal",
        "min_level": 1,
        "status": "proposed",
    }
    base.update(overrides)
    return base


def test_apply_models_cover_proposer_labels() -> None:
    """The apply re-validation map covers every label the proposer emits.

    M2 product decision: the proposer now emits only ``Dashboard`` / ``Metric``
    (per-entry UIComponents are gone — chart types are generalised type nodes
    seeded at bootstrap). ``UIComponent`` stays in the re-validation map so
    bootstrap-seeded type nodes still re-validate if ever replayed.
    """
    for label in ("Metric", "Dashboard"):
        assert label in _PAYLOAD_MODELS
    # The generalised chart-type node label is still covered for re-validation.
    assert "UIComponent" in _PAYLOAD_MODELS


def test_revalidation_drops_unknown_fields() -> None:
    """An invented field name is silently dropped by re-validation."""
    payload = _metric_payload(bogus_field="DROP", value_format="currency")
    props = Metric(**payload).cypher_props()
    assert "bogus_field" not in props
    # A valid enum is preserved and coerced to its string value.
    assert props["value_format"] == "currency"


def test_revalidation_rejects_bad_enum() -> None:
    """An out-of-vocabulary enum value raises ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Metric(**_metric_payload(value_format="dollars"))


def test_apply_proposal_invalid_payload_does_not_reach_writer() -> None:
    """A bad-enum payload returns ``invalid_payload`` without any DB write."""
    proposal = {
        "target_label": "Metric",
        "target_id": "metric:x:y",
        "key_field": "metric_uid",
        "source_kind": "llm_proposal",
        "payload": _metric_payload(value_format="dollars"),  # bad enum
    }
    result = apply_proposal(_ExplodingDB(), proposal)
    assert result["status"] == "invalid_payload"
    assert result["target_id"] == "metric:x:y"
    assert "error" in result and result["error"]
