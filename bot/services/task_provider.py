from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from bot.services.task_ai_service import TaskAnalysis, TaskSubtask


@dataclass(frozen=True)
class TaskDraft:
    """Provider-neutral result of task triage."""

    title: str
    description: str
    priority: str = "normal"
    project: str | None = None
    team: str | None = None
    type: str = "other"
    area: str = "other"
    environment: str | None = None
    status: str | None = None
    estimate: int | None = None
    assignee: str | None = None
    due_date: date | None = None
    cycle: str | None = None
    milestone: str | None = None
    parent_issue: str | None = None
    confidence: str = ""
    field_confidence: dict[str, str] = field(default_factory=dict)
    risk: str = "low"
    manual_triage: bool = False
    source_content: str = ""
    source_url: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    subtasks: list[TaskSubtask] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskProvider(Protocol):
    async def create_task(self, draft: TaskDraft, *, context: str = "task") -> dict[str, Any]: ...


def task_draft_from_analysis(
    analysis: TaskAnalysis,
    *,
    description: str | None = None,
    source_content: str = "",
) -> TaskDraft:
    parsed_due_date = None
    if analysis.due_date:
        try:
            parsed_due_date = date.fromisoformat(analysis.due_date)
        except ValueError:
            pass
    return TaskDraft(
        title=analysis.title,
        description=description or analysis.description,
        priority=analysis.priority,
        type=analysis.category,
        area=analysis.area,
        environment=None if analysis.environment in {"unknown", ""} else analysis.environment,
        team=analysis.team, project=analysis.project, status=analysis.status,
        assignee=analysis.assignee, due_date=parsed_due_date, cycle=analysis.cycle, milestone=analysis.milestone,
        confidence=analysis.confidence,
        field_confidence=dict(analysis.field_confidence),
        risk=analysis.risk,
        manual_triage=analysis.destination == "manual_triage",
        source_content=source_content,
        subtasks=list(analysis.subtasks),
        metadata={
            "risk": analysis.risk,
            "confidence": analysis.confidence,
            "possible_cause": analysis.possible_cause,
            "possible_solution": analysis.possible_solution,
            "acceptance_criteria": analysis.acceptance_criteria,
            "missing_information": analysis.missing_information,
            "tags": analysis.tags,
            "destination": analysis.destination,
            "points": analysis.points,
            "estimated_minutes": analysis.estimated_minutes,
            "resolution_deadline_days": analysis.resolution_deadline_days,
        },
    )
