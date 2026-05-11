"""CLI for the lab."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import typer
import yaml

from .graph import build_graph
from .metrics import MetricsReport, metric_from_state, summarize_metrics, write_metrics
from .persistence import build_checkpointer
from .report import write_report
from .scenarios import load_scenarios
from .state import Route, Scenario, initial_state

app = typer.Typer(no_args_is_help=True)


def _state_history(graph: object, run_config: dict[str, object]) -> list[object]:
    get_state_history = getattr(graph, "get_state_history", None)
    if not callable(get_state_history):
        return []
    try:
        return list(get_state_history(run_config))
    except Exception:
        return []


def _time_travel_replay(history: list[object]) -> list[dict[str, Any]]:
    replay = []
    for step, snapshot in enumerate(reversed(history)):
        values = getattr(snapshot, "values", {}) or {}
        events = values.get("events", []) or []
        last_event = events[-1] if events else {}
        replay.append(
            {
                "step": step,
                "node": last_event.get("node", "start"),
                "route": values.get("route"),
                "attempt": values.get("attempt"),
                "events_count": len(events),
            }
        )
    return replay


def _crash_resume_replay(
    checkpointer_kind: str,
    database_url: str | None,
    run_config: dict[str, object],
) -> list[dict[str, Any]]:
    if checkpointer_kind not in {"sqlite", "postgres"}:
        return []
    if database_url == ":memory:":
        return []
    checkpointer = build_checkpointer(checkpointer_kind, database_url)
    graph = build_graph(checkpointer=checkpointer)
    return _time_travel_replay(_state_history(graph, run_config))


def _write_time_travel_replay(replay: list[dict[str, Any]], output_path: Path) -> None:
    path = output_path.parent / "time_travel_replay.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(replay, indent=2, ensure_ascii=False), encoding="utf-8")


def _graph_mermaid() -> str:
    graph = build_graph()
    return graph.get_graph().draw_mermaid()


def _write_graph_diagram(output_path: Path) -> str:
    path = output_path.parent / "graph_diagram.mmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    mermaid = _graph_mermaid()
    path.write_text(mermaid, encoding="utf-8")
    return mermaid


def _interrupt_payload(interrupts: list[object]) -> list[dict[str, Any]]:
    payload = []
    for item in interrupts:
        payload.append(
            {
                "id": getattr(item, "id", None),
                "value": getattr(item, "value", None),
            }
        )
    return payload


@app.command("run-scenarios")
def run_scenarios(
    config: Annotated[Path, typer.Option("--config")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Run all grading scenarios and write metrics JSON."""
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    scenarios = load_scenarios(cfg["scenarios_path"])
    checkpointer = build_checkpointer(cfg.get("checkpointer", "memory"), cfg.get("database_url"))
    graph = build_graph(checkpointer=checkpointer)
    metrics = []
    first_run_config = None
    run_id = str(cfg.get("run_id") or f"run-{uuid4().hex}")
    for scenario in scenarios:
        state = initial_state(scenario)
        state["thread_id"] = f"{state['thread_id']}-{run_id}"
        run_config = {
            "configurable": {
                "thread_id": state["thread_id"],
            }
        }
        if first_run_config is None:
            first_run_config = run_config
        final_state = graph.invoke(state, config=run_config)
        metrics.append(
            metric_from_state(
                final_state,
                scenario.expected_route.value,
                scenario.requires_approval,
            )
        )
    checkpointer_kind = str(cfg.get("checkpointer", "memory")).strip().lower()
    database_url = cfg.get("database_url")
    replay = (
        _crash_resume_replay(checkpointer_kind, database_url, first_run_config)
        if first_run_config is not None
        else []
    )
    resume_success = bool(replay)
    report = summarize_metrics(metrics, resume_success=resume_success)
    write_metrics(report, output)
    _write_time_travel_replay(replay, output)
    graph_diagram = _write_graph_diagram(output)
    if cfg.get("report_path"):
        write_report(
            report,
            cfg["report_path"],
            time_travel_replay=replay,
            graph_diagram=graph_diagram,
        )
    typer.echo(f"Wrote metrics to {output}")


@app.command("export-graph")
def export_graph(
    output: Annotated[Path, typer.Option("--output")] = Path("outputs/graph_diagram.mmd"),
) -> None:
    """Export the compiled LangGraph diagram as Mermaid."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_graph_mermaid(), encoding="utf-8")
    typer.echo(f"Wrote graph diagram to {output}")


@app.command("hitl-demo")
def hitl_demo(
    output: Annotated[Path, typer.Option("--output")] = Path("outputs/hitl_demo.json"),
    approved: Annotated[bool, typer.Option("--approved/--rejected")] = True,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "demo-human",
    comment: Annotated[str, typer.Option("--comment")] = "approved for HITL demo",
) -> None:
    """Run a real interrupt/resume HITL approval demo."""
    from langgraph.types import Command

    scenario = Scenario(
        id="hitl_demo",
        query="Refund this customer and send confirmation email",
        expected_route=Route.RISKY,
        requires_approval=True,
    )
    state = initial_state(scenario)
    state["thread_id"] = f"{state['thread_id']}-run-{uuid4().hex}"
    run_config = {"configurable": {"thread_id": state["thread_id"]}}
    graph = build_graph(checkpointer=build_checkpointer("memory"))

    previous_interrupt = os.environ.get("LANGGRAPH_INTERRUPT")
    os.environ["LANGGRAPH_INTERRUPT"] = "true"
    try:
        interrupted_state = graph.invoke(state, config=run_config)
        interrupts = interrupted_state.get("__interrupt__", [])
        if not interrupts:
            raise typer.BadParameter("Expected HITL interrupt, but graph completed without one")

        decision = {
            "approved": approved,
            "reviewer": reviewer,
            "comment": comment,
        }
        final_state = graph.invoke(Command(resume=decision), config=run_config)
    finally:
        if previous_interrupt is None:
            os.environ.pop("LANGGRAPH_INTERRUPT", None)
        else:
            os.environ["LANGGRAPH_INTERRUPT"] = previous_interrupt

    evidence = {
        "thread_id": state["thread_id"],
        "interrupts": _interrupt_payload(interrupts),
        "decision": decision,
        "approval": final_state.get("approval"),
        "final_answer": final_state.get("final_answer"),
        "nodes_visited": [event.get("node") for event in final_state.get("events", [])],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"Wrote HITL demo evidence to {output}")


@app.command("validate-metrics")
def validate_metrics(metrics: Annotated[Path, typer.Option("--metrics")]) -> None:
    """Validate metrics JSON schema for grading."""
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    report = MetricsReport.model_validate(payload)
    if report.total_scenarios < 6:
        raise typer.BadParameter("Expected at least 6 scenarios")
    typer.echo(f"Metrics valid. success_rate={report.success_rate:.2%}")


if __name__ == "__main__":
    app()
