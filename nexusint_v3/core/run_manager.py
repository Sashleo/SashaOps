"""
core/run_manager.py — SashaOps Analysis Run State Machine.

Manages the three-stage analysis pipeline:
  Stage 1 — Data Collection
  Stage 2 — Module Analysis
  Stage 3 — Report Generation

Two run modes:
  GATED   — pauses at each stage gate for user approval
  EXPRESS — runs all stages without stopping

Every state transition is written to the database immediately.
Interrupted runs are fully resumable from the last completed stage.
"""

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass, field, asdict

import structlog

from database.connection import get_db

if TYPE_CHECKING:
    import asyncio


# ── Cancellation infrastructure ───────────────────────────────────────────────
# A run is cancelled in two ways simultaneously:
#   1. A cooperative flag (_CANCELLED_RUNS) the background stage workers poll at
#      checkpoints — guarantees the worker stops overwriting the FAILED state even
#      if the asyncio task can't be force-cancelled (e.g. mid executor call).
#   2. A registered asyncio.Task handle that cancel_run() force-cancels, so the
#      worker is interrupted at its next await point without waiting for a poll.

class RunCancelled(Exception):
    """Raised inside a stage worker when the run has been cancelled by the user."""


_CANCELLED_RUNS: set[str] = set()
_RUNNING_TASKS: "dict[str, asyncio.Task]" = {}


def request_cancellation(run_id: str) -> None:
    _CANCELLED_RUNS.add(run_id)


def is_cancelled(run_id: str) -> bool:
    return run_id in _CANCELLED_RUNS


def clear_cancellation(run_id: str) -> None:
    _CANCELLED_RUNS.discard(run_id)


def check_cancelled(run_id: str) -> None:
    """Raise RunCancelled if a cancellation has been requested for this run."""
    if run_id in _CANCELLED_RUNS:
        raise RunCancelled(run_id)


def register_task(run_id: str, task: "asyncio.Task") -> None:
    """Register the asyncio task running a stage so cancel_run() can interrupt it."""
    _RUNNING_TASKS[run_id] = task


def unregister_task(run_id: str) -> None:
    _RUNNING_TASKS.pop(run_id, None)

log = structlog.get_logger()


# ── Enums ─────────────────────────────────────────────────────────────────────

class RunStage(str, Enum):
    NOT_STARTED       = "not_started"
    STAGE1_COLLECTING = "stage1_collecting"
    STAGE1_COMPLETE   = "stage1_complete"    # Gate: awaiting user approval
    STAGE2_ANALYSING  = "stage2_analysing"
    STAGE2_COMPLETE   = "stage2_complete"    # Gate: awaiting user approval
    STAGE3_REPORTING  = "stage3_reporting"
    COMPLETE          = "complete"
    PAUSED            = "paused"
    FAILED            = "failed"


class RunMode(str, Enum):
    GATED   = "gated"    # Stops at gates, waits for approval
    EXPRESS = "express"  # Runs all stages without stopping


class ModuleStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    COMPLETE = "complete"
    FAILED   = "failed"
    SKIPPED  = "skipped"   # User deselected before report generation


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class DataCollectionSummary:
    """Summary shown at the Stage 1 gate."""
    sources_found: list = field(default_factory=list)
    sources_failed: list = field(default_factory=list)
    source_details: list = field(default_factory=list)   # List[dict] per-feed rows
    headlines_collected: int = 0
    sample_headlines: list = field(default_factory=list) # Top 5 headlines for display
    stock_data_available: bool = False
    stock_data_notes: str = ""
    documents_available: int = 0
    document_text: str = ""      # Extracted text from client's selected documents (Stage 2 injection)
    document_names: list = field(default_factory=list)  # Filenames included in this run
    search_results_count: int = 0
    search_snippets: str = ""    # Formatted DDG results for Stage 2 injection
    website_chars_scraped: int = 0
    website_snippet: str = ""    # First 3000 chars for Stage 2 injection
    news_brief: str = ""         # format_brief_for_prompt() output for Stage 2 injection
    analyst_note: str = ""       # Short 2-3 sentence summary for briefing card display
    market_sentiment: str = ""   # e.g. "Cautious" or "Positive"
    forex_text: str = ""         # SBP / PKR forex rates for Stage 2 injection
    index_text: str = ""         # PSX index summary for Stage 2 injection
    data_quality_score: float = 0.0    # 0.0 to 1.0
    gaps: list = field(default_factory=list)
    collected_at: str = ""
    # ── Pakistan intelligence pipeline (deterministic data layers) ──────────────
    industry_tag: str = "general"                          # detected industry key
    industry_keywords: list = field(default_factory=list)  # extracted keywords
    psx_profile: dict = field(default_factory=dict)        # ticker, sector, website
    gov_data: dict = field(default_factory=dict)           # keyed by scraper_id
    psx_sector_peers: list = field(default_factory=list)   # local PSX comparables
    global_peers: dict = field(default_factory=dict)       # yfinance sector benchmark
    ratios_text: str = ""                                  # PSX financial ratios block
    # ── Document intelligence (unlisted/private company track) ──────────────────
    local_financial_result: Optional[dict] = None          # from document_financials.py; same dict shape as PSX track
    detected_keywords: list = field(default_factory=list)  # NLP-extracted keywords for search targeting

    def quality_label(self) -> str:
        if self.data_quality_score >= 0.8: return "Strong"
        if self.data_quality_score >= 0.5: return "Adequate"
        return "Limited — consider uploading additional documents"


