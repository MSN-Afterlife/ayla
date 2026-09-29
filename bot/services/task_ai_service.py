import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from bot.config import Settings

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

logger = logging.getLogger(__name__)
NATIVE_LINEAR_FIELDS_INSTRUCTION = """When the Linear provider is used, also return optional team, project, status, assignee, due_date, cycle and milestone plus field_confidence. field_confidence is an object keyed by fields such as team, project, type, area, environment, priority, due_date, estimate, assignee, cycle and milestone with only high|medium|low. Use null unless the original user message provides clear evidence; never infer fields from Discord origin metadata. due_date must be ISO YYYY-MM-DD only when unambiguous. Estimate is points only when confidence is sufficient; risk and priority are separate. manual_triage identifies unresolved aspects and must not erase independently high-confidence fields."""
DEFAULT_TASK_TIMEZONE = "America/Sao_Paulo"
DEFAULT_TASK_AI_TIMEOUT_SECONDS = 120
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4000
MAX_SUBTASKS = 5
MAX_FIELD_LENGTH = 1000
SUBTASK_TYPES = {"investigation", "implementation", "validation"}
ALLOWED_VALUES = {
    "category": {"bug", "feature", "improvement", "maintenance", "research", "security", "content", "infrastructure", "other"},
    "area": {"ayla", "site", "api", "backend", "frontend", "database", "discord", "minecraft", "network", "security", "devops", "infrastructure", "other"},
    "environment": {"production", "staging", "both", "local", "unknown"},
    "priority": {"urgent", "high", "normal", "low"},
    "risk": {"critical", "high", "medium", "low"},
    "confidence": {"high", "medium", "low"},
}
DEFAULT_DESTINATIONS = {"ayla_bugs", "site_bugs", "incidents", "security", "suggestions", "community", "manual_triage"}


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
    possible_cause: str | None = None
    possible_solution: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    estimated_minutes: int | None = None
    estimate_confidence: str = "low"
    estimate_basis: str = ""
    points: int | None = None
    tags: list[str] = field(default_factory=list)
    browser_version: str = ""
    operating_system: str = ""
    reproduction_steps: list[str] = field(default_factory=list)
    resolution_deadline_days: int | None = None
    custom_field_values: dict[str, Any] = field(default_factory=dict)
    team: str | None = None
    project: str | None = None
    status: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    cycle: str | None = None
    milestone: str | None = None
    field_confidence: dict[str, str] = field(default_factory=dict)


