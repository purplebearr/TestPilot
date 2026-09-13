# tasks.py
from huey.contrib.djhuey import db_task, task
import asyncio
from django.db.models import Sum

from .models import ExecutorAgentRun, OrchestrationRun
from .orchestrator import run_orchestrator
from .screenshot_helper import assign_executor_screenshots


@db_task()
def process_executor_screenshots(orchestration_run_id):
    """
    Reconcile ExecutorScreenshot rows for every executor agent run in this
    orchestration run, based on each executor's execution_log.
    """
    print(f"Processing executor screenshots for orchestration run {orchestration_run_id}...")
    orchestration_run = OrchestrationRun.objects.get(id=orchestration_run_id)
    created = assign_executor_screenshots(orchestration_run)
    print(f"Finished processing executor screenshots for orchestration run {orchestration_run_id}. Created {created} new screenshots.")
    return created


def get_presented_screenshots(data):
    """
    Return a list of presented screenshots with their descriptions/captions.

    Output format:
    [
        {
            "screenshot": "01_homepage_initial.png",
            "caption": "Navigated to homepage at http://127.0.0.1:8000"
        },
        ...
    ]
    """
    presented = set(data.get("presented_screenshots", []))
    results = []

    for step in data.get("steps", []):
        description = step.get("description", "")

        for screenshot in step.get("screenshots", []):
            if screenshot in presented:
                results.append({
                    "screenshot": screenshot,
                    "caption": description
                })

    return results


@task()
def run_orchestration_run(orchestration_run_id):
    orchestration_run = OrchestrationRun.objects.get(id=orchestration_run_id)

    prompt = orchestration_run.user_inquiry + orchestration_run.target_url

    result = asyncio.run(
        run_orchestrator(
            prompt,
            orchestration_run_id=str(orchestration_run_id),
        )
    )

    orchestration_run.final_report = result
    orchestration_run.status = OrchestrationRun.Status.SUCCEEDED

    executor_usage = ExecutorAgentRun.objects.filter(
        task_set__orchestration_run=orchestration_run
    ).aggregate(
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
    )

    executor_input = executor_usage["input_tokens"] or 0
    executor_output = executor_usage["output_tokens"] or 0

    orchestration_run.total_input_tokens = (
        orchestration_run.orchestrator_input_tokens + executor_input
    )
    orchestration_run.total_output_tokens = (
        orchestration_run.orchestrator_output_tokens + executor_output
    )

    orchestration_run.save()

    # Enqueue screenshot reconciliation as its own task instead of running
    # it inline — keeps a screenshot failure from affecting the run's
    # already-saved status/report, and gets it off this worker slot.
    process_executor_screenshots(orchestration_run_id)