@dataclass
class AnalysisSummary:
    """Summary shown at the Stage 2 gate."""
    modules_complete: int = 0
    modules_failed: int = 0
    modules_partial: int = 0
    total_modules: int = 0
    key_findings: list = field(default_factory=list)   # One per module, 1 sentence
    overall_confidence: float = 0.0
    failed_module_reasons: dict = field(default_factory=dict)
    analysed_at: str = ""


@dataclass
class RunState:
    """Complete state of one analysis run."""
    run_id: str
    client_id: str
    business_name: str
    mode: RunMode
    stage: RunStage
    modules_requested: list
    modules_pending: list
    modules_running: list
    modules_complete: list
    modules_failed: dict      # module_id -> error_message
    modules_skipped: list
    data_summary: Optional[DataCollectionSummary]
    analysis_summary: Optional[AnalysisSummary]
    started_at: str
    updated_at: str
    completed_at: Optional[str]
    error: Optional[str] = None

    @property
    def is_at_gate(self) -> bool:
        return self.stage in (RunStage.STAGE1_COMPLETE, RunStage.STAGE2_COMPLETE)

    @property
    def is_running(self) -> bool:
        return self.stage in (
            RunStage.STAGE1_COLLECTING,
            RunStage.STAGE2_ANALYSING,
            RunStage.STAGE3_REPORTING,
        )

    @property
    def progress_pct(self) -> int:
        total = len(self.modules_requested)
        if total == 0: return 0
        done = len(self.modules_complete) + len(self.modules_failed)
        return int((done / total) * 100)

    @property
    def gate_message(self) -> str:
        if self.stage == RunStage.STAGE1_COMPLETE:
            d = self.data_summary
            if d:
                return (
                    f"Found {d.headlines_collected} headlines · "
                    f"{len(d.sources_found)} data sources · "
                    f"Data quality: {d.quality_label()}"
                )
            return "Data collection complete."
        if self.stage == RunStage.STAGE2_COMPLETE:
            a = self.analysis_summary
            if a:
                return (
                    f"{a.modules_complete}/{a.total_modules} modules complete · "
                    f"{a.modules_failed} failed · "
                    f"{a.modules_partial} partial"
                )
            return "Analysis complete."
        return ""

    @property
    def next_action_label(self) -> str:
        if self.stage == RunStage.STAGE1_COMPLETE:
            return "I've reviewed this — Begin Analysis →"
        if self.stage == RunStage.STAGE2_COMPLETE:
            return "Generate Report →"
        if self.stage == RunStage.PAUSED:
            return "Resume Run →"
        return ""

    @property
    def stage_label(self) -> str:
        labels = {
            RunStage.NOT_STARTED:       "Not started",
            RunStage.STAGE1_COLLECTING: "Collecting data...",
            RunStage.STAGE1_COMPLETE:   "Stage 1 complete — awaiting review",
            RunStage.STAGE2_ANALYSING:  "Analysing...",
            RunStage.STAGE2_COMPLETE:   "Stage 2 complete — awaiting review",
            RunStage.STAGE3_REPORTING:  "Generating report...",
            RunStage.COMPLETE:          "Complete",
            RunStage.PAUSED:            "Paused",
            RunStage.FAILED:            "Failed",
        }
        return labels.get(self.stage, self.stage.value)

    @property
    def stage_number(self) -> str:
        stage_nums = {
            RunStage.STAGE1_COLLECTING: "Stage 1 of 3",
            RunStage.STAGE1_COMPLETE:   "Stage 1 of 3",
            RunStage.STAGE2_ANALYSING:  "Stage 2 of 3",
            RunStage.STAGE2_COMPLETE:   "Stage 2 of 3",
            RunStage.STAGE3_REPORTING:  "Stage 3 of 3",
            RunStage.COMPLETE:          "Complete",
        }
        return stage_nums.get(self.stage, "")

    @property
    def elapsed_minutes(self) -> float:
        try:
            start = datetime.fromisoformat(self.started_at)
            return (datetime.utcnow() - start).total_seconds() / 60
        except Exception:
            return 0.0