class TaskAIService:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._model = getattr(settings, "task_ai_model", "gpt-5.4-mini")
        self._timeout_seconds = getattr(settings, "task_ai_timeout_seconds", DEFAULT_TASK_AI_TIMEOUT_SECONDS)
        self._destinations = set((settings.clickup_destinations or {}).keys()) or {"manual_triage"}
        self._client = client
        if self._client is None and settings.openai_api_key and AsyncOpenAI is not None:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=0)

    async def analyze_task(self, content: str, catalog: list[dict[str, str]] | None = None, custom_fields: list[dict[str, Any]] | None = None, *, reference_at: datetime | None = None, timezone_name: str = DEFAULT_TASK_TIMEZONE) -> TaskAnalysis:
        cleaned_content = content.strip()
        if not cleaned_content:
            raise TaskAIError("mensagem sem conteúdo textual")
        if self._client is None:
            raise TaskAIError("cliente de IA indisponível")
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": self._build_prompt(catalog, custom_fields, reference_at=reference_at, timezone_name=timezone_name) + "\n" + NATIVE_LINEAR_FIELDS_INSTRUCTION},
                        {"role": "user", "content": cleaned_content},
                    ],
                    response_format={"type": "json_object"},
                    max_completion_tokens=900,
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            raise TaskAIError(f"análise da IA excedeu {self._timeout_seconds:g}s") from error
        allowed = set()
        for item in catalog or []:
            if item.get("id"):
                allowed.add(str(item["id"]))
            if item.get("path"):
                allowed.add(str(item["path"]))
            if item.get("name"):
                allowed.add(str(item["name"]))
        allowed = allowed or self._destinations
        return _parse_analysis(_response_content(response), allowed, source_content=cleaned_content, custom_field_schema=custom_fields, reference_at=reference_at, timezone_name=timezone_name)

    def _build_prompt(self, catalog: list[dict[str, str]] | None = None, custom_fields: list[dict[str, Any]] | None = None, *, reference_at: datetime | None = None, timezone_name: str = DEFAULT_TASK_TIMEZONE) -> str:
        date_context = f"\nCurrent datetime: {_format_current_datetime(reference_at, timezone_name)}\nTimezone: {timezone_name}\nNormalize relative due dates against this context; do not use implicit current date.\n"
        field_lines = []
        for field in custom_fields or []:
            if not field.get("id"):
                continue
            options = (field.get("type_config") or {}).get("options", []) if isinstance(field.get("type_config"), dict) else []
            option_text = ", ".join(f"{item.get('id')}={item.get('name', item.get('label', ''))}" for item in options if isinstance(item, dict))
            field_lines.append(f"- id={field['id']} | name={field.get('name', '')} | type={field.get('type', '')} | options={option_text}")
        field_instruction = "\nCustom Fields reais da lista (use somente estes IDs e opções; não crie nomes nem IDs):\n" + "\n".join(field_lines) + "\ncustom_field_values é obrigatório para todo campo aplicável com evidência. Formato: {\"ID_DO_CAMPO\": \"ID_DA_OPCAO\"}. Para dropdown, use EXATAMENTE o ID da opção, nunca o nome traduzido. Se não houver evidência, não inclua o campo." if field_lines else ""
        if catalog:
            lines = [f"- id={item['id']} | path={item['path']} | name={item.get('name', '')}" for item in catalog[:150] if item.get("id") and item.get("path")]
            lines.insert(0, date_context)
            lines.insert(0, "Inclua estimated_minutes, estimate_confidence, estimate_basis, points, tags, browser_version, operating_system, reproduction_steps e resolution_deadline_days; nao invente dados ausentes.")
            if field_instruction:
                lines.insert(0, field_instruction)
            return _TASK_ANALYSIS_PROMPT + "\nListas reais disponíveis no ClickUp (escolha somente um id):\n" + "\n".join(lines)
        destinations = {
            "ayla_bugs": "bugs no bot Ayla e seus comandos/serviços",
            "site_bugs": "bugs funcionais ou visuais do site",
            "incidents": "indisponibilidade, deploy, servidor, banco ou infraestrutura",
            "security": "vulnerabilidades, credenciais, exposição ou incidentes de segurança",
            "suggestions": "novas funcionalidades e melhorias de produto",
            "community": "conteúdo, moderação ou administração da comunidade/Discord",
            "manual_triage": "mensagem realmente ambígua ou sem dados suficientes",
        }
        lines = [f"- {key}: {destinations.get(key, 'destino configurado pelo sistema')}" for key in sorted(self._destinations)]
        lines.insert(0, date_context)
        lines.insert(0, "Inclua estimated_minutes, estimate_confidence, estimate_basis, points, tags, browser_version, operating_system, reproduction_steps e resolution_deadline_days; nao invente dados ausentes.")
        if field_instruction:
            lines.insert(0, field_instruction)
        return _TASK_ANALYSIS_PROMPT + "\nDestinos disponíveis neste ambiente (use somente estes):\n" + "\n".join(lines)

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result


_TASK_ANALYSIS_PROMPT = """Transforme a mensagem do Discord em um chamado técnico acionável.
Retorne exclusivamente JSON válido com title, description, category, area, environment, priority, risk, confidence, destination, possible_cause, possible_solution, acceptance_criteria, missing_information, estimated_minutes, estimate_confidence, estimate_basis, points, tags e subtasks.
Valores internos permitidos: category=bug|feature|improvement|maintenance|research|security|content|infrastructure|other; area=ayla|site|api|backend|frontend|database|discord|minecraft|network|security|devops|infrastructure|other; environment=production|staging|both|local|unknown; priority=urgent|high|normal|low; risk=critical|high|medium|low; confidence=high|medium|low.
destination deve ser exatamente o id numérico da lista, nunca o caminho ou nome. Para solicitação de funcionalidade sem falha, possible_cause deve ser null ou explicar que não se aplica. possible_solution deve ser técnica, concreta e assumir-se como hipótese. acceptance_criteria deve conter resultados verificáveis. missing_information deve listar o que impede investigação.
subtasks é uma lista de 0 a 5 objetos com title, description e type (investigation|implementation|validation). Para ticket acionável, gere 2 a 5 etapas distintas seguindo investigar/reproduzir, implementar/configurar e validar. Uma etapa isolada útil também deve ser preservada. Não use títulos genéricos como analisar o problema, resolver o problema ou testar a solução; mencione o componente e a ação concreta. Não invente fatos ou evidências."""


