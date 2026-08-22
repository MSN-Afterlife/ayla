import logging
import unicodedata
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings, validate_clickup_settings
from bot.services.clickup_service import ClickUpError, ClickUpService
from bot.services.task_ai_service import TaskAIService, TaskAnalysis, priority_from_content


logger = logging.getLogger(__name__)
MAX_DESCRIPTION_LENGTH = 12000
SAO_PAULO_TIMEZONE = timezone(timedelta(hours=-3))


def setup_clickup_commands(bot: commands.Bot, settings: Settings) -> ClickUpService:
    try:
        validate_clickup_settings(settings)
    except RuntimeError as error:
        logger.error("ClickUp configuration invalid: %s", error)
    service = ClickUpService(settings.clickup_api_token)
    task_ai_service = TaskAIService(settings)
    bot._task_ai_service = task_ai_service

    async def create_task_from_message(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction, settings.clickup_allowed_role_ids or []):
            await interaction.response.send_message(
                "Você não tem permissão para criar tarefas no ClickUp.", ephemeral=True
            )
            return

        if not settings.clickup_api_token:
            logger.error(
                "ClickUp is not configured: token=%s destinations=%s",
                bool(settings.clickup_api_token),
                bool(settings.clickup_destinations),
            )
            await interaction.response.send_message(
                "A integração com o ClickUp não está configurada. Avise um administrador.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        logger.info(
            "ClickUp task creation requested by %s from %s",
            interaction.user.id,
            message.id,
        )
        title = _task_title(message)
        description = _task_description(message, interaction.user)
        subtasks = []
        analysis = None
        semantic_priority = priority_from_content(message.content) if message.content.strip() else None
        catalog = []
        try:
            catalog = await service.get_list_catalog(
                settings.clickup_workspace_id,
                cache_seconds=settings.clickup_catalog_cache_seconds,
            )
        except ClickUpError as error:
            logger.error("ClickUp catalog unavailable message_id=%s error=%s; using configured/legacy routing", message.id, error)
        if message.content.strip():
            logger.info("Task AI analysis requested for Discord message %s", message.id)
            try:
                analysis = await task_ai_service.analyze_task(message.content, catalog)
                title = analysis.title
                description = _task_description(message, interaction.user, analysis.description, analysis)
                subtasks = analysis.subtasks
                logger.info("Task AI generated %s subtasks for %s", len(subtasks), message.id)
                logger.info("Task AI analysis completed for %s", message.id)
            except Exception as error:
                logger.warning("Task AI analysis failed message_id=%s error=%s detail=%s", message.id, _safe_error_name(error), str(error)[:300])
                logger.info("Using ClickUp fallback message_id=%s priority=%s", message.id, semantic_priority)

        try:
            destination = getattr(analysis, "destination", "manual_triage")
            list_id = resolve_destination(settings, destination, catalog)
            task_priority = getattr(analysis, "priority", None) or semantic_priority
            fields_metadata = await _load_custom_field_metadata(service, list_id, settings, destination)
            custom_fields = _custom_fields(analysis, settings, fields_metadata)
            task_tags = _task_tags(analysis)
            time_estimate = analysis.estimated_minutes * 60 * 1000 if analysis and analysis.estimated_minutes else None
            points = analysis.points if analysis else None
            logger.info("Task triage message_id=%s destination=%s list_id=%s priority=%s subtasks=%s custom_fields=%s", message.id, _destination_label(destination, catalog), list_id, task_priority, len(subtasks), [field["id"] for field in custom_fields])
            task = await service.create_task(
                list_id, title, description, priority=task_priority, custom_fields=custom_fields,
                time_estimate=time_estimate, points=points,
                context=f"parent message_id={message.id}",
            )
        except ClickUpError:
            await interaction.followup.send(
                "❌ Não consegui criar a tarefa no ClickUp. O erro foi registrado para análise.", ephemeral=True
            )
            return

        task_id = str(task.get("id")) if task.get("id") else ""
        if task_id and analysis:
            space_id = next((item.get("space_id") for item in catalog if item.get("id") == list_id), None)
            await service.ensure_task_tags(task_id, space_id, _task_tags(analysis))
        created_subtasks = 0
        if subtasks and task_id:
            for index, subtask in enumerate(subtasks, start=1):
                logger.info("Creating ClickUp subtask %s/%s for parent %s", index, len(subtasks), task_id)
                try:
                    created = await service.create_subtask(
                        list_id,
                        task_id,
                        subtask.title,
                        subtask.description,
                        priority=task_priority,
                        context=f"subtask {index}/{len(subtasks)} message_id={message.id}",
                    )
                    created_subtasks += 1
                    logger.info(
                        "ClickUp subtask %s created for parent %s",
                        created.get("id", "desconhecido"),
                        task_id,
                    )
                except ClickUpError as error:
                    logger.error("ClickUp subtask creation failed parent_id=%s index=%s error=%s", task_id, index, error)

        if task_id and custom_fields:
            await _verify_custom_fields(service, task_id, custom_fields, message.id)

        task_url = task.get("url")
        logger.info("ClickUp task %s created from Discord message %s", task_id or "desconhecido", message.id)
        suffix = f"\n[ Abrir tarefa no ClickUp ]({task_url})" if task_url else ""
        if subtasks and created_subtasks < len(subtasks):
            status = f"⚠️ {created_subtasks} de {len(subtasks)} subtarefas foram criadas."
        elif created_subtasks:
            status = f"com {created_subtasks} subtarefas"
        else:
            status = ""
        detail = f" {status}" if status else ""
        await interaction.followup.send(f"✅ Tarefa criada no ClickUp{detail}: **{title}**{suffix}", ephemeral=True)

    context_menu = app_commands.ContextMenu(
        name="📋 Criar tarefa",
        callback=create_task_from_message,
        type=discord.AppCommandType.message,
    )
    bot.tree.add_command(context_menu)
    return service


