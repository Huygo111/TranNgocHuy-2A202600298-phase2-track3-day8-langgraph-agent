"""Report generation helper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .metrics import MetricsReport


def _render_time_travel_replay(replay: list[dict[str, Any]] | None) -> str:
    if not replay:
        return "No time-travel replay evidence captured."

    rows = ["| Step | Node | Route | Attempt | Events |", "|---:|---|---|---:|---:|"]
    for item in replay[-12:]:
        rows.append(
            "| {step} | {node} | {route} | {attempt} | {events_count} |".format(
                step=item.get("step", ""),
                node=item.get("node", ""),
                route=item.get("route", ""),
                attempt=item.get("attempt", ""),
                events_count=item.get("events_count", ""),
            )
        )
    return "\n".join(rows)


def render_report_stub(
    metrics: MetricsReport,
    time_travel_replay: list[dict[str, Any]] | None = None,
    graph_diagram: str | None = None,
) -> str:
    """Return a minimal report stub.

    TODO(student): replace with a richer report using the template in reports/.
    """
    return f"""# Day 08 Lab Report

## Metrics summary

- Total scenarios: {metrics.total_scenarios}
- Success rate: {metrics.success_rate:.2%}
- Average nodes visited: {metrics.avg_nodes_visited:.2f}
- Total retries: {metrics.total_retries}
- Total interrupts: {metrics.total_interrupts}
- Crash-resume/state-history success: {metrics.resume_success}

## Time-travel replay

{_render_time_travel_replay(time_travel_replay)}

## Graph diagram

```mermaid
{graph_diagram or "Graph diagram was not exported."}
```

## TODO(student)

Explain your architecture, state schema, persistence evidence, failure modes, and improvement plan.
"""


def write_report(
    metrics: MetricsReport,
    output_path: str | Path,
    time_travel_replay: list[dict[str, Any]] | None = None,
    graph_diagram: str | None = None,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_report_stub(metrics, time_travel_replay, graph_diagram),
        encoding="utf-8",
    )
