import logging
import unicodedata
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings, validate_clickup_settings
from bot.services.clickup_service import ClickUpError, ClickUpService
from bot.services.linear_service import LinearError, LinearService
from bot.services.task_ai_service import TaskAIService, TaskAnalysis, priority_from_content
from bot.services.task_provider import TaskDraft, task_draft_from_analysis
from bot.services.staging_rbac import can_use_admin_command


logger = logging.getLogger(__name__)
MAX_DESCRIPTION_LENGTH = 12000
SAO_PAULO_TIMEZONE = timezone(timedelta(hours=-3))


def setup_clickup_commands(bot: commands.Bot, settings: Settings) -> ClickUpService:
    try:
        validate_clickup_settings(settings)
    except RuntimeError as error:
        logger.error("ClickUp configuration invalid: %s", error)
    service = ClickUpService(settings.clickup_api_token, free_mode=settings.clickup_free_mode)
    linear_service = LinearService(
        settings.linear_api_key,
        timeout_seconds=settings.linear_timeout_seconds,
        cache_seconds=settings.linear_catalog_cache_seconds,
    )
    task_ai_service = TaskAIService(settings)
    bot._task_ai_service = task_ai_service
    bot._linear_service = linear_service

    async def create_task_from_message(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction, settings.clickup_allowed_role_ids or []):
            await interaction.response.send_message(
                "Você não tem permissão para criar tarefas no ClickUp.", ephemeral=True
            )
            return

        use_linear = settings.linear_enabled and (
            settings.task_provider == "linear" or not settings.clickup_api_token
        )
        if use_linear:
            await _create_linear_task(interaction, message, settings, task_ai_service, linear_service)
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
                description = _task_description(message, interaction.user, analysis.description if analysis else None, analysis)
                subtasks = analysis.subtasks if analysis else []
                logger.info("Task AI generated %s subtasks for %s", len(subtasks), message.id)
                logger.info("Task AI analysis completed for %s", message.id)
            except Exception as error:
                logger.warning("Task AI analysis failed message_id=%s error=%s detail=%s", message.id, _safe_error_name(error), str(error)[:300])
                logger.info("Using ClickUp fallback message_id=%s priority=%s", message.id, semantic_priority)

        try:
            destination = getattr(analysis, "destination", "manual_triage")
            list_id = resolve_destination(settings, destination, catalog)
            task_priority = getattr(analysis, "priority", None) or semantic_priority
            fields_metadata = [] if settings.clickup_free_mode else await _load_custom_field_metadata(service, list_id, settings, destination)
            if settings.clickup_free_mode:
                logger.info("ClickUp Free mode enabled message_id=%s; using native fields, tags and markdown", message.id)
            if analysis is not None and fields_metadata and message.content.strip():
                try:
                    enriched_analysis = await task_ai_service.analyze_task(message.content, catalog, fields_metadata)
                    if enriched_analysis.destination == "manual_triage" and destination != "manual_triage":
                        enriched_analysis = replace(enriched_analysis, destination=destination)
                        logger.warning("Task AI enrichment returned invalid/triage destination; preserving first valid destination=%s message_id=%s", destination, message.id)
                    if enriched_analysis.destination == destination:
                        analysis = enriched_analysis
                        title = analysis.title
                        subtasks = analysis.subtasks
                        task_priority = analysis.priority or semantic_priority
                        logger.info("Task AI enriched with ClickUp Custom Fields message_id=%s fields=%s", message.id, len(analysis.custom_field_values))
                except Exception as error:
                    logger.warning("Task AI Custom Fields enrichment failed message_id=%s error=%s; using first analysis", message.id, _safe_error_name(error))
            custom_fields = _custom_fields(
                analysis, settings, fields_metadata,
                reference_at=message.created_at,
                reported_by=_display_name(message.author),
            )
            description = _task_description(
                message, interaction.user, analysis.description if analysis else None, analysis,
                mapped_field_keys=_mapped_field_keys(analysis, settings, fields_metadata, custom_fields),
            )
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
                    if "item_246" in str(error).casefold() or "max usage for custom task types reached" in str(error).casefold():
                        logger.error("Stopping remaining subtasks because ClickUp custom task type limit was reached parent_id=%s", task_id)
                        break

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


