import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from dotenv import load_dotenv
from openai import OpenAI

from .agent_config import create_agent
from . import interposer
from strands import tool


load_dotenv()


# ---------------------------------------------------------
# Orchestration run layout
# ---------------------------------------------------------
#
# One orchestration_run_id = one orchestration run. The ID is supplied by
# the caller (e.g. a Django view/Huey task passing in its own UUID) via
# make_run_agentic_task() below, rather than being generated once at import
# time — that way multiple runs can be in flight in the same process without
# clobbering each other's output. Everything an executor agent produces
# lives, at write time, in the flat directories the agent/browser/logger
# already know about (agent_runs/, agent_screenshots/{agent_id}/). Once a
# run_agentic_task() call finishes, archive_agent_outputs() moves each
# executor's files into:
#
#   MEDIA_ROOT/orchestration_runs/{orchestration_run_id}/{agentic_task_set_id}/{agent_id}/
#       {agent_id}.json
#       {agent_id}.txt
#       screenshots/...
#
# agentic_task_set_id is no longer minted locally with uuid.uuid4() — it's
# the id of the AgenticTaskSet row the interposer creates, so the on-disk
# folder and the DB row always agree (see AgenticTaskSet.archive_path).
#
# The archive root is rooted at MEDIA_ROOT (not the process cwd) because
# screenshots end up behind an ImageField (ExecutorScreenshot.image /
# screenshot_upload_path in models.py), and Django's default FileSystemStorage
# resolves a FieldFile's .name relative to MEDIA_ROOT. If the physical files
# lived anywhere else, the DB rows record_screenshots() creates would point
# at paths the storage backend can't actually find.

AGENT_RUNS_DIR = Path("agent_runs")
AGENT_SCREENSHOTS_DIR = Path("agent_screenshots")
MEDIA_ROOT = Path(settings.MEDIA_ROOT)
ORCHESTRATION_RUNS_ROOT = MEDIA_ROOT / "orchestration_runs"

# Extensions ScopedLocalChromiumBrowser's screenshots are expected to use.
# Anything else sitting in a screenshots/ dir is ignored rather than
# archived as a "screenshot".
_SCREENSHOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# ScopedLocalChromiumBrowser.screenshot() writes filenames in one of two
# shapes (see scoped_browser.py):
#   - auto-generated, when the agent doesn't pass a path: "screenshot_{unix_ts}.png"
#   - agent-chosen, whatever `action.path` the agent supplied, e.g.
#     "0003_clicked_login.png" or "clicked_login_button.png" (and this can
#     include subdirectories, which is why _collect_screenshot_records()
#     below walks the tree with rglob() rather than a flat iterdir()).
_AUTO_SCREENSHOT_RE = re.compile(r"^screenshot_(?P<ts>\d+)$")
_LEADING_NUM_RE = re.compile(r"^(?P<num>\d+)[_\-\s]*(?P<rest>.*)$")

MAX_EXECUTOR_AGENTS_PER_TASK_SET =  int(os.getenv("MAX_EXECUTOR_AGENTS_PER_TASK_SET"))
MAX_TASK_SETS_PER_ORCHESTRATION =  int(os.getenv("MAX_EXECUTOR_AGENTS_PER_TASK_SET"))


# ---------------------------------------------------------
# Chat Completions client -- either Amazon Bedrock Mantle's
# OpenAI-compatible endpoint, or OpenAI directly, depending on
# USE_AWS_BEDROCK. Defaults to true so existing deployments that don't
# set the var keep using Bedrock.
# ---------------------------------------------------------

USE_AWS_BEDROCK = os.getenv("USE_AWS_BEDROCK", "true").lower() != "false"

if USE_AWS_BEDROCK:
    api_key = os.getenv("AWS_BEDROCK_API_KEY")

    if not api_key:
        raise RuntimeError(
            "AWS_BEDROCK_API_KEY is not set."
        )

    base_url = os.getenv(
        "AWS_BEDROCK_BASE_URL",
    )

    model_id = os.getenv("AWS_BEDROCK_MODEL_ID")

    if not model_id:
        raise RuntimeError(
            "AWS_BEDROCK_MODEL_ID is not set."
        )

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )
else:
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set."
        )

    model_id = os.getenv("OPENAI_API_MODEL")

    if not model_id:
        raise RuntimeError(
            "OPENAI_API_MODEL is not set."
        )

    client = OpenAI(
        api_key=api_key,
    )


