# orchestrator.py
#
# Entry point for the agentic web-testing framework.
#
# Loads environment configuration, creates the orchestrator agent, and
# exposes a reusable function for running an orchestration task.
#
# The orchestrator agent has exactly one tool: run_agentic_task.
# That tool is responsible for spawning executor agents, running their tasks,
# collecting their summaries, and producing the combined result.

import asyncio
import os
import uuid

from dotenv import load_dotenv
from strands import Agent
from strands.models.openai import OpenAIModel
from strands.models.openai_responses import OpenAIResponsesModel
from strands.types.content import SystemContentBlock

from .prompt import ORCHESTRATOR_SYSTEM_PROMPT
from .launcher import make_run_agentic_task
from . import interposer


load_dotenv()

# USE_AWS_BEDROCK controls which backend the orchestrator (and its
# executor agents, via agent_config.py) talk to. Defaults to true so
# existing deployments that don't set the var keep using Bedrock.
USE_AWS_BEDROCK = os.getenv("USE_AWS_BEDROCK", "true").lower() != "false"

if USE_AWS_BEDROCK:
    api_key = os.getenv("AWS_BEDROCK_API_KEY")
    base_url = os.getenv("AWS_BEDROCK_BASE_URL")
    model_id = os.getenv("AWS_BEDROCK_MODEL_ID")
else:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = None
    model_id = os.getenv("OPENAI_API_MODEL")


def _build_orchestrator_agent(orchestration_run_id: str) -> Agent:
    """
    Construct a fresh orchestrator Agent wired to a run_agentic_task tool
    that's bound to this specific orchestration_run_id. A new Agent is
    built per call (rather than reused as a module-level singleton) so
    concurrent orchestration runs in the same process never share a tool
    instance or an orchestration_run_id.
    """

    if USE_AWS_BEDROCK:
        model = OpenAIModel(
            model_id=model_id,
            client_args={
                "api_key": api_key,
                "base_url": base_url,
            },
        )
    else:
        model = OpenAIResponsesModel(
            client_args={
                "api_key": api_key,
            },
            model_id=model_id,
        )

    return Agent(
    model=model,
    tools=[make_run_agentic_task(orchestration_run_id)],
    system_prompt=[
        SystemContentBlock(text=ORCHESTRATOR_SYSTEM_PROMPT),
        SystemContentBlock(cachePoint={"type": "default"}),
    ],
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_orchestrator(prompt: str, orchestration_run_id: str | None = None) -> str:
    """
    Run one orchestration end-to-end.

    orchestration_run_id: caller-supplied ID for this run (e.g. a UUID your
        Django view/Huey task already generated -- and already used to
        create the OrchestrationRun row itself; this function only updates
        that row's status, it never creates it). All executor output for
        every run_agentic_task call the orchestrator makes during this
        invocation will be archived under
        orchestration_runs/{orchestration_run_id}/... — including if it
        calls run_agentic_task multiple times (e.g. multiple testing
        rounds), since they all share the same orchestration_run_id.
        If omitted, a UUID is generated so this still works standalone
        (see main() below) -- though note that standalone path has the same
        caveat as launcher.py's main(): the interposer expects a matching
        OrchestrationRun row to already exist in the DB.
    """

    if not prompt or not prompt.strip():
        raise ValueError("Prompt must not be empty.")

    if orchestration_run_id is None:
        orchestration_run_id = uuid.uuid4().hex

    orchestrator_agent = _build_orchestrator_agent(orchestration_run_id)

    await interposer.start_orchestration_run(orchestration_run_id, model_id=model_id or "")

    try:
        result = await orchestrator_agent.invoke_async(prompt)
    except Exception as exc:
        # Best-effort: pull whatever the orchestrator agent itself has
        # accumulated so far, even though invoke_async() didn't return a
        # usable AgentResult. Any run_agentic_task calls it made before
        # failing already recorded their own executor-side tokens via
        # complete_executor_run()/complete_task_set(), and
        # fail_orchestration_run() rolls those in regardless.
        orch_input_tokens, orch_output_tokens = interposer.extract_token_usage(
            orchestrator_agent
        )
        await interposer.fail_orchestration_run(
            orchestration_run_id,
            str(exc),
            orchestrator_input_tokens=orch_input_tokens,
            orchestrator_output_tokens=orch_output_tokens,
        )
        raise

    result_str = str(result)
    orch_input_tokens, orch_output_tokens = interposer.extract_token_usage(result)
    await interposer.complete_orchestration_run(
        orchestration_run_id,
        result_str,
        orchestrator_input_tokens=orch_input_tokens,
        orchestrator_output_tokens=orch_output_tokens,
    )

    return result_str


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

TASK_PROMPT = """
I have a web app running locally at http://127.0.0.1:8000/. I want to test
my registration system — specifically, how easy it is for a new user to create an account.

Please come up with and execute an appropriate testing plan, run it, and give me a report
on what you find, including any bugs or usability issues.

Important restrictions:
Do NOT run more than 2 concurrent subagents at a time. Wait for each subagent to finish before starting the next one. You can run them sequentially if you want.
Do NOT run more than 2 rounds of testing in total. Wait for each round to finish before starting the next one.
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("ORCHESTRATOR — starting run")
    print("=" * 60)

    print(f"\nRequest:\n{TASK_PROMPT.strip()}\n")

    try:
        result = await run_orchestrator(TASK_PROMPT)

        print("\n" + "=" * 60)
        print("ORCHESTRATOR — final report")
        print("=" * 60)
        print(result)

    except Exception as exc:
        print("\n" + "=" * 60)
        print("ORCHESTRATOR — run failed")
        print("=" * 60)
        print(f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    asyncio.run(main())