async def _create_linear_task(
    interaction: discord.Interaction,
    message: discord.Message,
    settings: Settings,
    task_ai_service: TaskAIService,
    service: LinearService,
) -> None:
    if not settings.linear_api_key:
        await interaction.response.send_message("A integração com o Linear não está configurada. Avise um administrador.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    title = _task_title(message)
    description = _task_description(message, interaction.user)
    analysis: TaskAnalysis | None = None
    priority = priority_from_content(message.content) if message.content.strip() else "normal"
    if message.content.strip():
        try:
            analysis = await task_ai_service.analyze_task(message.content)
            title = analysis.title
            priority = analysis.priority
            description = _task_description(message, interaction.user, analysis.description, analysis)
        except Exception as error:
            logger.warning("Linear task AI analysis failed message_id=%s error=%s", message.id, _safe_error_name(error))
    if analysis is None:
        draft = TaskDraft(title=title, description=description, priority=priority)
    else:
        draft = task_draft_from_analysis(analysis, description=description)
        if draft.subtasks:
            description += "\n\n## Subtarefas sugeridas\n" + "\n".join(f"- **{item.title}** — {item.description}" for item in draft.subtasks)
            draft = TaskDraft(
                title=draft.title, description=description, priority=draft.priority,
                project=draft.project, team=draft.team, type=draft.type, area=draft.area,
                environment=draft.environment, subtasks=draft.subtasks, metadata=draft.metadata,
            )
    try:
        issue = await service.create_task(draft, context=f"discord message_id={message.id}")
    except LinearError as error:
        logger.error("Linear task creation failed message_id=%s error=%s", message.id, error)
        await interaction.followup.send("❌ Não consegui criar a issue no Linear. O erro foi registrado para análise.", ephemeral=True)
        return
    url = issue.get("url") if isinstance(issue, dict) else None
    suffix = f"\n[ Abrir issue no Linear ]({url})" if url else ""
    await interaction.followup.send(f"✅ Issue criada no Linear: **{title}**{suffix}", ephemeral=True)


def _is_allowed(interaction: discord.Interaction, allowed_role_ids: list[int]) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return can_use_admin_command(interaction, required="administrator") or any(role.id in allowed_role_ids for role in user.roles)


def resolve_destination(settings: Settings, destination: str, catalog: list[dict[str, str]] | None = None) -> str:
    catalog = catalog or []
    destination = str(destination or "").strip()
    normalized_destination = _normalize_destination(destination)
    for item in catalog:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        candidates = (item_id, str(item.get("path") or ""), str(item.get("name") or ""))
        if normalized_destination in {_normalize_destination(candidate) for candidate in candidates if candidate}:
            if destination != item_id:
                logger.warning("Normalized ClickUp destination value=%r to list_id=%s", destination, item_id)
            return item_id
    if destination == "manual_triage":
        manual_query = getattr(settings, "clickup_manual_triage_query", "Backlog > Triage Manual")
        manual_query_normalized = _normalize_destination(manual_query)
        for item in catalog:
            item_id = str(item.get("id") or "").strip()
            path = _normalize_destination(item.get("path"))
            name = _normalize_destination(item.get("name"))
            if item_id and (path == manual_query_normalized or path.endswith(" > " + manual_query_normalized) or name == manual_query_normalized):
                logger.info("Resolved manual_triage through ClickUp catalog query=%r list_id=%s path=%s", manual_query, item_id, item.get("path"))
                return item_id
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


def _normalize_destination(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


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
    mapped_field_keys: set[str] | None = None,
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
    lines = _remove_mapped_markdown(lines, mapped_field_keys or set())
    return "\n".join(lines)[:MAX_DESCRIPTION_LENGTH]


def _remove_mapped_markdown(lines: list[str], mapped: set[str]) -> list[str]:
    labels = {
        "category": "Categoria:", "area": "Área:", "environment": "Ambiente:",
        "priority": "Prioridade:", "risk": "Risco:", "confidence": "Confiança:",
    }
    sections = {
        "possible_cause": "Possível causa", "possible_solution": "Solução possível",
        "acceptance_criteria": "Critérios de aceite",
    }
    result = []
    skip_section = False
    for line in lines:
        if skip_section:
            if not line.strip():
                skip_section = False
            continue
        section_key = next((key for key, title in sections.items() if key in mapped and line == f"### {title}"), None)
        if section_key:
            skip_section = True
            continue
        if any(key in mapped and label in line for key, label in labels.items()):
            continue
        result.append(line)
    return result


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
    # Keep the complete API catalog so fields not present in the legacy map can
    # still be matched automatically by their names.
    return list(available.values())


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


def _custom_fields(
    analysis: TaskAnalysis | None,
    settings: Settings,
    metadata: list[dict[str, object]] | None = None,
    *,
    reference_at: datetime | None = None,
    reported_by: str | None = None,
) -> list[dict[str, object]]:
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
        "browser_version": analysis.browser_version,
        "operating_system": analysis.operating_system,
        "reproduction_steps": "\n".join(f"- {item}" for item in analysis.reproduction_steps),
        "reported_by": reported_by or "",
        "resolution_deadline": (
            int((reference_at + timedelta(days=analysis.resolution_deadline_days)).timestamp() * 1000)
            if reference_at is not None and analysis.resolution_deadline_days is not None else None
        ),
    }
    field_ids = settings.clickup_custom_field_ids or {}
    available = {str(field.get("id")): field for field in metadata or [] if field.get("id")}
    result: list[dict[str, object]] = []
    direct_values = getattr(analysis, "custom_field_values", {}) or {}
    for field_id, value in direct_values.items():
        if str(field_id) in available and value not in (None, ""):
            result.append({"id": str(field_id), "value": value})
            logger.info("Custom Field prepared directly from API schema field_id=%s value=%s", field_id, value)
    for key, value in values.items():
        field_id = field_ids.get(key) or _find_named_field_id(key, available.values())
        field = available.get(field_id or "")
        if not field_id or not value or not field:
            continue
        if str(field_id) in {str(item["id"]) for item in result}:
            continue
        field_type = field.get("type")
        if not _field_type_allowed(key, field_type):
            logger.warning("Custom Field skipped incompatible key=%s field_id=%s type=%s", key, field_id, field_type)
            continue
        if field_type == "drop_down":
            type_config = field.get("type_config") if isinstance(field.get("type_config"), dict) else {}
            options = type_config.get("options", [])
            option = _find_dropdown_option(key, value, options)
            if not option:
                logger.error(
                    "Custom Field dropdown option missing field_id=%s key=%s value=%s available=%s",
                    field_id, key, value,
                    [item.get("name") for item in options if isinstance(item, dict)],
                )
                continue
            value = str(option.get("id"))
        result.append({"id": field_id, "value": value})
        logger.info("Custom Field prepared field_id=%s type=%s value=%s", field_id, field_type, value)
    return result


def _mapped_field_keys(
    analysis: TaskAnalysis,
    settings: Settings,
    metadata: list[dict[str, object]],
    sent_fields: list[dict[str, object]],
) -> set[str]:
    sent_ids = {str(field.get("id")) for field in sent_fields}
    keys = {
        "risk", "category", "area", "environment", "confidence", "possible_cause",
        "possible_solution", "acceptance_criteria", "missing_information", "browser_version",
        "operating_system", "reproduction_steps", "reported_by", "resolution_deadline",
    }
    configured = settings.clickup_custom_field_ids or {}
    available = {str(field.get("id")): field for field in metadata if field.get("id")}
    mapped = set()
    for key in keys:
        field_id = configured.get(key) or _find_named_field_id(key, available.values())
        if field_id and field_id in sent_ids:
            mapped.add(key)
    return mapped


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
    "browser_version": {"browser version", "versao do navegador", "versao navegador"},
    "operating_system": {"operating system", "sistema operacional"},
    "reproduction_steps": {"reproduction steps", "passos para reproduzir", "passos de reproducao"},
    "reported_by": {"reported by", "reportado por"},
    "resolution_deadline": {"resolution deadline", "prazo de resolucao", "prazo de resolução"},
    "risk": {"risco", "risk", "severity level", "nivel de severidade", "nível de severidade"},
}


