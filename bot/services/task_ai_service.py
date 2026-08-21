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


class TaskAIError(Exception):
    """Falha não crítica da análise de uma mensagem para o ClickUp."""


@dataclass(frozen=True)
class TaskAnalysis:
    title: str
    description: str
    subtasks: list["TaskSubtask"] = field(default_factory=list)


@dataclass(frozen=True)
class TaskSubtask:
    title: str
    description: str


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

        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _TASK_ANALYSIS_PROMPT},
                    {"role": "user", "content": cleaned_content},
                ],
                temperature=0.2,
                max_tokens=300,
            ),
            timeout=TASK_AI_TIMEOUT_SECONDS,
        )
        raw_content = _response_content(response)
        return _parse_analysis(raw_content)

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result


_TASK_ANALYSIS_PROMPT = """Você transforma mensagens informais do Discord em tarefas técnicas curtas e objetivas.

Retorne exclusivamente JSON válido com exatamente estes campos:
{"title": "...", "description": "...", "subtasks": []}

Regras:
- crie subtasks somente quando existirem duas ou mais demandas independentes;
- nÃ£o decomponha uma Ãºnica tarefa em etapas de execuÃ§Ã£o;
- retorne subtasks como [] quando nÃ£o houver decomposiÃ§Ã£o Ãºtil;
- cada subtarefa deve conter title e description;
- crie no mÃ¡ximo 5 subtarefas;
- não invente fatos, contexto ou solução técnica;
- preserve o significado da mensagem;
- transforme linguagem informal em linguagem clara;
- o título deve representar uma ação e ser breve;
- se a mensagem for vaga, seja conservador e não invente contexto;
- não inclua Markdown fora dos valores dos campos JSON."""


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
    title = payload.get("title")
    description = payload.get("description")
    if not isinstance(title, str) or not isinstance(description, str):
        raise TaskAIError("resposta da IA sem title ou description válidos")

    title = " ".join(title.split())[:MAX_TITLE_LENGTH].strip()
    description = description.strip()[:MAX_DESCRIPTION_LENGTH]
    if not title or not description:
        raise TaskAIError("resposta da IA possui campos vazios")
    return TaskAnalysis(title=title, description=description, subtasks=_parse_subtasks(payload.get("subtasks", [])))


def _parse_subtasks(value: Any) -> list[TaskSubtask]:
    if not isinstance(value, list):
        return []

    valid: list[TaskSubtask] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        description = item.get("description")
        if not isinstance(title, str) or not isinstance(description, str):
            continue
        title = " ".join(title.split())[:MAX_TITLE_LENGTH].strip()
        description = description.strip()[:MAX_DESCRIPTION_LENGTH]
        if title and description:
            valid.append(TaskSubtask(title=title, description=description))

    if len(valid) < 2:
        return []
    return valid[:MAX_SUBTASKS]
