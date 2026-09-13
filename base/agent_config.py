import os

from strands import Agent
from strands.models.openai import OpenAIModel
from strands.types.content import SystemContentBlock
from strands.models.openai_responses import OpenAIResponsesModel

from .prompt import SYSTEM_PROMPT
from .loggingtools import ExecutionLogger
from .scoped_browser import ScopedLocalChromiumBrowser
from .agent_tools import (
    generate_name,
    generate_username,
    generate_password,
)
from . import interposer


def _build_model() -> OpenAIModel | OpenAIResponsesModel:
    """
    Build the executor's model from whichever backend is configured.

    USE_AWS_BEDROCK selects the backend (defaults to true, so existing
    deployments that don't set the var keep using Bedrock):
      - true  (default): Amazon Bedrock Mantle's OpenAI-compatible Chat
        Completions endpoint, via AWS_BEDROCK_API_KEY / AWS_BEDROCK_BASE_URL
        / AWS_BEDROCK_MODEL_ID.
      - false: OpenAI directly, via OPENAI_API_KEY / OPENAI_API_MODEL.

    Only the env vars for the selected backend are required -- e.g. this
    won't fail because AWS_BEDROCK_API_KEY is unset when USE_AWS_BEDROCK is
    false. Raises RuntimeError with a specific message if a required var
    for the selected backend is missing.
    """

    use_aws_bedrock = os.getenv("USE_AWS_BEDROCK", "true").strip().lower() != "false"

    if use_aws_bedrock:
        api_key = os.getenv("AWS_BEDROCK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "AWS_BEDROCK_API_KEY is not set (required because "
                "USE_AWS_BEDROCK is not 'false')."
            )

        base_url = os.getenv("AWS_BEDROCK_BASE_URL")
        if not base_url:
            raise RuntimeError(
                "AWS_BEDROCK_BASE_URL is not set (required because "
                "USE_AWS_BEDROCK is not 'false')."
            )

        model_id = os.getenv("AWS_BEDROCK_MODEL_ID")
        if not model_id:
            raise RuntimeError(
                "AWS_BEDROCK_MODEL_ID is not set (required because "
                "USE_AWS_BEDROCK is not 'false')."
            )

        return OpenAIModel(
            model_id=model_id,
            client_args={
                "api_key": api_key,
                "base_url": base_url,
            },
        )

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set (required because USE_AWS_BEDROCK "
            "is 'false')."
        )

    openai_api_model = os.getenv("OPENAI_API_MODEL")
    if not openai_api_model:
        raise RuntimeError(
            "OPENAI_API_MODEL is not set (required because USE_AWS_BEDROCK "
            "is 'false')."
        )

    return OpenAIResponsesModel(
        client_args={
            "api_key": openai_api_key,
        },
        model_id=openai_api_model,
    )


async def create_agent(
    agent_id: str,
    task_set_id: str,
    index: int,
    prompt: str,
    persona_name: str | None = None,
) -> tuple[Agent, ScopedLocalChromiumBrowser]:
    """
    Build an executor Agent + its scoped browser, and register its
    existence in the DB via the interposer.

    task_set_id / index / prompt / persona_name only feed the
    ExecutorAgentRun row created here (linking this agent to its
    AgenticTaskSet) -- they don't otherwise change how the agent itself is
    built. `prompt` in particular is recorded for bookkeeping; the caller
    is still responsible for actually invoking the agent with it.

    The DB row is created *before* the browser/model are constructed, so a
    row exists (and can be marked failed by the caller) even if agent
    construction itself -- including missing/invalid credentials for
    whichever backend USE_AWS_BEDROCK selects -- blows up right after this.
    """

    await interposer.create_executor_run(
        task_set_id=task_set_id,
        agent_id=agent_id,
        index=index,
        prompt=prompt,
        persona_name=persona_name,
    )

    # Raises RuntimeError with a specific missing-var message if the
    # selected backend (AWS Bedrock or OpenAI) isn't fully configured.
    model = _build_model()

    screenshots_dir = os.path.join("agent_screenshots", agent_id)
    browser = ScopedLocalChromiumBrowser(screenshots_dir=screenshots_dir)

    logger = ExecutionLogger(
        output_dir="agent_runs",
        agent_id=agent_id,
    )

    agent = Agent(
        model=model,
        tools=[
            browser.browser,
            logger.record_step,
            logger.write_summary,
            generate_name,
            generate_username,
            generate_password,
        ],
        system_prompt=[
            SystemContentBlock(text=SYSTEM_PROMPT),
            SystemContentBlock(cachePoint={"type": "default"}),
        ],
    )

    return agent, browser