def _find_named_field_id(key: str, fields) -> str | None:
    aliases = _CUSTOM_FIELD_NAME_ALIASES.get(key, set())
    for field in fields:
        if isinstance(field, dict) and _normalize_field_name(field.get("name")) in aliases and _field_type_allowed(key, field.get("type")):
            return str(field["id"])
    return None


def _field_type_allowed(key: str, field_type: object) -> bool:
    field_type = str(field_type or "")
    if key == "reported_by":
        # A Discord user is not automatically a ClickUp user or an email.
        return field_type in {"text", "short_text"}
    if key == "resolution_deadline":
        return field_type == "date"
    if key in {"risk", "category", "area", "environment", "confidence"}:
        return field_type in {"drop_down", "short_text", "text"}
    if key in {"browser_version", "operating_system", "reproduction_steps", "possible_cause", "possible_solution", "acceptance_criteria", "missing_information"}:
        return field_type in {"short_text", "text", "url"}
    return True


def _normalize_field_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def _find_dropdown_option(key: str, value: object, options: object) -> dict[str, object] | None:
    if not isinstance(options, list):
        return None
    normalized = _normalize_field_name(value)
    candidates = {normalized}
    if key == "risk":
        candidates.add({
            "critical": "critical", "critico": "critical", "crítico": "critical",
            "high": "high", "alto": "high", "medium": "medium", "medio": "medium", "médio": "medium",
            "low": "low", "baixo": "low",
        }.get(_normalize_field_name(value), _normalize_field_name(value)))
    if key == "risk":
        equivalences = (
            {"critical", "critico", "critica", "critical"},
            {"high", "alto", "alta", "major"},
            {"medium", "medio", "media", "minor"},
            {"low", "baixo", "baixa", "trivial"},
        )
        for group in equivalences:
            normalized_group = {_normalize_field_name(item) for item in group}
            if normalized in normalized_group:
                candidates.update(normalized_group)
                break
    for option in options:
        if isinstance(option, dict) and _normalize_field_name(option.get("name")) in candidates:
            return option
    return None


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