def _is_allowed(interaction: discord.Interaction, allowed_role_ids: list[int]) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return user.guild_permissions.administrator or any(role.id in allowed_role_ids for role in user.roles)


def resolve_destination(settings: Settings, destination: str, catalog: list[dict[str, str]] | None = None) -> str:
    catalog = catalog or []
    catalog_ids = {item.get("id") for item in catalog}
    if destination in catalog_ids:
        return destination
    destinations = settings.clickup_destinations or {}
    if destination not in destinations:
        if destination == "manual_triage" and settings.clickup_list_id:
            logger.warning(
                "Using legacy CLICKUP_LIST_ID for manual_triage; configure CLICKUP_DESTINATIONS for automatic routing."
            )
            return settings.clickup_list_id
        raise ClickUpError(f"Destino {destination!r} não possui list_id configurado.")
    list_id = destinations[destination]
    if not list_id.strip():
        raise ClickUpError(f"Destino {destination!r} possui list_id vazio.")
    return list_id


def _destination_label(destination: str, catalog: list[dict[str, str]]) -> str:
    for item in catalog:
        if item.get("id") == destination:
            return f"{item.get('path', item.get('name', destination))} ({destination})"
    return destination


def _task_tags(analysis: TaskAnalysis) -> list[str]:
    tags = list(analysis.tags)
    for value in (analysis.category, analysis.area, analysis.environment):
        if value and value not in {"other", "unknown"} and value not in tags:
            tags.append(value)
    return tags[:5]


def _task_title(message: discord.Message) -> str:
    content = " ".join(message.content.split())
    if content:
        return content[:200]
    channel_name = getattr(message.channel, "name", "canal")
    return f"Mensagem de {message.author.display_name} em #{channel_name}"[:200]


