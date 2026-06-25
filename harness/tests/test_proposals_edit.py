"""NO-DB tests for the file-based proposal queue's edit-persistence (M2 FIX 2).

The ``edit`` review action must persist the human-corrected ``payload`` so it
survives a reload AND is the payload the arbitration writer later applies. These
tests redirect the queue's :data:`PROPOSALS_DIR` to a tmp path (via
``monkeypatch``) so no real ``data/proposals`` is touched and no DB is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.store import proposals as store


@pytest.fixture
def proposals_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the proposal queue at an isolated tmp directory for one test."""
    monkeypatch.setattr(store, "PROPOSALS_DIR", tmp_path / "proposals")
    return tmp_path / "proposals"


def _seed_one(run_id: str, dashboard_id: str) -> str:
    """Write a single proposal and return its proposal_id."""
    store.write_proposals(
        run_id,
        dashboard_id,
        [
            {
                "proposal_id": "kgp_edit_me",
                "operation": "upsert",
                "target_label": "Metric",
                "target_id": "metric:x:y",
                "payload": {"display_name": "Original"},
            }
        ],
    )
    return "kgp_edit_me"


def test_edit_persists_payload_and_state(proposals_dir: Path) -> None:
    """set_review_state(..., 'approved', payload=...) persists the new payload."""
    run_id = "run-20260615T000000Z"
    proposal_id = _seed_one(run_id, "dash-a")

    # An edit is an approval of the corrected payload (server semantics): state
    # 'approved' + the replacement payload.
    ok = store.set_review_state(
        run_id,
        proposal_id,
        "approved",
        reason="fixed name",
        payload={"display_name": "Edited X", "extra": 1},
    )
    assert ok is True

    # Reload from disk — the edited payload and review_state must persist.
    reloaded = store.load_proposals(run_id=run_id)
    assert len(reloaded) == 1
    p = reloaded[0]
    assert p["review_state"] == "approved"
    assert p["payload"] == {"display_name": "Edited X", "extra": 1}
    assert p["review_reason"] == "fixed name"
    assert p.get("reviewed_at")


def test_set_review_state_without_payload_keeps_existing(proposals_dir: Path) -> None:
    """Omitting payload leaves the original payload untouched (approve/reject)."""
    run_id = "run-20260615T000100Z"
    proposal_id = _seed_one(run_id, "dash-b")

    ok = store.set_review_state(run_id, proposal_id, "rejected", reason="no good")
    assert ok is True

    p = store.load_proposals(run_id=run_id)[0]
    assert p["review_state"] == "rejected"
    assert p["payload"] == {"display_name": "Original"}  # unchanged


def test_set_review_state_missing_proposal_returns_false(proposals_dir: Path) -> None:
    """An unknown proposal id yields False (and no file rewrite)."""
    run_id = "run-20260615T000200Z"
    _seed_one(run_id, "dash-c")
    assert store.set_review_state(run_id, "nope", "approved") is False


def test_approve_all_pending_flips_only_pending(proposals_dir: Path) -> None:
    """approve_all_pending approves proposed/pending across files, leaving others."""
    run_id = "run-20260615T000300Z"
    # Two pending proposals across two per-dashboard files, plus one already
    # rejected (must be left untouched).
    store.write_proposals(run_id, "dash-a", [
        {"proposal_id": "p1", "operation": "upsert_edge", "target_label": "Metric",
         "target_id": "m:a", "payload": {}},
        {"proposal_id": "p2", "operation": "upsert_edge", "target_label": "Metric",
         "target_id": "m:b", "payload": {}},
    ])
    store.write_proposals(run_id, "dash-b", [
        {"proposal_id": "p3", "operation": "upsert", "target_label": "Metric",
         "target_id": "m:c", "payload": {}},
    ])
    # Reject one so it is no longer pending.
    store.set_review_state(run_id, "p2", "rejected", reason="nope")

    approved = store.approve_all_pending(run_id)
    assert approved == 2  # p1 + p3 (p2 was already rejected)

    by_id = {p["proposal_id"]: p for p in store.load_proposals(run_id=run_id)}
    assert by_id["p1"]["review_state"] == "approved"
    assert by_id["p3"]["review_state"] == "approved"
    assert by_id["p2"]["review_state"] == "rejected"  # untouched
    # A second call is a no-op (nothing left pending).
    assert store.approve_all_pending(run_id) == 0