def _response_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as error:
        raise TaskAIError("resposta da IA sem conteúdo") from error
    if not isinstance(content, str) or not content.strip():
        raise TaskAIError("resposta da IA vazia")
    return content.strip()


def _parse_analysis(content: str, allowed_destinations: set[str] | None = None, source_content: str | None = None, custom_field_schema: list[dict[str, Any]] | None = None, *, reference_at: datetime | None = None, timezone_name: str = DEFAULT_TASK_TIMEZONE) -> TaskAnalysis:
    logger.debug("Task AI raw JSON=%s", content[:8000])
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
    title = _text(payload.get("title"), MAX_TITLE_LENGTH)
    description = _text(payload.get("description"), MAX_DESCRIPTION_LENGTH)
    if not title or not description:
        raise TaskAIError("resposta da IA possui title ou description vazios")
    defaults = {"category": "other", "area": "other", "environment": "unknown", "priority": "normal", "risk": "low", "confidence": "low"}
    values = {key: _enum(payload.get(key), ALLOWED_VALUES[key], default, key) for key, default in defaults.items()}
    destinations = allowed_destinations or DEFAULT_DESTINATIONS
    raw_destination = payload.get("destination")
    # IDs do not need to be quoted in JSON, so some models return a ClickUp
    # list ID as an integer even though the catalog/API represents it as text.
    # Normalize both forms before checking the discovered catalog.
    if isinstance(raw_destination, bool):
        destination = ""
    elif isinstance(raw_destination, int):
        destination = str(raw_destination)
    elif isinstance(raw_destination, str):
        destination = raw_destination.strip()
    else:
        destination = ""
    if destination not in destinations:
        logger.warning("Task AI destination invalid value=%r allowed=%s; using manual_triage", destination, sorted(destinations))
        destination = "manual_triage"
    priority, risk = _deterministic_overrides(content, values["priority"], values["risk"], values["environment"])
    subtasks, discarded = _parse_subtasks(payload.get("subtasks", []))
    estimated_minutes = _bounded_int(payload.get("estimated_minutes"), 5, 10080)
    resolution_deadline_days = _bounded_int(payload.get("resolution_deadline_days"), 0, 365)
    points = _bounded_int(payload.get("points"), 1, 13)
    estimate_confidence_value = payload.get("estimate_confidence")
    estimate_confidence = _enum(estimate_confidence_value, {"high", "medium", "low"}, "low", "estimate_confidence") if estimate_confidence_value is not None else "low"
    tags = _parse_tags(payload.get("tags"))
    custom_field_values = _validate_custom_field_values(payload.get("custom_field_values"), custom_field_schema or [])
    field_confidence = _parse_field_confidence(payload.get("field_confidence"))
    browser_version = _text(payload.get("browser_version"), 100) or _extract_browser(source_content or "")
    operating_system = _text(payload.get("operating_system"), 100) or _extract_operating_system(source_content or "")
    if points is None:
        points = _default_points(priority, risk)
    if estimated_minutes is None:
        estimated_minutes = points * 60
    if resolution_deadline_days is None:
        resolution_deadline_days = _default_deadline_days(priority)
    normalized_due_date = parse_due_date(payload.get("due_date"), source_content or content, reference_at=reference_at, timezone_name=timezone_name)
    logger.info("Task AI subtasks received=%s valid=%s discarded=%s", len(payload.get("subtasks", [])) if isinstance(payload.get("subtasks", []), list) else 0, len(subtasks), discarded)
    analysis = TaskAnalysis(
        title=title, description=description, subtasks=subtasks, destination=destination,
        possible_cause=_nullable_text(payload.get("possible_cause")), possible_solution=_text(payload.get("possible_solution")),
        acceptance_criteria=_string_list(payload.get("acceptance_criteria")), missing_information=_string_list(payload.get("missing_information")),
        estimated_minutes=estimated_minutes, estimate_confidence=estimate_confidence,
        estimate_basis=_text(payload.get("estimate_basis")) or "Fallback automático baseado em prioridade e risco; validar com a equipe.", points=points, tags=tags,
        browser_version=browser_version,
        operating_system=operating_system,
        reproduction_steps=_string_list(payload.get("reproduction_steps")),
        resolution_deadline_days=resolution_deadline_days,
        custom_field_values=custom_field_values,
        team=_nullable_text(payload.get("team")), project=_nullable_text(payload.get("project")),
        status=_nullable_text(payload.get("status")), assignee=_nullable_text(payload.get("assignee")),
        due_date=normalized_due_date, cycle=_nullable_text(payload.get("cycle")),
        milestone=_nullable_text(payload.get("milestone")),
        field_confidence=field_confidence,
        priority=priority, risk=risk, **{key: values[key] for key in ("category", "area", "environment", "confidence")},
    )
    logger.info("Task AI normalized analysis=%s", analysis)
    return analysis