def _task_description(
    message: discord.Message,
    creator: discord.User | discord.Member,
    ai_summary: str | None = None,
    analysis: TaskAnalysis | None = None,
) -> str:
    content = message.content.strip() or "_(mensagem sem conteúdo textual)_"
    author_name = _display_name(message.author)
    creator_name = _display_name(creator)
    channel_name = getattr(message.channel, "name", "canal")
    guild_name = message.guild.name if message.guild else "Mensagem direta"
    lines = []
    if ai_summary:
        lines.extend(["## Resumo", ai_summary.strip(), ""])
    if analysis:
        lines.extend([
            "## Triagem automática",
            f"- **Categoria:** {_visible_value('category', analysis.category)}", f"- **Área:** {_visible_value('area', analysis.area)}",
            f"- **Ambiente:** {_visible_value('environment', analysis.environment)}", f"- **Prioridade:** {_visible_value('priority', analysis.priority)}",
            f"- **Risco:** {_visible_value('risk', analysis.risk)}", f"- **Confiança:** {_visible_value('confidence', analysis.confidence)}",
            f"- **Destino:** {analysis.destination}", "",
        ])
        if analysis.possible_cause:
            lines.extend(["### Possível causa", analysis.possible_cause, ""])
        if analysis.possible_solution:
            lines.extend(["### Solução possível", analysis.possible_solution, ""])
        if analysis.acceptance_criteria:
            lines.extend(["### Critérios de aceite", *[f"- {item}" for item in analysis.acceptance_criteria], ""])
        if analysis.missing_information:
            lines.extend(["### Informações ausentes", *[f"- {item}" for item in analysis.missing_information], ""])
    lines.extend([
        "## Mensagem original",
        content,
        "",
        f"**Autor:** @{author_name}",
        f"**Canal:** #{channel_name}",
        f"**Servidor:** {guild_name}",
        f"**Enviada em:** {_format_message_datetime(message.created_at)}",
        f"**Criada por:** @{creator_name}",
        "",
        f"[Ir para a mensagem original]({message.jump_url})",
        "",
        "<details>",
        "<summary>Identificadores técnicos</summary>",
        "",
        f"- Autor: `{message.author.id}`",
        f"- Canal: `{getattr(message.channel, 'id', 'indisponível')}`",
        f"- Servidor: `{message.guild.id if message.guild else 'mensagem direta'}`",
        f"- Mensagem: `{message.id}`",
        f"- Criada por: `{creator.id}`",
        "",
        "</details>",
    ])
    if message.attachments:
        lines.extend(["", "## Anexos", *[f"- {attachment.url}" for attachment in message.attachments]])
    return "\n".join(lines)[:MAX_DESCRIPTION_LENGTH]


async def _load_custom_field_metadata(service: ClickUpService, list_id: str, settings: Settings, destination: str) -> list[dict[str, object]]:
    try:
        fields = await service.get_list_custom_fields(list_id)
    except ClickUpError as error:
        logger.error("Custom Fields lookup failed destination=%s list_id=%s error=%s; continuing markdown_only", destination, list_id, error)
        return []
    available = {str(field.get("id")): field for field in fields if field.get("id")}
    if not settings.clickup_custom_field_ids:
        logger.info("Custom Fields discovered automatically destination=%s list_id=%s count=%s; Markdown remains enabled", destination, list_id, len(available))
        return list(available.values())
    missing = {key: field_id for key, field_id in settings.clickup_custom_field_ids.items() if field_id not in available}
    if missing:
        logger.error("Custom Fields configuration invalid destination=%s missing=%s; invalid fields will be skipped", destination, missing)
    return [available[field_id] for field_id in settings.clickup_custom_field_ids.values() if field_id in available]


async def _verify_custom_fields(service: ClickUpService, task_id: str, sent_fields: list[dict[str, object]], message_id: int) -> None:
    try:
        task = await service.get_task(task_id)
    except ClickUpError as error:
        logger.error("Custom Fields verification failed message_id=%s task_id=%s error=%s", message_id, task_id, error)
        return
    actual = {str(field.get("id")): field.get("value") for field in task.get("custom_fields", []) if isinstance(field, dict)}
    for field in sent_fields:
        field_id = str(field["id"])
        if field_id not in actual or actual[field_id] in (None, ""):
            logger.error("Custom Field not confirmed message_id=%s task_id=%s field_id=%s", message_id, task_id, field_id)
        else:
            logger.info("Custom Field confirmed message_id=%s task_id=%s field_id=%s value=%s", message_id, task_id, field_id, actual[field_id])


