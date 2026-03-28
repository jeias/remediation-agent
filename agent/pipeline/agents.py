import json
from pathlib import Path

import yaml
from anthropic import Anthropic, transform_schema
from pydantic import TypeAdapter

from pipeline.config import MAX_TOKENS, MAX_TOOL_CALLS, CONFIDENCE_THRESHOLD
from pipeline.schemas import SummarizationOutput, ClassificationOutput, RemediationOutput
from pipeline.tools import SUMMARIZATION_TOOLS, CLASSIFICATION_TOOLS, REMEDIATION_TOOLS
from pipeline.aws_actions import execute_tool
from pipeline.tracing import TraceLogger


# --- Prompt Loading ---

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> dict:
    with open(PROMPTS_DIR / f"{name}.yaml") as f:
        return yaml.safe_load(f)


SUMMARIZATION_CONFIG = load_prompt("summarization")
CLASSIFICATION_CONFIG = load_prompt("classification")
REMEDIATION_CONFIG = load_prompt("remediation")


# --- Exceptions ---

class MaxToolCallsExceeded(Exception):
    pass

class AgentValidationError(Exception):
    pass


# --- Structured Output Schema ---

def build_output_config(schema_class: type) -> dict:
    """Convert a Pydantic model to an output_config for structured outputs."""
    raw_schema = TypeAdapter(schema_class).json_schema()
    return {
        "format": {
            "type": "json_schema",
            "schema": transform_schema(raw_schema),
        }
    }


# --- Generic Agentic Loop ---

def run_agent(
    client: Anthropic,
    model: str,
    temperature: float,
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
    output_schema: type,
    agent_name: str,
    trace_logger: TraceLogger,
    max_tool_calls: int = MAX_TOOL_CALLS,
    parallel_tool_use: bool = False,
):
    output_config = build_output_config(output_schema)
    tool_call_count = 0
    current_messages = list(messages)

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            temperature=temperature,
            system=system_prompt,
            tools=tools,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=current_messages,
            output_config=output_config,
        )

        trace_logger.log_token_usage(agent_name, model, response.usage)

        # Tool use — execute tools and continue the loop
        if response.stop_reason == "tool_use":
            tool_call_count += 1
            if tool_call_count > max_tool_calls:
                raise MaxToolCallsExceeded(
                    f"Agent '{agent_name}' exceeded {max_tool_calls} tool calls"
                )

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    trace_logger.log_tool_call(agent_name, block.name, block.input)
                    result = execute_tool(block.name, block.input)
                    trace_logger.log_tool_result(agent_name, block.name, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            current_messages.append({"role": "assistant", "content": response.content})
            current_messages.append({"role": "user", "content": tool_results})
            continue

        # Check for truncation — max_tokens reached means incomplete JSON
        if response.stop_reason == "max_tokens":
            raise AgentValidationError(
                f"Agent '{agent_name}' response truncated (hit max_tokens). "
                f"Increase MAX_TOKENS or reduce prompt size."
            )

        # End turn — structured output guarantees valid JSON matching the schema
        output = output_schema.model_validate_json(response.content[0].text)

        # Log reasoning if present (Classification agent)
        if hasattr(output, "reasoning"):
            trace_logger.log_reasoning(agent_name, output.reasoning)

        trace_logger.log_agent_response(agent_name, output.model_dump())
        return output


# --- Agent Runner Functions ---

def run_summarization(client: Anthropic, alarm_event: dict, trace_logger: TraceLogger) -> SummarizationOutput:
    trace_logger.log_event("agent_start", {"agent": "summarization"})
    messages = [
        {
            "role": "user",
            "content": (
                f"A CloudWatch alarm has triggered. Here is the alarm event:\n\n"
                f"{json.dumps(alarm_event, indent=2, default=str)}\n\n"
                f"Fetch the application logs and produce a structured summary."
            ),
        }
    ]
    return run_agent(
        client=client,
        model=SUMMARIZATION_CONFIG["model"],
        temperature=SUMMARIZATION_CONFIG["temperature"],
        system_prompt=SUMMARIZATION_CONFIG["system_prompt"],
        tools=SUMMARIZATION_TOOLS,
        messages=messages,
        output_schema=SummarizationOutput,
        agent_name="summarization",
        trace_logger=trace_logger,
    )


def run_classification(client: Anthropic, summary: SummarizationOutput, trace_logger: TraceLogger) -> ClassificationOutput:
    trace_logger.log_event("agent_start", {"agent": "classification"})
    messages = [
        {
            "role": "user",
            "content": (
                f"Here is the error summary from the log analysis agent:\n\n"
                f"{summary.model_dump_json(indent=2)}\n\n"
                f"Classify this incident. Use your tools to gather evidence, then reason "
                f"step by step before outputting your classification."
            ),
        }
    ]
    return run_agent(
        client=client,
        model=CLASSIFICATION_CONFIG["model"],
        temperature=CLASSIFICATION_CONFIG["temperature"],
        system_prompt=CLASSIFICATION_CONFIG["system_prompt"],
        tools=CLASSIFICATION_TOOLS,
        messages=messages,
        output_schema=ClassificationOutput,
        agent_name="classification",
        trace_logger=trace_logger,
        max_tool_calls=8,
    )


def run_remediation(
    client: Anthropic,
    classification: ClassificationOutput,
    summary: SummarizationOutput,
    trace_logger: TraceLogger,
) -> RemediationOutput:
    trace_logger.log_event("agent_start", {"agent": "remediation"})
    messages = [
        {
            "role": "user",
            "content": (
                f"You are reviewing the following incident classification:\n\n"
                f"Classification:\n{classification.model_dump_json(indent=2)}\n\n"
                f"Original error summary:\n{summary.model_dump_json(indent=2)}\n\n"
                f"The confidence threshold for automated rollback is {CONFIDENCE_THRESHOLD}.\n\n"
                f"Review the classification, decide whether to execute the recommended action, "
                f"verify the result if you rollback, and compose a notification email. Use your tools to act."
            ),
        }
    ]
    return run_agent(
        client=client,
        model=REMEDIATION_CONFIG["model"],
        temperature=REMEDIATION_CONFIG["temperature"],
        system_prompt=REMEDIATION_CONFIG["system_prompt"],
        tools=REMEDIATION_TOOLS,
        messages=messages,
        output_schema=RemediationOutput,
        agent_name="remediation",
        trace_logger=trace_logger,
        max_tool_calls=8,
    )
