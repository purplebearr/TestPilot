import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

from strands import tool


class ExecutionLogger:
    """
    Concise, durable execution logger.

    Each execution produces:
      - One JSON execution log
      - One TXT final summary
    """

    def __init__(self, output_dir: str = "agent_runs", agent_id: str | None = None):
        os.makedirs(output_dir, exist_ok=True)

        # Use the agent_id as the shared identifier for both the
        # execution log and the summary, if provided. Otherwise fall
        # back to a fresh random UUID (keeps this class usable
        # standalone / in tests without an agent_id).
        shared_id = agent_id if agent_id else str(uuid.uuid4())

        self.execution_log_uuid = shared_id
        self.summary_uuid = shared_id

        self.execution_log_path = os.path.join(
            output_dir,
            f"{self.execution_log_uuid}.json",
        )

        self.summary_path = os.path.join(
            output_dir,
            f"{self.summary_uuid}.txt",
        )

        self._data = {
            "execution_log_uuid": self.execution_log_uuid,
            "summary_uuid": self.summary_uuid,
            "started_at": self._now(),
            "steps": [],
        }

        self._save_json()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _save_json(self):
        """Atomically persist the execution log."""
        directory = os.path.dirname(self.execution_log_path)

        fd, temp_path = tempfile.mkstemp(
            dir=directory,
            prefix=".execution_",
            suffix=".tmp",
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, self.execution_log_path)

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @tool
    def record_step(
        self,
        action: str,
        description: str,
        status: str = "success",
        details: str = "",
        screenshots: list[str] | None = None,
    ) -> str:
        """
        Record a meaningful action performed by the agent.

        Args:
            action: Short action type, e.g. navigate, click, type, inspect.
            description: Concise description of what happened.
            status: success or failure.
            details: Optional concise context, especially for failures.
            screenshots: Filenames (not paths) of any screenshots taken for
                this step, e.g. ["02_signup_form_filled.png"]. Only include
                filenames you actually just saved via the screenshot tool.
        """

        status = status.lower().strip()

        if status not in {"success", "failure"}:
            return "Logging failed: status must be 'success' or 'failure'."

        step = {
            "step": len(self._data["steps"]) + 1,
            "timestamp": self._now(),
            "action": action,
            "status": status,
            "description": description,
        }

        if details:
            step["details"] = details

        screenshots = [s.strip() for s in (screenshots or []) if s and s.strip()]
        if screenshots:
            step["screenshots"] = screenshots
            self._data.setdefault("all_screenshots", [])
            for name in screenshots:
                if name not in self._data["all_screenshots"]:
                    self._data["all_screenshots"].append(name)

        self._data["steps"].append(step)
        self._save_json()

        # console print uuid and step completed and status
        print(
            f"Step {step['step']} recorded ({status}). "
            f"execution_log_uuid={self.execution_log_uuid}"
        )
        return f"Step {step['step']} recorded ({status})."

    @tool
    def write_summary(
        self,
        summary: str,
        screenshots: list[str] | None = None,
    ) -> str:
        """
        Write the final execution report.

        The supplied summary should reflect the agent's own memory of
        what happened. This tool automatically adds facts derived from
        the execution log.

        Args:
            summary: Free-text account of what was tested and the outcome.
            screenshots: Curated filenames (not paths) of the screenshots
                that best show the outcome of this task, in the order they
                should be presented. Pick from screenshots you actually
                logged via record_step — don't dump every screenshot taken,
                just the ones a human reviewer needs to see.
        """

        steps = self._data["steps"]

        successes = sum(
            step["status"] == "success"
            for step in steps
        )

        failures = [
            step for step in steps
            if step["status"] == "failure"
        ]

        actions = {}
        for step in steps:
            action = step["action"]
            actions[action] = actions.get(action, 0) + 1

        all_logged = set(self._data.get("all_screenshots", []))
        presented = []
        for name in (screenshots or []):
            name = name.strip()
            if name and name in all_logged and name not in presented:
                presented.append(name)

        self._data["presented_screenshots"] = presented
        self._save_json()

        started_at = datetime.fromisoformat(
            self._data["started_at"]
        )

        completed_at = datetime.now(timezone.utc)
        duration = completed_at - started_at

        report = [
            "EXECUTION SUMMARY",
            "",
            summary.strip(),
            "",
            "LOGGED RESULTS",
            f"- Steps: {len(steps)}",
            f"- Successful: {successes}",
            f"- Failed: {len(failures)}",
            f"- Duration: {duration}",
        ]

        if actions:
            action_summary = ", ".join(
                f"{name}={count}"
                for name, count in actions.items()
            )
            report.append(f"- Actions: {action_summary}")

        if failures:
            report.extend(["", "ISSUES"])
            for step in failures:
                issue = f"- {step['action']}: {step['description']}"
                if step.get("details"):
                    issue += f" ({step['details']})"
                report.append(issue)

        if presented:
            report.extend(["", "SCREENSHOTS"])
            for name in presented:
                report.append(f"- {name}")

        report.extend([
            "",
            f"Execution log: {self.execution_log_uuid}.json",
            f"Summary ID: {self.summary_uuid}",
        ])

        with open(
            self.summary_path,
            "w",
            encoding="utf-8",
        ) as f:
            f.write("\n".join(report))
            f.write("\n")

        return (
            "Summary written successfully. "
            f"summary_uuid={self.summary_uuid}"
        )