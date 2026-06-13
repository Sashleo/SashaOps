"""Tests for the run state machine (core/run_manager.py)."""

from core.run_manager import (
    create_run, advance_to_stage1, cancel_run, load_run, RunStage,
)


def test_create_run_starts_not_started(temp_client):
    st = create_run(temp_client, "Test Co", ["overview"])
    assert st.stage == RunStage.NOT_STARTED
    assert st.business_name == "Test Co"
    assert "overview" in st.modules_requested


def test_advance_to_stage1(temp_client):
    st = create_run(temp_client, "Test Co", ["overview"])
    st2 = advance_to_stage1(st)
    assert st2.stage == RunStage.STAGE1_COLLECTING


def test_cancel_run_sets_failed(temp_client):
    st = create_run(temp_client, "Test Co", ["overview"])
    advance_to_stage1(st)
    cancelled = cancel_run(st.run_id)
    assert cancelled is not None
    assert cancelled.stage == RunStage.FAILED
    # Persisted: reloading shows FAILED too
    assert load_run(st.run_id).stage == RunStage.FAILED


def test_load_nonexistent_run_returns_none():
    assert load_run("does-not-exist-zzz") is None
