"""Emit one hand-built agent-shaped trace so every backend can be checked before the agent exists.

Span names and attributes follow the OTel GenAI semantic conventions (v1.41, Development status):
invoke_agent -> chat -> execute_tool -> chat.
"""

from __future__ import annotations

import time
import uuid

from opentelemetry import trace

from payerbench import telemetry


def main() -> None:
    telemetry.configure()
    tracer = trace.get_tracer("payerbench.smoke")
    conversation_id = str(uuid.uuid4())

    with tracer.start_as_current_span(
        "invoke_agent payerbench",
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "payerbench",
            "gen_ai.conversation.id": conversation_id,
            "payerbench.smoke": True,
        },
    ):
        with tracer.start_as_current_span(
            "chat local/gpt-oss-120b",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "local/gpt-oss-120b",
                "gen_ai.usage.input_tokens": 412,
                "gen_ai.usage.output_tokens": 38,
                "gen_ai.response.finish_reasons": ["tool_calls"],
            },
        ):
            time.sleep(0.05)

        with tracer.start_as_current_span(
            "execute_tool check_eligibility",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "check_eligibility",
                "gen_ai.tool.call.arguments": '{"member_id": "M-SYNTH-000001"}',
                "gen_ai.tool.call.result": '{"active": true, "plan": "Silver PPO"}',
            },
        ):
            time.sleep(0.02)

        with tracer.start_as_current_span(
            "chat local/gpt-oss-120b",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "local/gpt-oss-120b",
                "gen_ai.usage.input_tokens": 480,
                "gen_ai.usage.output_tokens": 61,
                "gen_ai.response.finish_reasons": ["stop"],
            },
        ):
            time.sleep(0.05)

    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]
    print(f"smoke trace sent, conversation_id={conversation_id}")
    print("look for span 'invoke_agent payerbench' in Langfuse, Phoenix, and Grafana Tempo")


if __name__ == "__main__":
    main()