def _parse_field_confidence(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    allowed_fields = {"team", "project", "status", "type", "area", "environment", "priority", "due_date", "estimate", "assignee", "cycle", "milestone"}
    return {str(key): str(level).casefold() for key, level in value.items() if str(key) in allowed_fields and str(level).casefold() in {"high", "medium", "low"}}


def _format_current_datetime(reference_at: datetime | None, timezone_name: str) -> str:
    value = reference_at or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        if ZoneInfo is not None:
            value = value.astimezone(ZoneInfo(timezone_name))
    except Exception:
        pass
    return value.isoformat(timespec="seconds")


def parse_due_date(value: Any, source_content: str = "", *, reference_at: datetime | None = None, timezone_name: str = DEFAULT_TASK_TIMEZONE) -> str | None:
    """Normalize only unambiguous absolute or Portuguese relative dates."""
    raw = value.strip().casefold() if isinstance(value, str) else ""
    text = f"{raw} {source_content.casefold()}"
    now = reference_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        if ZoneInfo is not None:
            now = now.astimezone(ZoneInfo(timezone_name))
    except Exception:
        pass
    today = now.date()
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso:
        try:
            return date.fromisoformat(iso.group(1)).isoformat()
        except ValueError:
            return None
    full = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", text)
    if full:
        try:
            return date(int(full.group(3)), int(full.group(2)), int(full.group(1))).isoformat()
        except ValueError:
            return None
    if re.search(r"\b(hoje|today)\b", text):
        return today.isoformat()
    if re.search(r"\b(amanh[ãa]|tomorrow)\b", text):
        return (today + timedelta(days=1)).isoformat()
    weekdays = {"segunda": 0, "segunda-feira": 0, "monday": 0, "terca": 1, "terça": 1, "terça-feira": 1, "tuesday": 1, "quarta": 2, "quarta-feira": 2, "wednesday": 2, "quinta": 3, "quinta-feira": 3, "thursday": 3, "sexta": 4, "sexta-feira": 4, "friday": 4, "sabado": 5, "sábado": 5, "saturday": 5, "domingo": 6, "sunday": 6}
    weekday = next((day for name, day in weekdays.items() if re.search(rf"\b{name}\b", text)), None)
    if weekday is not None:
        delta = (weekday - today.weekday()) % 7
        if delta == 0:
            delta = 7
        return (today + timedelta(days=delta)).isoformat()
    months = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
    month_match = re.search(r"\b([0-3]?\d)\s+de\s+([a-zç]+)(?:\s+de\s+(20\d{2}))?\b", text)
    if month_match and month_match.group(2) in months:
        day, month = int(month_match.group(1)), months[month_match.group(2)]
        year = int(month_match.group(3) or today.year)
        if not month_match.group(3) and (month, day) < (today.month, today.day):
            year += 1
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    day_match = re.search(r"\b(?:até|ate|dia)\s+(?:dia\s+)?([0-3]?\d)\b", text)
    if day_match:
        day = int(day_match.group(1))
        month = today.month + (1 if day <= today.day else 0)
        year = today.year + (1 if month == 13 else 0)
        month = 1 if month == 13 else month
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def _parse_subtasks(value: Any) -> tuple[list[TaskSubtask], list[str]]:
    if not isinstance(value, list):
        return [], ["subtasks não é uma lista"]
    valid: list[TaskSubtask] = []
    discarded: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            discarded.append(f"#{index}: item não é objeto")
            continue
        title = _text(item.get("title"), MAX_TITLE_LENGTH)
        description = _text(item.get("description"), MAX_DESCRIPTION_LENGTH)
        if not title or not description:
            discarded.append(f"#{index}: title/description vazio ou inválido")
            continue
        task_type = item.get("type", "investigation")
        if task_type not in SUBTASK_TYPES:
            discarded.append(f"#{index}: type inválido ({task_type!r}), normalizado para investigation")
            task_type = "investigation"
        valid.append(TaskSubtask(title, description, task_type))
    return valid[:MAX_SUBTASKS], discarded + ([f"{len(valid) - MAX_SUBTASKS} excedentes ao limite de {MAX_SUBTASKS}"] if len(valid) > MAX_SUBTASKS else [])


def _text(value: Any, limit: int = MAX_FIELD_LENGTH) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    return _text(value) or None


def _enum(value: Any, allowed: set[str], default: str, field_name: str) -> str:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if normalized in allowed:
        return normalized
    logger.warning("Task AI invalid %s value=%r; fallback=%s reason=not_allowed_or_missing", field_name, value, default)
    return default


def _string_list(value: Any) -> list[str]:
    return [_text(item) for item in value if isinstance(item, str) and _text(item)] if isinstance(value, list) else []


def _bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _parse_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, str):
            continue
        tag = re.sub(r"[^a-z0-9_-]+", "-", item.casefold()).strip("-_")[:40]
        if tag and tag not in result:
            result.append(tag)
    return result[:5]


