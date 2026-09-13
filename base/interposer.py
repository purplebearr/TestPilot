"""
interposer.py

Bridges the async agentic runtime (orchestrator.py / launcher.py /
agent_config.py) with the synchronous Django ORM. Every function here is
already wrapped in `sync_to_async`, so agent code just does e.g.:

    await interposer.create_executor_run(...)

Ownership model
----------------
- OrchestrationRun rows are created *outside* this module -- by whatever
  Django view/Huey task kicks off a run (it generates the UUID first, then
  hands it down as `orchestration_run_id`). This module only ever updates
  an existing OrchestrationRun by id; it never creates one.

- AgenticTaskSet rows ARE created here, by `create_task_set()`, called once
  at the top of `run_agentic_task()` in launcher.py -- i.e. once per round
  of parallel executor agents. The round number is assigned automatically
  from how many task sets already exist for the orchestration run, unless
  you pass one explicitly.

- ExecutorAgentRun rows ARE created here too, by `create_executor_run()`,
  called from inside `create_agent()` in agent_config.py -- i.e. at the
  moment an executor agent actually comes into existence, before it does
  any work. Its primary key is the same `agent_id` used everywhere else in
  the codebase (agent_runs/{agent_id}.json, agent_screenshots/{agent_id}/,
  etc.), so no separate id-mapping is ever needed.

Failure handling
-----------------
Every "mark *" / "complete_*" / "fail_*" function is defensive: if the row
it's looking for doesn't exist (which shouldn't happen, but a DB hiccup or
a race during shutdown shouldn't be able to crash a live browser test), it
logs a warning and returns quietly instead of raising.
"""

import logging
from typing import Optional

from asgiref.sync import sync_to_async
from django.db.models import Sum
from django.utils import timezone