def _read_text_optional(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Could not read {path}: {e}")
        return ""


def _read_json_optional(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Could not read/parse {path}: {e}")
        return None


def _natural_sort_key(relpath: str) -> list:
    """
    Splits a path into alternating text/number chunks so "2.png" sorts
    before "10.png" (plain string sort would put "10.png" first), and so
    "screenshot_1700000001.png" sorts before "screenshot_1700000010.png" --
    i.e. capture order for the auto-generated filename shape.
    """
    return [
        int(chunk) if chunk.isdigit() else chunk.lower()
        for chunk in re.split(r"(\d+)", relpath)
    ]


def _parse_screenshot_filename(stem: str) -> tuple[int | None, str, int | None]:
    """
    Best-effort (step_number, caption, epoch_seconds) from a screenshot's
    filename (no extension, no subdirectory components).

    - Auto-generated ("screenshot_{unix_ts}"): no caption to extract, but
      the timestamp in the name IS the exact capture time -- more precise
      than the archived file's mtime, which only survives the
      shutil.move()/copy in archive_agent_outputs() by convention.
    - Agent-chosen with a leading number ("0003_clicked_login"): number
      becomes step_number, remainder becomes a humanized caption.
    - Agent-chosen, no leading number ("clicked_login_button"): whole stem
      becomes a humanized caption; step_number is left for the caller to
      fill in from list position.
    """
    auto_match = _AUTO_SCREENSHOT_RE.match(stem)
    if auto_match:
        return None, "", int(auto_match.group("ts"))

    num_match = _LEADING_NUM_RE.match(stem)
    if num_match:
        step_number = int(num_match.group("num"))
        caption = num_match.group("rest").replace("_", " ").replace("-", " ").strip()
        return step_number, caption, None

    caption = stem.replace("_", " ").replace("-", " ").strip()
    return None, caption, None


def _collect_screenshot_records(screenshots_dir: Path, media_root: Path) -> list[dict]:
    """
    Build the list of dicts record_screenshots() expects, for every image
    file under an already-archived screenshots/ directory. Walks
    subdirectories too, since ScopedLocalChromiumBrowser will create them
    if an agent passes a relative path containing "/". Files are ordered
    naturally by their path relative to screenshots_dir; step_number/
    caption/taken_at are parsed from the filename when possible (see
    _parse_screenshot_filename()) and otherwise fall back to list
    position / empty string / the file's mtime.
    """
    if not screenshots_dir.exists():
        return []

    files = sorted(
        (
            p
            for p in screenshots_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in _SCREENSHOT_EXTENSIONS
        ),
        key=lambda p: _natural_sort_key(str(p.relative_to(screenshots_dir))),
    )

    records = []
    for position, path in enumerate(files):
        parsed_step, caption, epoch_seconds = _parse_screenshot_filename(path.stem)

        if epoch_seconds is not None:
            taken_at = datetime.fromtimestamp(epoch_seconds, tz=dt_timezone.utc)
        else:
            try:
                taken_at = datetime.fromtimestamp(path.stat().st_mtime, tz=dt_timezone.utc)
            except OSError:
                taken_at = None

        try:
            relpath = str(path.relative_to(media_root))
        except ValueError:
            # Shouldn't happen -- archive_agent_outputs always writes under
            # media_root -- but don't let a path-math surprise blow up
            # archiving over something as inessential as screenshot linking.
            print(f"Warning: {path} is not under MEDIA_ROOT ({media_root}); skipping")
            continue

        records.append(
            {
                "relpath": relpath,
                "step_number": parsed_step if parsed_step is not None else position,
                "caption": caption,
                "taken_at": taken_at,
            }
        )

    return records


async def run_agent(
    index: int,
    prompt: str,
    agent_id: str,
    task_set_id: str,
    persona_name: str | None = None,
):
    """
    Create an agent + browser scoped to the given agent_id (this also
    registers an ExecutorAgentRun row via create_agent -> interposer), run
    the same prompt, record the outcome, then tear down the browser.
    """

    agent, browser = await create_agent(agent_id, task_set_id, index, prompt, persona_name)

    try:
        print(f"\n{'=' * 60}")
        print(f"AGENT {index}")
        print(f"AGENT ID: {agent_id}")
        print(f"{'=' * 60}\n")

        response = await agent.invoke_async(prompt)

        print(f"\n--- AGENT {index} RESULT ---")
        print(response)

        # summary/log files are still sitting in the flat AGENT_RUNS_DIR at
        # this point -- archive_agent_outputs() moves them later, once all
        # agents in this task set are done. Read them now, before the move,
        # to fill in the ExecutorAgentRun row.
        summary_text = _read_text_optional(AGENT_RUNS_DIR / f"{agent_id}.txt")
        execution_log = _read_json_optional(AGENT_RUNS_DIR / f"{agent_id}.json")
        input_tokens, output_tokens = interposer.extract_token_usage(response)

        await interposer.complete_executor_run(
            agent_id=agent_id,
            summary_text=summary_text,
            execution_log=execution_log,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return response

    except Exception as exc:
        # Best-effort: some Strands versions keep accumulated usage on the
        # Agent instance itself even after a failed invoke_async(), so try
        # there before giving up and recording 0/0.
        input_tokens, output_tokens = interposer.extract_token_usage(agent)
        await interposer.fail_executor_run(
            agent_id, str(exc), input_tokens=input_tokens, output_tokens=output_tokens
        )
        raise

    finally:
        await asyncio.to_thread(browser._cleanup)


def archive_agent_outputs(
    orchestration_run_id: str,
    agentic_task_set_id: str,
    agent_ids: list[str],
) -> tuple[Path, dict[str, list[dict]]]:
    """
    Move each executor agent's execution log, text summary, and screenshots
    directory out of the flat working dirs (agent_runs/, agent_screenshots/)
    and into this run's nested archive:

        MEDIA_ROOT/orchestration_runs/{orchestration_run_id}/{agentic_task_set_id}/{agent_id}/
            {agent_id}.json
            {agent_id}.txt
            screenshots/...

    Missing files are logged and skipped rather than raising, since an
    agent that errored out mid-run may not have written a summary (or
    anything at all).

    Returns (task_set_dir, screenshots_by_agent). screenshots_by_agent maps
    agent_id -> the list of screenshot record dicts (relpath/step_number/
    caption/taken_at) found in that agent's now-archived screenshots/ dir,
    ready to hand straight to interposer.record_screenshots(). Agents with
    no screenshots directory (or an empty one) map to [].
    """

    task_set_dir = ORCHESTRATION_RUNS_ROOT / orchestration_run_id / agentic_task_set_id
    task_set_dir.mkdir(parents=True, exist_ok=True)

    screenshots_by_agent: dict[str, list[dict]] = {}

    for agent_id in agent_ids:
        dest_dir = task_set_dir / agent_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        json_src = AGENT_RUNS_DIR / f"{agent_id}.json"
        txt_src = AGENT_RUNS_DIR / f"{agent_id}.txt"
        screenshots_src = AGENT_SCREENSHOTS_DIR / agent_id

        if json_src.exists():
            shutil.move(str(json_src), str(dest_dir / json_src.name))
        else:
            print(f"Warning: no execution log found for agent {agent_id}")

        if txt_src.exists():
            shutil.move(str(txt_src), str(dest_dir / txt_src.name))
        else:
            print(
                f"Warning: no summary found for agent {agent_id} "
                "(it may have failed before calling write_summary)"
            )

        if screenshots_src.exists():
            dest_screenshots_dir = dest_dir / "screenshots"
            shutil.move(str(screenshots_src), str(dest_screenshots_dir))
            screenshots_by_agent[agent_id] = _collect_screenshot_records(
                dest_screenshots_dir, MEDIA_ROOT
            )
        else:
            screenshots_by_agent[agent_id] = []

    return task_set_dir, screenshots_by_agent


def summarize_agent_runs(task_set_dir: Path):
    """
    Read every archived executor summary (.txt) under task_set_dir,
    synthesize them with Amazon Bedrock Mantle Chat Completions, and
    write the combined result alongside them as summary.txt.
    """

    texts = []

    for path in sorted(task_set_dir.rglob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
            texts.append(f"--- {path.parent.name}/{path.name} ---\n{text}")
        except Exception as e:
            print(f"Could not read {path}: {e}")

    if not texts:
        print(f"No .txt summaries found under {task_set_dir}")
        return None

    combined_text = "\n\n".join(texts)

    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are summarizing the results of multiple "
                    "agent runs. Read all provided agent output and "
                    "produce a concise summary of what happened, "
                    "including important findings, differences "
                    "between agents, errors, and the overall result."
                ),
            },
            {
                "role": "user",
                "content": combined_text,
            },
        ],
    )

    summary = response.choices[0].message.content

    print("\n" + "=" * 60)
    print("AGENT RUN SUMMARY")
    print("=" * 60)
    print(summary)

    try:
        (task_set_dir / "summary.txt").write_text(summary, encoding="utf-8")
    except Exception as e:
        print(f"Could not write combined summary to {task_set_dir}: {e}")

    return summary

