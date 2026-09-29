from dataclasses import dataclass, field
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
    subtasks: list[TaskSubtask] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskProvider(Protocol):
    async def create_task(self, draft: TaskDraft, *, context: str = "task") -> dict[str, Any]: ...


def task_draft_from_analysis(analysis: TaskAnalysis, *, description: str | None = None) -> TaskDraft:
    return TaskDraft(
        title=analysis.title,
        description=description or analysis.description,
        priority=analysis.priority,
        project=None,
        team=None,
        type=analysis.category,
        area=analysis.area,
        environment=None if analysis.environment in {"unknown", ""} else analysis.environment,
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
        },
    )
