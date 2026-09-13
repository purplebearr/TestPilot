from pathlib import Path

from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction

import posixpath


from .models import (
    ExecutorAgentRun,
    ExecutorScreenshot,
    OrchestrationRun,
)


def assign_executor_screenshots(orchestration_run: OrchestrationRun) -> int:
    """
    Reconcile ExecutorScreenshot records with the screenshots referenced by
    each executor's execution_log.

    Returns the number of screenshots created.
    """

    created_count = 0

    executor_runs = (
        ExecutorAgentRun.objects
        .filter(task_set__orchestration_run=orchestration_run)
        .select_related("task_set", "task_set__orchestration_run")
    )

    for executor_run in executor_runs:
        execution_log = executor_run.execution_log or {}
        steps = execution_log.get("steps") or []

        if not isinstance(steps, list):
            continue

        # Relative to MEDIA_ROOT (or wherever `default_storage` points),
        # NOT to the process's cwd. This is what screenshot_upload_path()
        # produces for the ImageField, so we mirror it exactly here.
        relative_screenshot_dir = Path("orchestration_runs") / str(
            orchestration_run.id
        ) / str(executor_run.task_set_id) / str(executor_run.id) / "screenshots"

        with transaction.atomic():
            for step_data in steps:
                if not isinstance(step_data, dict):
                    continue

                step_number = step_data.get("step", 0)
                timestamp = step_data.get("timestamp")
                action = step_data.get("action", "")
                description = step_data.get("description", "")
                screenshots = step_data.get("screenshots") or []

                if not isinstance(screenshots, list):
                    continue

                for screenshot_filename in screenshots:
                    if not screenshot_filename:
                        continue

                    screenshot_filename = Path(screenshot_filename).name
                    # Build with posixpath, not pathlib.Path — storage paths must always
                    # use "/" so this matches upload_to()'s f-string exactly, on every OS.
                    relative_path = posixpath.join(
                        "orchestration_runs",
                        str(orchestration_run.id),
                        str(executor_run.task_set_id),
                        str(executor_run.id),
                        "screenshots",
                        screenshot_filename,
                    )

                    if not default_storage.exists(relative_path):
                        continue

                    already_exists = ExecutorScreenshot.objects.filter(
                        executor_run=executor_run,
                        image=relative_path,          # exact match, not endswith
                    ).exists()

                    if already_exists:
                        continue

                    screenshot = ExecutorScreenshot(
                        executor_run=executor_run,
                        step_number=step_number,
                        caption=description,
                        taken_at=timestamp,
                    )
                    screenshot.image.name = relative_path   # assign, don't re-save
                    screenshot.save()

                    created_count += 1

    return created_count