from .models import (
    OrchestrationRun,
    AgenticTaskSet,
    ExecutorAgentRun,
    ExecutorScreenshot,
    Persona,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

def extract_token_usage(result) -> tuple[int, int]:
    """
    Best-effort (input_tokens, output_tokens) extraction from a Strands
    AgentResult (or, in an except block where you only have the Agent
    instance itself, an Agent -- some versions surface the same
    accumulated usage there too) via `result.metrics.accumulated_usage`.

    Returns (0, 0) on anything unexpected -- missing attribute, wrong
    shape, whatever -- rather than raising, since exact metrics
    attribute/shape can shift across SDK versions and token bookkeeping
    should never be able to take down a test run.
    """
    try:
        usage = result.metrics.accumulated_usage
        input_tokens = int(usage.get("inputTokens", 0) or 0)
        output_tokens = int(usage.get("outputTokens", 0) or 0)
        return input_tokens, output_tokens
    except Exception:
        return 0, 0


# ---------------------------------------------------------------------------
# OrchestrationRun
# ---------------------------------------------------------------------------
# Rows already exist by the time any of these are called (created by the
# view/task that generated orchestration_run_id). We only ever update.

@sync_to_async
def start_orchestration_run(orchestration_run_id: str, model_id: str = "") -> None:
    """Call once, right before orchestrator_agent.invoke_async() is awaited."""
    try:
        run = OrchestrationRun.objects.get(id=orchestration_run_id)
    except OrchestrationRun.DoesNotExist:
        logger.warning(
            "start_orchestration_run: no OrchestrationRun with id=%s",
            orchestration_run_id,
        )
        return

    run.status = OrchestrationRun.Status.RUNNING
    run.started_at = timezone.now()
    if model_id:
        run.model_id = model_id
    run.save(update_fields=["status", "started_at", "model_id"])


def _executor_token_totals(orchestration_run_id: str) -> tuple[int, int]:
    """Sum of AgenticTaskSet.input_tokens/output_tokens across every task
    set (every round of executors) this orchestration run has spawned so
    far. Each AgenticTaskSet's own totals are themselves a sum over its
    executor_runs, kept up to date by complete_task_set()."""
    totals = AgenticTaskSet.objects.filter(
        orchestration_run_id=orchestration_run_id
    ).aggregate(total_input=Sum("input_tokens"), total_output=Sum("output_tokens"))
    return totals["total_input"] or 0, totals["total_output"] or 0


@sync_to_async
def complete_orchestration_run(
    orchestration_run_id: str,
    final_report: str,
    orchestrator_input_tokens: int = 0,
    orchestrator_output_tokens: int = 0,
) -> None:
    """
    orchestrator_input_tokens/orchestrator_output_tokens should come from
    extract_token_usage() on the AgentResult of the orchestrator's own
    invoke_async() call. Executor-side totals are pulled from the DB
    (every AgenticTaskSet this run has spawned), so the caller never has
    to track those itself.
    """
    try:
        run = OrchestrationRun.objects.get(id=orchestration_run_id)
    except OrchestrationRun.DoesNotExist:
        logger.warning(
            "complete_orchestration_run: no OrchestrationRun with id=%s",
            orchestration_run_id,
        )
        return

    executor_input, executor_output = _executor_token_totals(orchestration_run_id)

    run.status = OrchestrationRun.Status.SUCCEEDED
    run.final_report = final_report or ""
    run.orchestrator_input_tokens = orchestrator_input_tokens
    run.orchestrator_output_tokens = orchestrator_output_tokens
    run.total_input_tokens = orchestrator_input_tokens + executor_input
    run.total_output_tokens = orchestrator_output_tokens + executor_output
    run.completed_at = timezone.now()
    run.save(
        update_fields=[
            "status",
            "final_report",
            "orchestrator_input_tokens",
            "orchestrator_output_tokens",
            "total_input_tokens",
            "total_output_tokens",
            "completed_at",
        ]
    )


@sync_to_async
def fail_orchestration_run(
    orchestration_run_id: str,
    error_message: str,
    orchestrator_input_tokens: int = 0,
    orchestrator_output_tokens: int = 0,
) -> None:
    """
    Same token bookkeeping as complete_orchestration_run(), so a run that
    fails partway through still records however many tokens it burned
    before dying -- orchestrator_input_tokens/output_tokens default to 0
    since a failed invoke_async() may not have a usable AgentResult to
    pull them from.
    """
    try:
        run = OrchestrationRun.objects.get(id=orchestration_run_id)
    except OrchestrationRun.DoesNotExist:
        logger.warning(
            "fail_orchestration_run: no OrchestrationRun with id=%s",
            orchestration_run_id,
        )
        return

    executor_input, executor_output = _executor_token_totals(orchestration_run_id)

    run.status = OrchestrationRun.Status.FAILED
    run.error_message = str(error_message)[:5000]
    run.orchestrator_input_tokens = orchestrator_input_tokens
    run.orchestrator_output_tokens = orchestrator_output_tokens
    run.total_input_tokens = orchestrator_input_tokens + executor_input
    run.total_output_tokens = orchestrator_output_tokens + executor_output
    run.completed_at = timezone.now()
    run.save(
        update_fields=[
            "status",
            "error_message",
            "orchestrator_input_tokens",
            "orchestrator_output_tokens",
            "total_input_tokens",
            "total_output_tokens",
            "completed_at",
        ]
    )


# ---------------------------------------------------------------------------
# AgenticTaskSet
# ---------------------------------------------------------------------------

@sync_to_async
def create_task_set(
    orchestration_run_id: str,
    prompt: str,
    num_agents_requested: int,
    round_number: Optional[int] = None,
) -> str:
    """
    Create the AgenticTaskSet row for one round of executor agents.

    Returns the new row's id as a str -- launcher.py should use this
    directly as `agentic_task_set_id` (the archive directory name), instead
    of minting its own uuid, so the DB row and the on-disk folder always
    agree.

    round_number defaults to "one more than however many task sets this
    orchestration run already has", so sequential calls to run_agentic_task
    within the same run are numbered 1, 2, 3, ... automatically.
    """
    if round_number is None:
        round_number = (
            AgenticTaskSet.objects.filter(
                orchestration_run_id=orchestration_run_id
            ).count()
            + 1
        )

    task_set = AgenticTaskSet.objects.create(
        orchestration_run_id=orchestration_run_id,
        round_number=round_number,
        prompt=prompt,
        num_agents_requested=num_agents_requested,
        status=AgenticTaskSet.Status.RUNNING,
    )
    return str(task_set.id)


@sync_to_async
def complete_task_set(
    task_set_id: str,
    synthesized_summary: str,
    num_agents_spawned: int,
) -> None:
    """
    Call once, after archive_agent_outputs() + summarize_agent_runs() have
    both finished for this task set. Rolls the set's overall status up from
    however its individual executors landed, rather than taking a status
    as an argument, so it can't drift out of sync with the executor rows.
    """
    try:
        task_set = AgenticTaskSet.objects.get(id=task_set_id)
    except AgenticTaskSet.DoesNotExist:
        logger.warning("complete_task_set: no AgenticTaskSet with id=%s", task_set_id)
        return

    executor_statuses = set(
        task_set.executor_runs.values_list("status", flat=True)
    )
    failed_like = {
        ExecutorAgentRun.Status.FAILED,
        ExecutorAgentRun.Status.MISSING_OUTPUT,
    }

    if not executor_statuses:
        status = AgenticTaskSet.Status.FAILED
    elif executor_statuses <= failed_like:
        status = AgenticTaskSet.Status.FAILED
    elif executor_statuses & failed_like:
        status = AgenticTaskSet.Status.PARTIAL
    else:
        status = AgenticTaskSet.Status.SUCCEEDED

    token_totals = task_set.executor_runs.aggregate(
        total_input=Sum("input_tokens"), total_output=Sum("output_tokens")
    )

    task_set.status = status
    task_set.synthesized_summary = synthesized_summary or ""
    task_set.num_agents_spawned = num_agents_spawned
    task_set.input_tokens = token_totals["total_input"] or 0
    task_set.output_tokens = token_totals["total_output"] or 0
    task_set.completed_at = timezone.now()
    task_set.save(
        update_fields=[
            "status",
            "synthesized_summary",
            "num_agents_spawned",
            "input_tokens",
            "output_tokens",
            "completed_at",
        ]
    )


@sync_to_async
def fail_task_set(task_set_id: str, error_message: str = "") -> None:
    """For the rare case run_agentic_task blows up before it can archive
    anything at all (e.g. asyncio.gather itself raised)."""
    try:
        task_set = AgenticTaskSet.objects.get(id=task_set_id)
    except AgenticTaskSet.DoesNotExist:
        logger.warning("fail_task_set: no AgenticTaskSet with id=%s", task_set_id)
        return

    task_set.status = AgenticTaskSet.Status.FAILED
    task_set.completed_at = timezone.now()
    task_set.save(update_fields=["status", "completed_at"])


# ---------------------------------------------------------------------------
# ExecutorAgentRun
# ---------------------------------------------------------------------------

@sync_to_async
def create_executor_run(
    task_set_id: str,
    agent_id: str,
    index: int,
    prompt: str,
    persona_name: Optional[str] = None,
) -> str:
    """
    Create the ExecutorAgentRun row for one executor agent. Called from
    create_agent() in agent_config.py, at the moment the agent object is
    built -- so this should be the *first* thing that happens, before the
    browser/model are even constructed, so a row exists even if agent
    construction itself fails right after.

    `id` is set explicitly to `agent_id` (rather than left to autogenerate)
    so it matches the agent_id used everywhere else: agent_runs/{id}.json,
    agent_screenshots/{id}/, etc.
    """
    persona = None
    if persona_name:
        persona = Persona.objects.filter(name=persona_name, is_active=True).first()
        if persona is None:
            logger.warning(
                "create_executor_run: persona %r not found or inactive; "
                "continuing without one",
                persona_name,
            )

    executor_run = ExecutorAgentRun.objects.create(
        id=agent_id,
        task_set_id=task_set_id,
        persona=persona,
        index=index,
        prompt=prompt,
        status=ExecutorAgentRun.Status.RUNNING,
        started_at=timezone.now(),
    )
    return str(executor_run.id)


@sync_to_async
def complete_executor_run(
    agent_id: str,
    summary_text: str = "",
    execution_log: Optional[dict] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """
    Call after agent.invoke_async() returns without raising. If the agent
    never got around to calling write_summary (e.g. it errored out
    mid-task without raising all the way up), summary_text will be empty
    and the row is marked MISSING_OUTPUT rather than SUCCEEDED.

    input_tokens/output_tokens should come from
    extract_token_usage(response) on this executor's AgentResult -- note
    this row's tokens do NOT automatically propagate up to its
    AgenticTaskSet/OrchestrationRun; that rollup happens when
    complete_task_set() / complete_orchestration_run() run.
    """
    try:
        executor_run = ExecutorAgentRun.objects.get(id=agent_id)
    except ExecutorAgentRun.DoesNotExist:
        logger.warning("complete_executor_run: no ExecutorAgentRun with id=%s", agent_id)
        return

    executor_run.status = (
        ExecutorAgentRun.Status.SUCCEEDED
        if summary_text
        else ExecutorAgentRun.Status.MISSING_OUTPUT
    )
    executor_run.summary_text = summary_text or ""
    if execution_log is not None:
        executor_run.execution_log = execution_log
    executor_run.input_tokens = input_tokens
    executor_run.output_tokens = output_tokens
    executor_run.completed_at = timezone.now()
    executor_run.save(
        update_fields=[
            "status",
            "summary_text",
            "execution_log",
            "input_tokens",
            "output_tokens",
            "completed_at",
        ]
    )


@sync_to_async
def fail_executor_run(
    agent_id: str,
    error_message: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """
    Call from the except block around agent.invoke_async(). Token args
    default to 0 since a failed invocation may not have a usable
    AgentResult to pull real numbers from -- pass real ones if the caller
    happens to have them (e.g. extracted from the Agent instance itself).
    """
    try:
        executor_run = ExecutorAgentRun.objects.get(id=agent_id)
    except ExecutorAgentRun.DoesNotExist:
        logger.warning("fail_executor_run: no ExecutorAgentRun with id=%s", agent_id)
        return

    executor_run.status = ExecutorAgentRun.Status.FAILED
    executor_run.error_message = str(error_message)[:5000]
    executor_run.input_tokens = input_tokens
    executor_run.output_tokens = output_tokens
    executor_run.completed_at = timezone.now()
    executor_run.save(
        update_fields=[
            "status",
            "error_message",
            "input_tokens",
            "output_tokens",
            "completed_at",
        ]
    )


# ---------------------------------------------------------------------------
# ExecutorScreenshot (optional, best-effort)
# ---------------------------------------------------------------------------

@sync_to_async
def record_screenshots(agent_id: str, screenshots: list[dict]) -> int:
    """
    Register ExecutorScreenshot rows for files that have ALREADY been moved
    to their final location on disk by archive_agent_outputs() (i.e. the
    path each one lives at already matches what screenshot_upload_path() in
    models.py would generate). This only writes DB rows -- it never touches
    the filesystem.

    screenshots: one dict per screenshot, in the order they should be
        displayed. Each dict:
            "relpath" (str, required): path relative to MEDIA_ROOT, e.g.
                "orchestration_runs/{run_id}/{task_set_id}/{agent_id}/screenshots/0001.png"
            "step_number" (int, optional): defaults to the dict's position
                in the list, so callers that can't determine a real step
                number still get a stable, correctly ordered sequence.
            "caption" (str, optional): defaults to "".
            "taken_at" (datetime, optional): defaults to None.

    Returns the number of ExecutorScreenshot rows created. If the
    ExecutorAgentRun doesn't exist, logs a warning and returns 0 -- same
    "never crash a live browser test" contract as the other interposer
    functions.

    Idempotency note: this always creates new rows. Callers should only
    invoke it once per agent_id (archive_agent_outputs()'s screenshot move
    is itself a one-time, run-once-per-agent step, so this lines up
    naturally) -- calling it twice for the same agent will duplicate rows.
    """
    try:
        executor_run = ExecutorAgentRun.objects.get(id=agent_id)
    except ExecutorAgentRun.DoesNotExist:
        logger.warning("record_screenshots: no ExecutorAgentRun with id=%s", agent_id)
        return 0

    if not screenshots:
        return 0

    shots = []
    for position, item in enumerate(screenshots):
        relpath = item.get("relpath")
        if not relpath:
            logger.warning(
                "record_screenshots: skipping entry with no relpath for agent_id=%s",
                agent_id,
            )
            continue

        shot = ExecutorScreenshot(
            executor_run=executor_run,
            step_number=item.get("step_number", position),
            caption=(item.get("caption") or "")[:255],
            taken_at=item.get("taken_at"),
        )
        # File is already on disk at MEDIA_ROOT/relpath (archive_agent_outputs
        # moved it there); just point the field at it rather than re-saving.
        shot.image.name = relpath
        shots.append(shot)

    created = ExecutorScreenshot.objects.bulk_create(shots)
    return len(created)


# ---------------------------------------------------------------------------
# Persona (read-only helper)
# ---------------------------------------------------------------------------

@sync_to_async
def get_persona_prompt_fragment(persona_name: str) -> Optional[str]:
    """
    Look up an active Persona's prompt_fragment by name, for callers that
    want to inject it into an executor's prompt before invoking the agent.
    Returns None if the persona doesn't exist or isn't active.
    """
    persona = Persona.objects.filter(name=persona_name, is_active=True).first()
    return persona.prompt_fragment if persona else None