def make_run_agentic_task(orchestration_run_id: str): 
    task_sets_called = 0
    task_set_lock = asyncio.Lock()

    """
    Build a run_agentic_task tool bound to a specific orchestration_run_id.

    orchestration_run_id is decided by the caller (e.g. a Django view /
    Huey task that generates a UUID for the run) rather than by the LLM, so
    it's baked into the tool via closure instead of being exposed as an
    argument the model could pass. Call this once per orchestration run to
    get a fresh tool instance, then hand that instance to the orchestrator
    Agent's `tools=[...]` list.

    agent_ids remain auto-generated per call, same as before.
    agentic_task_set_id is now assigned by the interposer (it's the id of
    the AgenticTaskSet row it creates), rather than minted locally.
    """

    @tool
    async def run_agentic_task(prompt: str, num_agents: int = 3) -> str:
        """
        Spawn multiple parallel executor agents to test a website, each running
        a browser session against the same or related prompt, then collect and
        summarize their results.

        Args:
            prompt: The specific, narrow task each executor agent should perform
                (e.g. "Navigate to http://127.0.0.1:5000 and create a new account").
            num_agents: How many parallel executor agents to spawn (hard-capped at 2).

        Returns:
            A synthesized text summary of all executor agent runs.

        
       Hard limits for this tool instance:
        - At most 2 executor agents per call.
        - At most 2 run_agentic_task calls per orchestration run.
        """

        nonlocal task_sets_called


        if num_agents < 1:
            raise ValueError("num_agents must be at least 1.")

        if num_agents > MAX_EXECUTOR_AGENTS_PER_TASK_SET:
            print(
                f"Limiting to {MAX_EXECUTOR_AGENTS_PER_TASK_SET} agents for this run."
            )
            num_agents = MAX_EXECUTOR_AGENTS_PER_TASK_SET



        async with task_set_lock:
            if task_sets_called >= MAX_TASK_SETS_PER_ORCHESTRATION:
                raise RuntimeError(
                    f"Hard limit reached: an orchestration may call "
                    f"run_agentic_task at most "
                    f"{MAX_TASK_SETS_PER_ORCHESTRATION} times."
                )

            task_sets_called += 1

        agentic_task_set_id = await interposer.create_task_set(
            orchestration_run_id=orchestration_run_id,
            prompt=prompt,
            num_agents_requested=num_agents,
        )

        # Generate agent_ids up front so we know exactly which agents to
        # archive afterward, even if one of them raises before finishing.
        agent_ids = [str(uuid.uuid4()) for _ in range(num_agents)]

        try:
            results = await asyncio.gather(
                *(
                    run_agent(index, prompt, agent_id, agentic_task_set_id)
                    for index, agent_id in enumerate(agent_ids, start=1)
                ),
                return_exceptions=True,
            )
        except Exception as exc:
            # Shouldn't normally happen (return_exceptions=True keeps
            # gather itself from raising), but if it does, don't leave the
            # task set stuck in RUNNING.
            await interposer.fail_task_set(agentic_task_set_id, str(exc))
            raise

        for index, result in enumerate(results, start=1):
            if isinstance(result, Exception):
                print(f"\nAGENT {index} failed: {result!r}")

        task_set_dir, screenshots_by_agent = archive_agent_outputs(
            orchestration_run_id, agentic_task_set_id, agent_ids
        )

        # Link each agent's archived screenshots to its ExecutorAgentRun row.
        # Best-effort/non-fatal by construction: record_screenshots() itself
        # never raises (see interposer.py), and agents with no screenshots
        # dir already mapped to [] in archive_agent_outputs(), so this is a
        # no-op for them rather than an extra branch here.
        screenshot_counts = await asyncio.gather(
            *(
                interposer.record_screenshots(agent_id, records)
                for agent_id, records in screenshots_by_agent.items()
                if records
            )
        )
        if screenshot_counts:
            print(f"Recorded {sum(screenshot_counts)} screenshot(s) across {len(screenshot_counts)} agent(s).")

        summary = summarize_agent_runs(task_set_dir)

        await interposer.complete_task_set(
            task_set_id=agentic_task_set_id,
            synthesized_summary=summary or "",
            num_agents_spawned=len(agent_ids),
        )

        return summary

    return run_agentic_task


async def main():
    # Standalone/manual run: nothing upstream supplied an orchestration_run_id.
    # Unlike OrchestrationRun updates (which no-op with a warning if the row
    # is missing), create_task_set() does a real FK insert against
    # orchestration_run_id, so this path only works if an OrchestrationRun
    # with this id already exists in the DB -- e.g. create one in the shell
    # first (`OrchestrationRun.objects.create(id=..., user_inquiry=...)`)
    # and paste its id in below. Running this file with no such row will
    # raise an IntegrityError from create_task_set().
    orchestration_run_id = uuid.uuid4().hex
    run_agentic_task = make_run_agentic_task(orchestration_run_id)

    prompt = (
        "Navigate to http://127.0.0.1:5000 "
        "and create a new user account."
    )

    summary = await run_agentic_task(
        prompt,
        num_agents=5,
    )

    print("\nFinal summary:")
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())