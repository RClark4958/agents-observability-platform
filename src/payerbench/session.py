"""Per-conversation session state the tools can see: who the caller is, what they changed, what
was planted in the data, and which defenses are on.

The agent's default mode has no session (any member ID in the message is accepted, as in P1/P2).
The red-team suite runs every conversation inside `use_session(...)`, which binds a verified caller
and gives the tools somewhere to record write actions. State lives in a ContextVar so LangGraph's
tool threads (which copy the context) see the same object.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Defense switches understood by the tools and the agent graph.
DEFENSES = ("authz", "spotlight", "injection_gate", "approval", "output_gate")


@dataclass
class Action:
    """One write the agent performed (or attempted) through an action tool."""

    tool: str
    member_id: str
    args: dict[str, Any]
    allowed: bool
    reason: str = ""


@dataclass
class Session:
    member_id: str | None = None
    # Text planted into tool results, keyed by surface: prior_auth_note, claim_note, plan_doc.
    plants: dict[str, str] = field(default_factory=dict)
    defenses: frozenset[str] = frozenset()
    # System prompt variant: "full" states the relationship rule explicitly; "minimal" keeps the
    # P1/P2 wording, to measure how much of the safety comes from the prompt.
    prompt: str = "full"
    actions: list[Action] = field(default_factory=list)
    # Tool-result fields the injection gate removed, for reporting.
    gated: list[dict[str, str]] = field(default_factory=list)
    # Latest user message; the simulated approver reads it (see tools._approve).
    last_user_text: str = ""
    # Optional injection classifier for the injection_gate defense; regex is used when unset.
    classifier: Callable[[str], bool] | None = None

    def on(self, defense: str) -> bool:
        return defense in self.defenses


_current: ContextVar[Session | None] = ContextVar("payerbench_session", default=None)


def current() -> Session | None:
    return _current.get()


@contextlib.contextmanager
def use_session(session: Session) -> Iterator[Session]:
    token = _current.set(session)
    try:
        yield session
    finally:
        _current.reset(token)