# ── Run creation ──────────────────────────────────────────────────────────────

def create_run(
    client_id: str,
    business_name: str,
    modules_requested: list,
    mode: RunMode = RunMode.GATED,
) -> RunState:
    """
    Create a new analysis run and persist it to the database.
    Returns the RunState object. Market is always Pakistan.
    """
    run_id = str(uuid.uuid4())
    now    = datetime.utcnow().isoformat()

    state = RunState(
        run_id=run_id,
        client_id=client_id,
        business_name=business_name,
        mode=mode,
        stage=RunStage.NOT_STARTED,
        modules_requested=modules_requested,
        modules_pending=list(modules_requested),
        modules_running=[],
        modules_complete=[],
        modules_failed={},
        modules_skipped=[],
        data_summary=None,
        analysis_summary=None,
        started_at=now,
        updated_at=now,
        completed_at=None,
    )

    _save_run(state)
    log.info("Run created", run_id=run_id, business=business_name, mode=mode.value)
    return state


def load_run(run_id: str) -> Optional[RunState]:
    """Load a run from the database. Returns None if not found."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM analysis_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row:
        return None
    return _row_to_state(row)


def load_latest_active_run(client_id: str) -> Optional[RunState]:
    """Find the most recent incomplete run for a client (any business)."""
    db = get_db()
    row = db.execute(
        """
        SELECT * FROM analysis_runs
        WHERE client_id = ?
          AND status NOT IN ('complete', 'failed')
        ORDER BY started_at DESC LIMIT 1
        """,
        (client_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_state(row)


def load_active_run(client_id: str, business_name: str) -> Optional[RunState]:
    """
    Load the most recent incomplete run for a client/business combination.
    Used to resume interrupted runs.
    """
    db = get_db()
    row = db.execute(
        """
        SELECT * FROM analysis_runs
        WHERE client_id = ? AND business_name = ?
          AND status NOT IN ('complete', 'failed')
        ORDER BY started_at DESC LIMIT 1
        """,
        (client_id, business_name),
    ).fetchone()
    if not row:
        return None
    return _row_to_state(row)


def get_run_history(client_id: str, limit: int = 20) -> list[dict]:
    """Get recent run history for a client."""
    db = get_db()
    rows = db.execute(
        """
        SELECT run_id, business_name, status, mode,
               modules_complete, modules_failed, started_at, completed_at
        FROM analysis_runs
        WHERE client_id = ?
        ORDER BY started_at DESC LIMIT ?
        """,
        (client_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


# ── Stage transitions ─────────────────────────────────────────────────────────

def advance_to_stage1(state: RunState) -> RunState:
    """Begin data collection."""
    state.stage      = RunStage.STAGE1_COLLECTING
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    log.info("Stage 1 started", run_id=state.run_id)
    return state


def complete_stage1(state: RunState, summary: DataCollectionSummary) -> RunState:
    """
    Mark Stage 1 complete.
    In GATED mode: sets stage to STAGE1_COMPLETE and waits.
    In EXPRESS mode: immediately returns with STAGE2_ANALYSING.
    """
    summary.collected_at = datetime.utcnow().isoformat()
    state.data_summary   = summary
    state.updated_at     = datetime.utcnow().isoformat()

    if state.mode == RunMode.GATED:
        state.stage = RunStage.STAGE1_COMPLETE
        log.info("Stage 1 complete — awaiting user approval", run_id=state.run_id)
    else:
        state.stage = RunStage.STAGE2_ANALYSING
        log.info("Stage 1 complete — auto-advancing (EXPRESS mode)", run_id=state.run_id)

    _save_run(state)
    return state


def approve_stage1(run_id: str) -> Optional[RunState]:
    """
    User clicked 'Proceed to Analysis' at the Stage 1 gate.
    Advances to Stage 2.
    """
    state = load_run(run_id)
    if not state or state.stage != RunStage.STAGE1_COMPLETE:
        return None
    state.stage      = RunStage.STAGE2_ANALYSING
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    log.info("Stage 1 gate approved by user", run_id=run_id)
    return state


def complete_stage2(state: RunState, summary: AnalysisSummary) -> RunState:
    """
    Mark Stage 2 complete.
    In GATED mode: sets stage to STAGE2_COMPLETE and waits.
    In EXPRESS mode: immediately advances to STAGE3_REPORTING.
    """
    summary.analysed_at    = datetime.utcnow().isoformat()
    state.analysis_summary = summary
    state.updated_at       = datetime.utcnow().isoformat()

    if state.mode == RunMode.GATED:
        state.stage = RunStage.STAGE2_COMPLETE
        log.info("Stage 2 complete — awaiting user approval", run_id=state.run_id)
    else:
        state.stage = RunStage.STAGE3_REPORTING
        log.info("Stage 2 complete — auto-advancing (EXPRESS mode)", run_id=state.run_id)

    _save_run(state)
    return state


def approve_stage2(run_id: str, selected_modules: Optional[list] = None) -> Optional[RunState]:
    """
    User clicked 'Generate Report' at the Stage 2 gate.
    selected_modules: if provided, marks any unselected modules as skipped.
    Advances to Stage 3.
    """
    state = load_run(run_id)
    if not state or state.stage != RunStage.STAGE2_COMPLETE:
        return None

    if selected_modules is not None:
        deselected = [
            m for m in state.modules_complete
            if m not in selected_modules
        ]
        state.modules_skipped.extend(deselected)
        for m in deselected:
            if m in state.modules_complete:
                state.modules_complete.remove(m)

    state.stage      = RunStage.STAGE3_REPORTING
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    log.info("Stage 2 gate approved by user", run_id=run_id)
    return state


def complete_run(state: RunState) -> RunState:
    """Mark the entire run as complete."""
    now                = datetime.utcnow().isoformat()
    state.stage        = RunStage.COMPLETE
    state.completed_at = now
    state.updated_at   = now
    _save_run(state)
    log.info("Run complete", run_id=state.run_id, business=state.business_name)
    return state


def fail_run(run_id: str, error: str) -> Optional[RunState]:
    """Mark a run as failed with an error message."""
    state = load_run(run_id)
    if not state:
        return None
    state.stage      = RunStage.FAILED
    state.error      = error
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    log.error("Run failed", run_id=run_id, error=error[:200])
    return state


def cancel_run(run_id: str) -> Optional[RunState]:
    """
    Cancel a run. Three things happen, in order:
      1. Set the cooperative cancellation flag so the stage worker stops at its
         next checkpoint and stops overwriting the FAILED state.
      2. Force-cancel the registered asyncio task so it's interrupted at its next
         await point immediately (rather than waiting for the next poll).
      3. Mark the run FAILED in the DB so the UI reflects the cancellation now.
    """
    request_cancellation(run_id)

    task = _RUNNING_TASKS.get(run_id)
    if task is not None and not task.done():
        try:
            task.cancel()
        except Exception as e:
            log.warning("Could not force-cancel run task", run_id=run_id, error=str(e)[:80])

    state = fail_run(run_id, "Cancelled by user")
    log.info("Run cancellation requested", run_id=run_id)
    return state


def pause_run(run_id: str) -> Optional[RunState]:
    """User explicitly paused the run."""
    state = load_run(run_id)
    if not state:
        return None
    state.stage      = RunStage.PAUSED
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    return state


def resume_run(run_id: str) -> Optional[RunState]:
    """
    Resume a paused or interrupted run.
    Re-queues failed modules and continues from current stage.
    Does NOT re-run completed modules.
    """
    state = load_run(run_id)
    if not state:
        return None

    # Re-queue failed modules
    for mod_id in list(state.modules_failed.keys()):
        state.modules_pending.append(mod_id)
        del state.modules_failed[mod_id]

    # Clear any stuck running state
    state.modules_pending.extend(state.modules_running)
    state.modules_running = []

    # Determine correct stage to resume from
    if state.stage in (RunStage.PAUSED, RunStage.FAILED):
        if state.data_summary is None:
            state.stage = RunStage.STAGE1_COLLECTING
        elif state.analysis_summary is None:
            state.stage = RunStage.STAGE2_ANALYSING
        else:
            state.stage = RunStage.STAGE3_REPORTING

    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    log.info("Run resumed", run_id=run_id, stage=state.stage.value)
    return state


# ── Module state updates ──────────────────────────────────────────────────────

def mark_module_running(run_id: str, module_id: str) -> None:
    """Mark a module as currently executing."""
    state = load_run(run_id)
    if not state: return
    if module_id in state.modules_pending:
        state.modules_pending.remove(module_id)
    if module_id not in state.modules_running:
        state.modules_running.append(module_id)
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)


def mark_module_complete(run_id: str, module_id: str) -> None:
    """Mark a module as successfully completed."""
    state = load_run(run_id)
    if not state: return
    for lst in (state.modules_running, state.modules_pending):
        if module_id in lst:
            lst.remove(module_id)
    if module_id not in state.modules_complete:
        state.modules_complete.append(module_id)
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    log.info("Module complete", run_id=run_id, module=module_id)


def mark_module_failed(run_id: str, module_id: str, error: str) -> None:
    """Mark a module as failed after all retries. Run continues."""
    state = load_run(run_id)
    if not state: return
    for lst in (state.modules_running, state.modules_pending):
        if module_id in lst:
            lst.remove(module_id)
    state.modules_failed[module_id] = error
    state.updated_at = datetime.utcnow().isoformat()
    _save_run(state)
    log.warning("Module failed", run_id=run_id, module=module_id, error=error)


# ── Retry wrapper ─────────────────────────────────────────────────────────────

import asyncio
from typing import Callable, Any

async def with_retry(
    fn: Callable,
    run_id: str,
    module_id: str,
    max_attempts: int = 4,
    is_data_fetch: bool = False,
) -> tuple[Any, Optional[str]]:
    """
    Execute a function with exponential backoff retry.
    Returns (result, error_message).
    error_message is None on success.

    For data fetches (is_data_fetch=True): max 3 attempts.
    For AI calls: max 4 attempts.
    """
    delays = [0, 5, 15, 45]
    attempts = min(max_attempts, 3 if is_data_fetch else 4)

    last_error = None
    for attempt in range(attempts):
        if attempt > 0:
            delay = delays[attempt]
            log.info(
                "Retrying after error",
                run_id=run_id,
                module=module_id,
                attempt=attempt + 1,
                delay=delay,
            )
            await asyncio.sleep(delay)

        try:
            result = await fn() if asyncio.iscoroutinefunction(fn) else fn()
            return result, None
        except Exception as e:
            last_error = str(e)
            log.warning(
                "Attempt failed",
                run_id=run_id,
                module=module_id,
                attempt=attempt + 1,
                error=last_error,
            )

    return None, last_error


# ── Database persistence ──────────────────────────────────────────────────────

def _save_run(state: RunState) -> None:
    """Write the full run state to the database."""
    db = get_db()

    def _json(obj):
        if obj is None: return None
        if isinstance(obj, (list, dict)): return json.dumps(obj)
        try:
            return json.dumps(asdict(obj))
        except Exception:
            return json.dumps(str(obj))

    db.execute(
        """
        INSERT OR REPLACE INTO analysis_runs (
            run_id, client_id, business_name, mode, status,
            modules_requested, modules_pending, modules_running,
            modules_complete, modules_failed, modules_skipped,
            data_summary, analysis_summary,
            started_at, updated_at, completed_at, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            state.run_id,
            state.client_id,
            state.business_name,
            state.mode.value,
            state.stage.value,
            _json(state.modules_requested),
            _json(state.modules_pending),
            _json(state.modules_running),
            _json(state.modules_complete),
            _json(state.modules_failed),
            _json(state.modules_skipped),
            _json(state.data_summary),
            _json(state.analysis_summary),
            state.started_at,
            state.updated_at,
            state.completed_at,
            state.error,
        ),
    )
    db.commit()


