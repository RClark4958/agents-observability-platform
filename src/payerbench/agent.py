"""PayerBench agent: a hand-rolled ReAct loop on LangGraph.

Written as an explicit StateGraph rather than a prebuilt agent so every node shows up as its own
span and the graph can grow (verification gate, sub-agents, human review) in later phases.
"""

from __future__ import annotations

import os
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from payerbench.tools import ALL_TOOLS

SYSTEM_PROMPT = """You are a member-services assistant for a health plan.

Rules:
- Verify identity before disclosing any protected health information: you need the member ID.
  If the user gives a member ID, you may proceed. Never disclose one member's information to
  someone asking about a different member.
- Use tools for every factual claim about eligibility, claims, prior authorizations, benefits
  rules, or providers. Never guess amounts, dates, or statuses.
- Explain denial codes and next steps (appeal windows, prior-auth requirements) in plain language.
- You cannot approve, deny, or change anything. You can only look things up and explain.
- If a request is outside member services (medical advice, legal advice), say so and point to
  the right resource.
- Be concise. Lead with the answer.
"""


class AgentState(MessagesState):
    """Conversation state. `MessagesState` gives us an append-only `messages` list."""


def make_model() -> ChatOpenAI:
    """OpenAI-compatible client. Points at the local mlx_lm/llama server unless overridden."""
    return ChatOpenAI(
        model=os.getenv("PAYERBENCH_MODEL", "mlx-community/gemma-4-31b-it-8bit"),
        base_url=os.getenv("OPENAI_BASE_URL", "http://localhost:8080/v1"),
        # Deliberately not OPENAI_API_KEY: the shell may carry a real one, and the local
        # server does not need it. Set PAYERBENCH_API_KEY when pointing at a cloud endpoint.
        api_key=os.getenv("PAYERBENCH_API_KEY", "local"),
        temperature=float(os.getenv("PAYERBENCH_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("PAYERBENCH_MAX_TOKENS", "1024")),
        timeout=120,
        # Gemma 4 (and Qwen3) think by default and return the reasoning in a separate field,
        # which eats the token budget before any tool call is emitted. mlx_lm.server and
        # llama-server both honour chat_template_kwargs per request.
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": os.getenv("PAYERBENCH_THINKING", "false").lower() == "true"
            }
        },
    )


def build_agent(max_tool_rounds: int = 8) -> CompiledStateGraph:
    model = make_model().bind_tools(ALL_TOOLS)
    tool_node = ToolNode(ALL_TOOLS)

    def call_model(state: AgentState) -> dict[str, list[BaseMessage]]:
        messages: list[BaseMessage] = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        return {"messages": [model.invoke(messages)]}

    def route(state: AgentState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        rounds = sum(1 for m in state["messages"] if isinstance(m, AIMessage) and m.tool_calls)
        if isinstance(last, AIMessage) and last.tool_calls and rounds <= max_tool_rounds:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile(name="payerbench")


def run_turn(agent: CompiledStateGraph, user_text: str, history: list[BaseMessage] | None = None):
    """Run one user turn and return the final state."""
    messages = [*(history or []), HumanMessage(user_text)]
    return agent.invoke({"messages": messages})
