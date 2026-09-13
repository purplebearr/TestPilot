# models.py
import uuid
from django.db import models
from django.conf import settings


class OrchestrationRun(models.Model):
    """
    One top-level invocation of the orchestrator agent, corresponding to
    ORCHESTRATION_RUN_ID in launcher.py / one call to orchestrator_agent.invoke_async().
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="orchestration_runs", null=True, blank=True,
    )

    # What the user actually asked for, e.g. "test how easy it is to log in"
    user_inquiry = models.TextField()
    target_url = models.URLField(blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)

    # Final synthesized report the orchestrator agent hands back
    final_report = models.TextField(blank=True)

    model_id = models.CharField(max_length=255, blank=True)  # AWS_BEDROCK_MODEL_ID used

    # Token usage. "orchestrator_*" is just the orchestrator agent's own
    # invoke_async call (its planning/tool-selection reasoning) -- it does
    # NOT include executor agents, since those are separate Agent objects
    # with their own model calls. "total_*" is orchestrator + every
    # executor across every AgenticTaskSet this run spawned, and is what
    # you want for "how many tokens did this whole run cost". Both are
    # denormalized (computed and stored at completion time) rather than
    # computed on read, so historical runs stay cheap to list/aggregate.
    orchestrator_input_tokens = models.PositiveIntegerField(default=0)
    orchestrator_output_tokens = models.PositiveIntegerField(default=0)
    total_input_tokens = models.PositiveIntegerField(default=0)
    total_output_tokens = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Run {self.id} ({self.status})"

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


class Persona(models.Model):
    """
    Optional preset tester profile (e.g. 'impatient user', 'screen-reader
    user') that can be injected into an executor's system prompt or task
    instructions.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    prompt_fragment = models.TextField(
        help_text="Text appended/injected into the executor's prompt to induce this persona's behavior."
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class AgenticTaskSet(models.Model):
    """
    One call to run_agentic_task() — i.e. one round of N parallel executor
    agents spawned against the same/related prompt. Corresponds to
    agentic_task_set_id in launcher.py.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial failure"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    orchestration_run = models.ForeignKey(
        OrchestrationRun, on_delete=models.CASCADE, related_name="task_sets"
    )

    # Ordering within the run (round 1, round 2, ...) — you cap this at 2
    # rounds in TASK_PROMPT, but model it generally.
    round_number = models.PositiveIntegerField(default=1)

    prompt = models.TextField()  # the narrow prompt passed to run_agentic_task
    num_agents_requested = models.PositiveSmallIntegerField(default=3)
    num_agents_spawned = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # summary.txt contents — the Bedrock-synthesized combination of all
    # executor .txt summaries in this set
    synthesized_summary = models.TextField(blank=True)

    # Sum of input_tokens/output_tokens across this set's executor_runs.
    # Computed and stored when the task set completes.
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["orchestration_run", "round_number", "created_at"]

    def __str__(self):
        return f"TaskSet {self.id} (round {self.round_number})"

    @property
    def archive_path(self) -> str:
        return f"orchestration_runs/{self.orchestration_run_id}/{self.id}"


class ExecutorAgentRun(models.Model):
    """
    One executor agent's run within a task set. Corresponds to a single
    agent_id, and to {agent_id}.json / {agent_id}.txt / screenshots/ on disk.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        MISSING_OUTPUT = "missing_output", "Missing output"  # agent errored before write_summary

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # == agent_id
    task_set = models.ForeignKey(
        AgenticTaskSet, on_delete=models.CASCADE, related_name="executor_runs"
    )
    persona = models.ForeignKey(
        Persona, on_delete=models.SET_NULL, null=True, blank=True, related_name="executor_runs"
    )

    index = models.PositiveSmallIntegerField()  # 1-based spawn order within the set
    prompt = models.TextField()  # actual prompt this executor received

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)

    # Raw {agent_id}.json execution log (steps, tool calls, etc from ExecutionLogger)
    execution_log = models.JSONField(null=True, blank=True)

    # {agent_id}.txt — the agent's own write_summary() output
    summary_text = models.TextField(blank=True)

    # From this executor's AgentResult.metrics.accumulated_usage
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["task_set", "index"]
        constraints = [
            models.UniqueConstraint(fields=["task_set", "index"], name="unique_index_per_task_set")
        ]

    def __str__(self):
        return f"Executor {self.id} (#{self.index}, {self.status})"


def screenshot_upload_path(instance: "ExecutorScreenshot", filename: str) -> str:
    run_id = instance.executor_run.task_set.orchestration_run_id
    task_set_id = instance.executor_run.task_set_id
    agent_id = instance.executor_run_id
    return f"orchestration_runs/{run_id}/{task_set_id}/{agent_id}/screenshots/{filename}"


class ExecutorScreenshot(models.Model):
    """
    One screenshot captured during an executor run (from
    ScopedLocalChromiumBrowser). Ordered sequence within the run.
    """

    executor_run = models.ForeignKey(
        ExecutorAgentRun, on_delete=models.CASCADE, related_name="screenshots"
    )
    image = models.ImageField(upload_to=screenshot_upload_path, max_length=255)
    step_number = models.PositiveIntegerField(default=0)
    caption = models.CharField(max_length=255, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["executor_run", "step_number"]

    def __str__(self):
        return f"Screenshot {self.step_number} for {self.executor_run_id}"