def _row_to_state(row) -> RunState:
    """Convert a database row to a RunState object."""

    def _load(val, default):
        if val is None: return default
        try: return json.loads(val)
        except Exception: return default

    def _load_summary(val, cls):
        if val is None: return None
        try:
            d = json.loads(val)
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except Exception:
            return None

    row_dict = dict(row)
    return RunState(
        run_id           = row_dict["run_id"],
        client_id        = row_dict["client_id"],
        business_name    = row_dict["business_name"],
        mode             = RunMode(row_dict["mode"]),
        stage            = RunStage(row_dict["status"]),
        modules_requested = _load(row_dict["modules_requested"], []),
        modules_pending  = _load(row_dict["modules_pending"], []),
        modules_running  = _load(row_dict["modules_running"], []),
        modules_complete = _load(row_dict["modules_complete"], []),
        modules_failed   = _load(row_dict["modules_failed"], {}),
        modules_skipped  = _load(row_dict["modules_skipped"], []),
        data_summary     = _load_summary(row_dict["data_summary"], DataCollectionSummary),
        analysis_summary = _load_summary(row_dict["analysis_summary"], AnalysisSummary),
        started_at       = row_dict["started_at"],
        updated_at       = row_dict["updated_at"],
        completed_at     = row_dict["completed_at"],
        error            = row_dict.get("error"),
    )