def _validate_custom_field_values(value: Any, schema: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fields = {str(item.get("id")): item for item in schema if item.get("id")}
    result = {}
    for field_id, raw_value in value.items():
        field = fields.get(str(field_id))
        if not field or raw_value in (None, ""):
            continue
        field_type = field.get("type")
        if field_type == "drop_down":
            options = (field.get("type_config") or {}).get("options", []) if isinstance(field.get("type_config"), dict) else []
            if not any(isinstance(item, dict) and str(item.get("id")) == str(raw_value) for item in options):
                continue
        elif field_type in {"text", "short_text", "url"} and not isinstance(raw_value, str):
            continue
        result[str(field_id)] = raw_value
    return result


def _extract_browser(text: str) -> str:
    match = re.search(r"\b(chrome|google chrome|edge|microsoft edge|firefox|safari|opera|brave)(?:\s+(\d+(?:\.\d+)*))?\b", text, re.IGNORECASE)
    if not match:
        return ""
    name = match.group(1)
    version = match.group(2)
    return f"{name} {version}" if version else name


def _extract_operating_system(text: str) -> str:
    patterns = (
        (r"\bwindows(?:\s+(\d+(?:\.\d+)*))?\b", "Windows"),
        (r"\b(?:macos|mac\s*os|osx)\b", "macOS"),
        (r"\blinux\b", "Linux"),
        (r"\bandroid\b", "Android"),
        (r"\b(?:ios|iphone|ipad)\b", "iOS"),
    )
    for pattern, label in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return f"{label} {match.group(1)}" if match.lastindex and match.group(1) else label
    return ""


def _default_points(priority: str, risk: str) -> int:
    if risk == "critical" or priority == "urgent":
        return 8
    if risk == "high" or priority == "high":
        return 5
    if priority == "low":
        return 1
    return 3


def _default_deadline_days(priority: str) -> int:
    return {"urgent": 1, "high": 3, "normal": 7, "low": 14}.get(priority, 7)


def _legacy_deterministic_overrides(content: str, priority: str, risk: str, environment: str) -> tuple[str, str]:
    text = content.casefold()
    old_priority, old_risk = priority, risk
    explicit_urgent = (
        "urgencia", "urgência", "urgente", "imediatamente", "imediato",
        "impactando todos", "todos os usuarios", "todos os usuários",
        "cego", "cegueira", "convulsao", "convulsão", "epileptic", "epiléptic",
        "risco legal", "risco à saúde", "risco a saude",
    )
    if any(word in text for word in ("token", "senha", "credencial", "credential", "vazamento", "exposto", "exposta", "perda de dados", "corrupção de dados", "corrupcao de dados")):
        priority, risk = "urgent", "critical"
    elif any(word in text for word in ("produção indisponível", "producao indisponivel", "site fora", "serviço fora", "servico fora", "tudo fora do ar")):
        priority = "urgent"
    elif environment == "production" and any(word in text for word in ("função importante quebrada", "funcao importante quebrada", "não funciona", "nao funciona", "erro crítico", "erro critico")):
        priority = _max_priority(priority, "high")
    elif environment == "staging":
        priority = "normal"
    elif any(word in text for word in ("visual", "cor", "css", "alinhamento", "texto errado")) and not any(word in text for word in ("não funciona", "nao funciona", "indisponível", "indisponivel")):
        priority = "low"
    if any(word in text for word in explicit_urgent):
        priority = "urgent"
        if any(word in text for word in ("cego", "cegueira", "convulsao", "convulsão", "epileptic", "epiléptic", "risco à saúde", "risco a saude")):
            risk = "critical"
    if (old_priority, old_risk) != (priority, risk):
        logger.info("Task AI deterministic override priority=%s->%s risk=%s->%s", old_priority, priority, old_risk, risk)
    return priority, risk


def _deterministic_overrides(content: str, priority: str, risk: str, environment: str) -> tuple[str, str]:
    """Apply bounded urgency rules without conflating risk with priority."""
    text = content.casefold()
    old_priority, old_risk = priority, risk
    security_or_data = any(word in text for word in ("token", "senha", "credencial", "credential", "vazamento", "exposto", "exposta", "perda de dados", "corrupção de dados", "corrupcao de dados"))
    health_critical = any(word in text for word in ("cego", "cegueira", "convulsão", "convulsao", "epileptic", "epiléptic", "risco à saúde", "risco a saude"))
    outage = environment != "staging" and any(word in text for word in ("produção inteira", "producao inteira", "produção indisponível", "producao indisponivel", "site fora", "serviço crítico fora", "servico critico fora", "tudo fora do ar"))
    immediate = any(word in text for word in ("agora", "imediatamente", "imediato", "bloqueio operacional", "impactando todos", "todos os usuarios", "todos os usuários"))
    strong_urgent = security_or_data or health_critical or outage or (immediate and (environment == "production" or "fora do ar" in text or "bloqueio operacional" in text))
    if security_or_data:
        priority, risk = "urgent", "critical"
    elif health_critical:
        priority, risk = "urgent", "critical"
    elif outage:
        priority = "urgent"
    elif environment == "production" and any(word in text for word in ("função importante quebrada", "funcao importante quebrada", "não funciona", "nao funciona", "erro crítico", "erro critico")):
        priority = _max_priority(priority, "high")
    elif environment == "staging":
        if priority == "urgent" or any(word in text for word in ("erro 500", "erro crítico", "erro critico", "regressão", "regressao", "indisponível", "indisponivel")):
            priority = "high"
    elif any(word in text for word in ("visual", "cor", "css", "alinhamento", "texto errado")) and not any(word in text for word in ("não funciona", "nao funciona", "indisponível", "indisponivel")):
        priority = "low"
    if strong_urgent:
        priority = "urgent"
    if (old_priority, old_risk) != (priority, risk):
        logger.info("Task AI deterministic override priority=%s->%s risk=%s->%s", old_priority, priority, old_risk, risk)
    return priority, risk


def _max_priority(current: str, minimum: str) -> str:
    order = {"urgent": 4, "high": 3, "normal": 2, "low": 1}
    return current if order.get(current, 2) >= order[minimum] else minimum


def priority_from_content(content: str) -> str:
    return _deterministic_overrides(content, "normal", "low", "unknown")[0]
