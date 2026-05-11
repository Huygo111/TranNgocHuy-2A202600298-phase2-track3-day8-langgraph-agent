from langgraph_agent_lab.metrics import metric_from_state, summarize_metrics
from langgraph_agent_lab.state import make_event


def test_metric_from_state_success() -> None:
    state = {
        "scenario_id": "S",
        "route": "simple",
        "final_answer": "ok",
        "events": [
            make_event("intake", "completed", "ok"),
            make_event("answer", "completed", "ok"),
        ],
        "errors": [],
    }
    metric = metric_from_state(state, expected_route="simple", approval_required=False)
    assert metric.success is True
    assert metric.nodes_visited == 2


def test_metric_from_state_counts_retry_approval_and_latency() -> None:
    state = {
        "scenario_id": "S",
        "route": "risky",
        "final_answer": "ok",
        "approval": {"approved": True},
        "events": [
            make_event("retry", "completed", "retry", latency_ms=4),
            make_event("approval", "completed", "approved", latency_ms=5),
            make_event("answer", "completed", "ok", latency_ms=6),
        ],
        "errors": ["transient failure"],
    }

    metric = metric_from_state(state, expected_route="risky", approval_required=True)

    assert metric.success is True
    assert metric.retry_count == 1
    assert metric.interrupt_count == 1
    assert metric.approval_required is True
    assert metric.approval_observed is True
    assert metric.latency_ms == 15
    assert metric.errors == ["transient failure"]


def test_metric_from_state_requires_approval_when_expected() -> None:
    state = {
        "scenario_id": "S",
        "route": "risky",
        "final_answer": "ok",
        "events": [make_event("answer", "completed", "ok")],
        "errors": [],
    }

    metric = metric_from_state(state, expected_route="risky", approval_required=True)

    assert metric.success is False
    assert metric.approval_required is True
    assert metric.approval_observed is False


def test_summarize_metrics() -> None:
    m1 = metric_from_state(
        {"scenario_id": "1", "route": "simple", "final_answer": "ok", "events": [], "errors": []},
        "simple",
        False,
    )
    m2 = metric_from_state(
        {"scenario_id": "2", "route": "tool", "final_answer": None, "events": [], "errors": []},
        "tool",
        False,
    )
    report = summarize_metrics([m1, m2], resume_success=True)

    assert report.total_scenarios == 2
    assert 0 <= report.success_rate <= 1
    assert report.resume_success is True


def test_summarize_metrics_rejects_empty_input() -> None:
    try:
        summarize_metrics([])
    except ValueError as exc:
        assert str(exc) == "No scenario metrics to summarize"
    else:
        raise AssertionError("Expected summarize_metrics to reject empty input")
