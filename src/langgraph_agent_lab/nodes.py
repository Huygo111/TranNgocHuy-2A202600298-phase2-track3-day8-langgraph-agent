"""Node skeletons for the LangGraph workflow.

Each function should be small, testable, and return a partial state update. Avoid mutating the
input state in place.
"""

from __future__ import annotations

import re

from .state import AgentState, ApprovalDecision, Route, make_event

_RISKY_KEYWORDS = {"refund", "delete", "send", "cancel", "remove", "revoke"}
_TOOL_KEYWORDS = {"status", "order", "lookup", "check", "track", "find", "search"}
_ERROR_KEYWORDS = {"timeout", "fail", "failure", "error", "crash", "unavailable"}
_VAGUE_REFERENCES = {"it", "this", "that", "thing", "issue", "problem"}


def _query_words(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", query.lower())


def intake_node(state: AgentState) -> dict:
    """Normalize raw query into state fields."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized", query_length=len(query))],
    }


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route.

    Required routes: simple, tool, missing_info, risky, error.
    """
    query = state.get("query", "")
    words = _query_words(query)
    word_set = set(words)
    route = Route.SIMPLE
    risk_level = "low"

    if word_set & _RISKY_KEYWORDS:
        route = Route.RISKY
        risk_level = "high"
    elif word_set & _TOOL_KEYWORDS:
        route = Route.TOOL
    elif len(words) < 5 and word_set & _VAGUE_REFERENCES:
        route = Route.MISSING_INFO
        risk_level = "medium"
    elif word_set & _ERROR_KEYWORDS:
        route = Route.ERROR
        risk_level = "medium"

    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [
            make_event(
                "classify",
                "completed",
                f"route={route.value}",
                matched_words=sorted(word_set),
                risk_level=risk_level,
            )
        ],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating."""
    question = (
        "Can you provide the specific request details, "
        "such as the order id or account context?"
    )
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested")],
    }


def tool_node(state: AgentState) -> dict:
    """Call a mock tool.

    Simulates transient failures for error-route scenarios to demonstrate retry loops.
    """
    attempt = int(state.get("attempt", 0))
    scenario_id = state.get("scenario_id", "unknown")
    if state.get("route") == Route.ERROR.value and attempt < 2:
        result = f"ERROR: transient failure attempt={attempt} scenario={scenario_id}"
    else:
        result = f"mock-tool-result scenario={scenario_id} attempt={attempt}"
    return {
        "tool_results": [result],
        "events": [
            make_event(
                "tool",
                "completed",
                f"tool executed attempt={attempt}",
                attempt=attempt,
                route=state.get("route"),
            )
        ],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for approval."""
    query = state.get("query", "")
    proposed_action = f"Review and approve risky support action for query: {query}"
    return {
        "proposed_action": proposed_action,
        "events": [
            make_event(
                "risky_action",
                "pending_approval",
                "approval required before external action",
                risk_level=state.get("risk_level"),
            )
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human approval step with optional LangGraph interrupt().

    Set LANGGRAPH_INTERRUPT=true to use real interrupt() for HITL demos.
    Default uses mock decision so tests and CI run offline.
    """
    import os

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt({
            "proposed_action": state.get("proposed_action"),
            "risk_level": state.get("risk_level"),
        })
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(approved=bool(value))
    else:
        decision = ApprovalDecision(approved=True, comment="mock approval for lab")
    return {
        "approval": decision.model_dump(),
        "events": [
            make_event(
                "approval",
                "completed",
                f"approved={decision.approved}",
                reviewer=decision.reviewer,
            )
        ],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt or fallback decision."""
    attempt = int(state.get("attempt", 0)) + 1
    max_attempts = int(state.get("max_attempts", 3))
    errors = [f"transient failure attempt={attempt} of {max_attempts}"]
    backoff_ms = min(1000 * 2 ** max(attempt - 1, 0), 8000)
    return {
        "attempt": attempt,
        "errors": errors,
        "events": [
            make_event(
                "retry",
                "completed",
                "retry attempt recorded",
                attempt=attempt,
                max_attempts=max_attempts,
                backoff_ms=backoff_ms,
            )
        ],
    }


def answer_node(state: AgentState) -> dict:
    """Produce a final response."""
    if state.get("tool_results"):
        latest_result = state["tool_results"][-1]
        if state.get("approval"):
            answer = f"Approved action completed with tool evidence: {latest_result}"
        else:
            answer = f"I found: {latest_result}"
    else:
        answer = f"Here is a safe support response for: {state.get('query', '').strip()}"
    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results as the done-check that enables retry loops."""
    tool_results = state.get("tool_results", [])
    latest = tool_results[-1] if tool_results else ""
    if "ERROR" in latest:
        return {
            "evaluation_result": "needs_retry",
            "events": [
                make_event("evaluate", "completed", "tool result indicates failure, retry needed")
            ],
        }
    return {
        "evaluation_result": "success",
        "events": [make_event("evaluate", "completed", "tool result satisfactory")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Log unresolvable failures for manual review.

    Third layer of error strategy: retry -> fallback -> dead letter.
    """
    attempt = int(state.get("attempt", 0))
    max_attempts = int(state.get("max_attempts", 3))
    answer = (
        "Request could not be completed after maximum retry attempts. "
        "Logged for manual review."
    )
    return {
        "final_answer": answer,
        "events": [
            make_event(
                "dead_letter",
                "completed",
                f"max retries exceeded, attempt={attempt}",
                attempt=attempt,
                max_attempts=max_attempts,
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
