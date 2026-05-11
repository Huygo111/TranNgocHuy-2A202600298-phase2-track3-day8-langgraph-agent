import importlib.util

import pytest

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import Route, Scenario, initial_state

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("langgraph") is None,
    reason="langgraph not installed in local environment",
)


@pytest.mark.parametrize(
    ("query", "expected_route"),
    [
        ("How do I reset my password?", Route.SIMPLE.value),
        ("Please lookup order status for order 123", Route.TOOL.value),
        ("Can you fix it?", Route.MISSING_INFO.value),
        ("Refund this customer", Route.RISKY.value),
        ("Timeout failure while processing request", Route.ERROR.value),
    ],
)
def test_graph_runs_basic_routes(query: str, expected_route: str) -> None:
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    scenario = Scenario(id="smoke", query=query, expected_route=Route(expected_route))
    state = initial_state(scenario)
    result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
    visited_nodes = [event["node"] for event in result["events"]]

    assert result["route"] == expected_route
    assert result.get("final_answer") or result.get("pending_question")
    assert visited_nodes[-1] == "finalize"


def test_graph_risky_route_requires_approval_before_tool() -> None:
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    scenario = Scenario(
        id="risky",
        query="Delete customer account after support verification",
        expected_route=Route.RISKY,
        requires_approval=True,
    )
    state = initial_state(scenario)
    result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
    visited_nodes = [event["node"] for event in result["events"]]

    assert result["route"] == Route.RISKY.value
    assert result["approval"]["approved"] is True
    assert visited_nodes.index("approval") < visited_nodes.index("tool")
    assert visited_nodes[-1] == "finalize"


def test_graph_dead_letter_route_terminates() -> None:
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    scenario = Scenario(
        id="dead-letter",
        query="System failure cannot recover after multiple attempts",
        expected_route=Route.ERROR,
        should_retry=True,
        max_attempts=1,
    )
    state = initial_state(scenario)
    result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
    visited_nodes = [event["node"] for event in result["events"]]

    assert result["route"] == Route.ERROR.value
    assert result["attempt"] == 1
    assert "dead_letter" in visited_nodes
    assert visited_nodes[-1] == "finalize"
