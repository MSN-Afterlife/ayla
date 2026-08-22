import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from bot.config import Settings

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

logger = logging.getLogger(__name__)
TASK_AI_TIMEOUT_SECONDS = 10
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4000
MAX_SUBTASKS = 5
MAX_FIELD_LENGTH = 1000
ENUMS = {
    "category": {"bug", "improvement", "security", "content", "infrastructure", "other"},
    "area": {"ayla", "site", "api", "database", "discord", "infrastructure", "other"},
    "environment": {"production", "staging", "both", "local", "unknown"},
    "priority": {"urgent", "high", "normal", "low"}, "risk": {"critical", "high", "medium", "low"},
    "confidence": {"high", "medium", "low"},
    "destination": {"ayla_bugs", "site_bugs", "incidents", "security", "suggestions", "community", "manual_triage"},
}


class TaskAIError(Exception):
    """Falha não crítica na análise de uma mensagem para o ClickUp."""


@dataclass(frozen=True)
class TaskSubtask:
    title: str
    description: str
    type: str = "investigation"


@dataclass(frozen=True)
class TaskAnalysis:
    title: str
    description: str
    subtasks: list[TaskSubtask] = field(default_factory=list)
    category: str = "other"
    area: str = "other"
    environment: str = "unknown"
    priority: str = "normal"
    risk: str = "low"
    confidence: str = "low"
    destination: str = "manual_triage"
    possible_cause: str = ""
    possible_solution: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)


class TaskAIService:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._model = settings.openai_model
        self._client = client
        if self._client is None and settings.openai_api_key and AsyncOpenAI is not None:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=0)

    async def analyze_task(self, content: str) -> TaskAnalysis:
        cleaned_content = content.strip()
        if not cleaned_content:
            raise TaskAIError("mensagem sem conteúdo textual")
        if self._client is None:
            raise TaskAIError("cliente de IA indisponível")
        response = await asyncio.wait_for(self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": _TASK_ANALYSIS_PROMPT}, {"role": "user", "content": cleaned_content}],
            temperature=0.2, max_tokens=700,
        ), timeout=TASK_AI_TIMEOUT_SECONDS)
        return _parse_analysis(_response_content(response))

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result


_TASK_ANALYSIS_PROMPT = """Transforme a mensagem do Discord em um chamado técnico.
Retorne exclusivamente JSON válido com title, description, category, area, environment, priority, risk, confidence, destination, possible_cause, possible_solution, acceptance_criteria, missing_information e subtasks.
Valores permitidos: category=bug|improvement|security|content|infrastructure|other; area=ayla|site|api|database|discord|infrastructure|other; environment=production|staging|both|local|unknown; priority=urgent|high|normal|low; risk=critical|high|medium|low; confidence=high|medium|low; destination=ayla_bugs|site_bugs|incidents|security|suggestions|community|manual_triage.
subtasks é uma lista de 0 a 5 objetos com title, description e type (investigation|implementation|validation). Crie de 2 a 5 apenas se houver fluxo real, com ações verificáveis. Não invente fatos, causa ou solução; trate hipóteses como possível causa. Não crie implementação quando faltarem informações básicas. Use listas de strings para acceptance_criteria e missing_information. Em caso de dúvida, use confidence=low e destination=manual_triage."""


def _response_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as error:
        raise TaskAIError("resposta da IA sem conteúdo") from error
    if not isinstance(content, str) or not content.strip():
        raise TaskAIError("resposta da IA vazia")
    return content.strip()


def _parse_analysis(content: str) -> TaskAnalysis:
    candidate = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        payload = json.loads(candidate)
    except (TypeError, json.JSONDecodeError) as error:
        raise TaskAIError("resposta da IA não é JSON válido") from error
    if not isinstance(payload, dict):
        raise TaskAIError("resposta da IA não é um objeto JSON")
    title, description = _text(payload.get("title"), MAX_TITLE_LENGTH), _text(payload.get("description"), MAX_DESCRIPTION_LENGTH)
    if not title or not description:
        raise TaskAIError("resposta da IA possui title ou description vazios")
    defaults = {"category": "other", "area": "other", "environment": "unknown", "priority": "normal", "risk": "low", "confidence": "low", "destination": "manual_triage"}
    values = {key: _enum(payload.get(key), ENUMS[key], default) for key, default in defaults.items()}
    values["priority"], values["risk"] = _deterministic_overrides(content, values["priority"], values["risk"], values["environment"])
    return TaskAnalysis(title=title, description=description, subtasks=_parse_subtasks(payload.get("subtasks", [])),
        possible_cause=_text(payload.get("possible_cause")), possible_solution=_text(payload.get("possible_solution")),
        acceptance_criteria=_string_list(payload.get("acceptance_criteria")), missing_information=_string_list(payload.get("missing_information")), **values)


def _parse_subtasks(value: Any) -> list[TaskSubtask]:
    if not isinstance(value, list):
        return []
    valid = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title, description = _text(item.get("title"), MAX_TITLE_LENGTH), _text(item.get("description"), MAX_DESCRIPTION_LENGTH)
        if not title or not description:
            continue
        task_type = item.get("type", "investigation")
        if task_type not in {"investigation", "implementation", "validation"}:
            task_type = "investigation"
        valid.append(TaskSubtask(title, description, task_type))
    return valid[:MAX_SUBTASKS] if len(valid) >= 2 else []


def _text(value: Any, limit: int = MAX_FIELD_LENGTH) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _enum(value: Any, allowed: set[str], default: str) -> str:
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value in allowed else default


def _string_list(value: Any) -> list[str]:
    return [_text(item) for item in value if isinstance(item, str) and _text(item)] if isinstance(value, list) else []


def _deterministic_overrides(content: str, priority: str, risk: str, environment: str) -> tuple[str, str]:
    text = content.lower()
    if any(word in text for word in ("token", "senha", "credencial", "vazamento", "credential")):
        risk = "critical"
    if any(word in text for word in ("produção indisponível", "producao indisponivel", "site fora", "serviço fora", "servico fora")):
        priority = "urgent"
    if any(word in text for word in ("perda de dados", "corrupção de dados", "corrupcao de dados")):
        risk = "critical"
    if environment == "staging" and priority == "urgent":
        priority = "normal"
    return priority, risk