def _custom_fields(analysis: TaskAnalysis | None, settings: Settings, metadata: list[dict[str, object]] | None = None) -> list[dict[str, object]]:
    """Monta Custom Fields reais; campos sem configuração ficam somente no Markdown."""
    if analysis is None:
        return []
    values = {
        "risk": _visible_value("risk", analysis.risk), "category": _visible_value("category", analysis.category),
        "area": _visible_value("area", analysis.area), "environment": _visible_value("environment", analysis.environment),
        "confidence": _visible_value("confidence", analysis.confidence), "possible_cause": analysis.possible_cause,
        "possible_solution": analysis.possible_solution,
        "acceptance_criteria": "\n".join(f"- {item}" for item in analysis.acceptance_criteria),
        "missing_information": "\n".join(f"- {item}" for item in analysis.missing_information),
    }
    field_ids = settings.clickup_custom_field_ids or {}
    available = {str(field.get("id")): field for field in metadata or [] if field.get("id")}
    result: list[dict[str, object]] = []
    for key, value in values.items():
        field_id = field_ids.get(key) or _find_named_field_id(key, available.values())
        field = available.get(field_id or "")
        if not field_id or not value or not field:
            continue
        field_type = field.get("type")
        if field_type == "drop_down":
            type_config = field.get("type_config") if isinstance(field.get("type_config"), dict) else {}
            options = type_config.get("options", [])
            option = next((item for item in options if isinstance(item, dict) and str(item.get("name", "")).casefold() == str(value).casefold()), None)
            if not option:
                logger.error("Custom Field dropdown option missing field_id=%s value=%s", field_id, value)
                continue
            value = str(option.get("id"))
        result.append({"id": field_id, "value": value})
        logger.info("Custom Field prepared field_id=%s type=%s value=%s", field_id, field_type, value)
    return result


_CUSTOM_FIELD_NAME_ALIASES = {
    "risk": {"risco", "risk"},
    "category": {"categoria", "category"},
    "area": {"area"},
    "environment": {"ambiente", "environment"},
    "confidence": {"confianca", "confidence"},
    "possible_cause": {"possivel causa", "possible cause", "causa"},
    "possible_solution": {"possivel solucao", "possible solution", "solucao"},
    "acceptance_criteria": {"criterios de aceite", "acceptance criteria"},
    "missing_information": {"informacoes ausentes", "missing information"},
}


def _find_named_field_id(key: str, fields) -> str | None:
    aliases = _CUSTOM_FIELD_NAME_ALIASES.get(key, set())
    for field in fields:
        if isinstance(field, dict) and _normalize_field_name(field.get("name")) in aliases:
            return str(field["id"])
    return None


def _normalize_field_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def _visible_value(field: str, value: str) -> str:
    labels = {
        "risk": {"critical": "Crítico", "high": "Alto", "medium": "Médio", "low": "Baixo"},
        "category": {"bug": "Bug", "improvement": "Melhoria", "security": "Segurança", "content": "Conteúdo", "infrastructure": "Infraestrutura", "other": "Outro"},
        "area": {"ayla": "Ayla", "site": "Site", "api": "API", "database": "Banco de dados", "discord": "Discord", "infrastructure": "Infraestrutura", "other": "Outro"},
        "environment": {"production": "Produção", "staging": "Staging", "both": "Ambos", "local": "Local", "unknown": "Desconhecido"},
        "confidence": {"high": "Alta", "medium": "Média", "low": "Baixa"},
        "priority": {"urgent": "Urgente", "high": "Alta", "normal": "Normal", "low": "Baixa"},
    }
    return labels.get(field, {}).get(value, value)


def _safe_error_name(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    return type(error).__name__


def _display_name(user: discord.User | discord.Member) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", "usuário")
    return name.replace("@", "＠").strip() or "usuário"


def _format_message_datetime(value: datetime) -> str:
    local_time = value.astimezone(SAO_PAULO_TIMEZONE)
    return local_time.strftime("%d/%m/%Y %H:%M")
