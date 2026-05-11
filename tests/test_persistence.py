import importlib.util

import pytest

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import Route, Scenario, initial_state


def test_build_checkpointer_none() -> None:
    assert build_checkpointer("none") is None


def test_build_checkpointer_memory() -> None:
    checkpointer = build_checkpointer("memory")

    assert checkpointer is not None


def test_build_checkpointer_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown checkpointer kind"):
        build_checkpointer("unknown")


@pytest.mark.skipif(
    importlib.util.find_spec("langgraph.checkpoint.sqlite") is None,
    reason="langgraph-checkpoint-sqlite is not installed",
)
def test_sqlite_checkpointer_persists_state_history() -> None:
    checkpointer = build_checkpointer("sqlite", ":memory:")
    graph = build_graph(checkpointer=checkpointer)
    scenario = Scenario(
        id="sqlite",
        query="Please lookup order status for order 12345",
        expected_route=Route.TOOL,
    )
    state = initial_state(scenario)
    config = {"configurable": {"thread_id": state["thread_id"]}}

    result = graph.invoke(state, config=config)
    history = list(graph.get_state_history(config))

    assert result["route"] == Route.TOOL.value
    assert history
    assert history[0].values["events"][-1]["node"] == "finalize"
