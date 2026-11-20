"""Server moderation commands with site-configurable member embeds."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import utcnow

from bot.config import Settings
from bot.services.moderation_embed_config import ModerationEmbedConfigStore
from bot.services.moderation_service import (
    ModerationService,
    format_duration,
    parse_duration,
)
from bot.services.staging_rbac import staging_app_command_check, staging_prefix_check


ACTION_LABELS = {
    "ban": "Banimento",
    "unban": "Desbanimento",
    "kick": "Expulsao",
    "mute": "Silenciamento",
    "unmute": "Dessilenciamento",
    "warn": "Advertencia",
}


def setup_moderation_commands(
    bot: commands.Bot,
    settings: Settings,
    *,
    moderation_service: ModerationService | None = None,
    embed_store: ModerationEmbedConfigStore | None = None,
) -> tuple[ModerationService, ModerationEmbedConfigStore]:
    service = moderation_service or getattr(bot, "_moderation_service", None) or ModerationService(settings.moderation_database_path)
    embeds = embed_store or getattr(bot, "_moderation_embeds", None) or ModerationEmbedConfigStore(
        settings.moderation_embeds_path,
        settings.moderation_assets_dir,
    )
    bot._moderation_service = service
    bot._moderation_embeds = embeds

    @bot.hybrid_command(name="ban", aliases=["banir"], description="Bane um usuario do servidor.")
    @commands.check(staging_prefix_check("ban_members"))
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(
        usuario="Membro ou usuario a ser banido.",
        apagar_dias="Quantidade de dias de mensagens para apagar (0 a 7).",
        motivo="Motivo do banimento.",
    )
    async def ban_command(
        ctx: commands.Context,
        usuario: discord.User,
        apagar_dias: int = 0,
        *,
        motivo: str = "Sem motivo informado",
    ) -> None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return

        if not (0 <= apagar_dias <= 7):
            await _reply(ctx, "`apagar_dias` precisa estar entre 0 e 7.", ephemeral=True)
            return

        target_member = ctx.guild.get_member(usuario.id)
        if target_member is not None:
            error_msg = _check_hierarchy(ctx.guild, ctx.author, target_member, bot.user)
            if error_msg:
                await _reply(ctx, error_msg, ephemeral=True)
                return
        elif bot.user and usuario.id == bot.user.id:
            await _reply(ctx, "Eu nao posso me banir.", ephemeral=True)
            return
        elif usuario.id == ctx.author.id:
            await _reply(ctx, "Voce nao pode banir a si mesmo.", ephemeral=True)
            return

        await _defer_if_slash(ctx)
        case = service.record_case(
            guild_id=ctx.guild.id,
            user_id=usuario.id,
            moderator_id=ctx.author.id,
            action="ban",
            reason=motivo,
        )
        context = _build_member_context(
            guild=ctx.guild,
            target=target_member or usuario,
            moderator=ctx.author,
            reason=case.reason,
            case_id=case.id,
            warns_count=service.count_active_warns(ctx.guild.id, usuario.id),
        )
        await _maybe_dm_member(embeds, "ban", target_member or usuario, context, ctx.guild)

        try:
            await ctx.guild.ban(
                usuario,
                reason=f"[{ctx.author}] {case.reason}"[:512],
                delete_message_days=apagar_dias,
            )
        except discord.Forbidden:
            await _reply(ctx, "Nao tenho permissao suficiente para banir esse usuario.", ephemeral=True)
            return
        except discord.HTTPException as error:
            await _reply(ctx, f"Nao foi possivel concluir o banimento: {error}", ephemeral=True)
            return

        await _send_member_moderation_response(ctx, service, embeds, "ban", context, target_member or usuario)

    @bot.hybrid_command(name="unban", aliases=["desbanir"], description="Remove o banimento de um usuario pelo ID.")
    @commands.check(staging_prefix_check("ban_members"))
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(usuario_id="ID do usuario banido.", motivo="Motivo do desbanimento.")
    async def unban_command(
        ctx: commands.Context,
        usuario_id: str,
        *,
        motivo: str = "Sem motivo informado",
    ) -> None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return

        parsed_id = _parse_user_id(usuario_id)
        if parsed_id is None:
            await _reply(ctx, "Informe um ID de usuario valido.", ephemeral=True)
            return

        await _defer_if_slash(ctx)
        try:
            user = await bot.fetch_user(parsed_id)
            await ctx.guild.unban(user, reason=f"[{ctx.author}] {motivo}"[:512])
        except discord.NotFound:
            await _reply(ctx, "Usuario nao encontrado ou nao esta banido neste servidor.", ephemeral=True)
            return
        except discord.Forbidden:
            await _reply(ctx, "Nao tenho permissao para desbanir usuarios.", ephemeral=True)
            return
        except discord.HTTPException as error:
            await _reply(ctx, f"Nao foi possivel desbanir: {error}", ephemeral=True)
            return

        case = service.record_case(
            guild_id=ctx.guild.id,
            user_id=user.id,
            moderator_id=ctx.author.id,
            action="unban",
            reason=motivo,
        )
        context = _build_member_context(
            guild=ctx.guild,
            target=user,
            moderator=ctx.author,
            reason=case.reason,
            case_id=case.id,
            warns_count=service.count_active_warns(ctx.guild.id, user.id),
        )
        await _send_member_moderation_response(ctx, service, embeds, "unban", context, user)

    @bot.hybrid_command(name="kick", aliases=["expulsar"], description="Expulsa um membro do servidor.")
    @commands.check(staging_prefix_check("kick_members"))
    @app_commands.default_permissions(kick_members=True)
    @app_commands.describe(membro="Membro a ser expulso.", motivo="Motivo da expulsao.")
    async def kick_command(
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Sem motivo informado",
    ) -> None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return

        error_msg = _check_hierarchy(ctx.guild, ctx.author, membro, bot.user)
        if error_msg:
            await _reply(ctx, error_msg, ephemeral=True)
            return

        await _defer_if_slash(ctx)
        case = service.record_case(
            guild_id=ctx.guild.id,
            user_id=membro.id,
            moderator_id=ctx.author.id,
            action="kick",
            reason=motivo,
        )
        context = _build_member_context(
            guild=ctx.guild,
            target=membro,
            moderator=ctx.author,
            reason=case.reason,
            case_id=case.id,
            warns_count=service.count_active_warns(ctx.guild.id, membro.id),
        )
        await _maybe_dm_member(embeds, "kick", membro, context, ctx.guild)

        try:
            await membro.kick(reason=f"[{ctx.author}] {case.reason}"[:512])
        except discord.Forbidden:
            await _reply(ctx, "Nao tenho permissao suficiente para expulsar esse membro.", ephemeral=True)
            return
        except discord.HTTPException as error:
            await _reply(ctx, f"Nao foi possivel expulsar o membro: {error}", ephemeral=True)
            return

        await _send_member_moderation_response(ctx, service, embeds, "kick", context, membro)

    @bot.hybrid_command(name="mute", aliases=["timeout", "silenciar"], description="Silencia um membro por tempo determinado.")
    @commands.check(staging_prefix_check("moderate_members"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(
        membro="Membro a ser silenciado.",
        duracao="Duracao (ex: 10m, 2h, 7d, 1h30m - maximo 28d).",
        motivo="Motivo do silenciamento.",
    )
    async def mute_command(
        ctx: commands.Context,
        membro: discord.Member,
        duracao: str,
        *,
        motivo: str = "Sem motivo informado",
    ) -> None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return

        error_msg = _check_hierarchy(ctx.guild, ctx.author, membro, bot.user)
        if error_msg:
            await _reply(ctx, error_msg, ephemeral=True)
            return

        try:
            delta = parse_duration(duracao)
        except ValueError as error:
            await _reply(ctx, str(error), ephemeral=True)
            return

        await _defer_if_slash(ctx)
        duration_seconds = int(delta.total_seconds())
        try:
            await membro.timeout(delta, reason=f"[{ctx.author}] {motivo}"[:512])
        except discord.Forbidden:
            await _reply(ctx, "Nao tenho permissao suficiente para silenciar esse membro.", ephemeral=True)
            return
        except discord.HTTPException as error:
            await _reply(ctx, f"Nao foi possivel silenciar o membro: {error}", ephemeral=True)
            return

        case = service.record_case(
            guild_id=ctx.guild.id,
            user_id=membro.id,
            moderator_id=ctx.author.id,
            action="mute",
            reason=motivo,
            duration_seconds=duration_seconds,
        )
        context = _build_member_context(
            guild=ctx.guild,
            target=membro,
            moderator=ctx.author,
            reason=case.reason,
            case_id=case.id,
            duration=format_duration(duration_seconds),
            warns_count=service.count_active_warns(ctx.guild.id, membro.id),
        )
        await _maybe_dm_member(embeds, "mute", membro, context, ctx.guild)
        await _send_member_moderation_response(ctx, service, embeds, "mute", context, membro)

    @bot.hybrid_command(name="unmute", aliases=["untimeout", "dessilenciar"], description="Remove o silenciamento de um membro.")
    @commands.check(staging_prefix_check("moderate_members"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(membro="Membro para remover o silenciamento.", motivo="Motivo da remocao.")
    async def unmute_command(
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Sem motivo informado",
    ) -> None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return

        error_msg = _check_hierarchy(ctx.guild, ctx.author, membro, bot.user)
        if error_msg:
            await _reply(ctx, error_msg, ephemeral=True)
            return

        await _defer_if_slash(ctx)
        try:
            await membro.timeout(None, reason=f"[{ctx.author}] {motivo}"[:512])
        except discord.Forbidden:
            await _reply(ctx, "Nao tenho permissao suficiente para remover o silenciamento.", ephemeral=True)
            return
        except discord.HTTPException as error:
            await _reply(ctx, f"Nao foi possivel remover o silenciamento: {error}", ephemeral=True)
            return

        case = service.record_case(
            guild_id=ctx.guild.id,
            user_id=membro.id,
            moderator_id=ctx.author.id,
            action="unmute",
            reason=motivo,
        )
        context = _build_member_context(
            guild=ctx.guild,
            target=membro,
            moderator=ctx.author,
            reason=case.reason,
            case_id=case.id,
            warns_count=service.count_active_warns(ctx.guild.id, membro.id),
        )
        await _maybe_dm_member(embeds, "unmute", membro, context, ctx.guild)
        await _send_member_moderation_response(ctx, service, embeds, "unmute", context, membro)

    @bot.hybrid_command(name="warn", aliases=["advertir"], description="Aplica uma advertencia persistente a um membro.")
    @commands.check(staging_prefix_check("moderate_members"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(membro="Membro a ser advertido.", motivo="Motivo da advertencia.")
    async def warn_command(
        ctx: commands.Context,
        membro: discord.Member,
        *,
        motivo: str = "Sem motivo informado",
    ) -> None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return

        error_msg = _check_hierarchy(ctx.guild, ctx.author, membro, bot.user)
        if error_msg:
            await _reply(ctx, error_msg, ephemeral=True)
            return

        await _defer_if_slash(ctx)
        case = service.record_case(
            guild_id=ctx.guild.id,
            user_id=membro.id,
            moderator_id=ctx.author.id,
            action="warn",
            reason=motivo,
        )
        warns_count = service.count_active_warns(ctx.guild.id, membro.id)
        context = _build_member_context(
            guild=ctx.guild,
            target=membro,
            moderator=ctx.author,
            reason=case.reason,
            case_id=case.id,
            warns_count=warns_count,
        )
        await _maybe_dm_member(embeds, "warn", membro, context, ctx.guild)
        await _send_member_moderation_response(ctx, service, embeds, "warn", context, membro)

    @bot.hybrid_command(name="historico", aliases=["warns", "warnings", "casos"], description="Mostra o historico de moderacao de um membro.")
    @commands.check(staging_prefix_check("moderate_members"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(membro="Membro para consultar o historico.")
    async def history_command(ctx: commands.Context, membro: discord.User) -> None:
        if not ctx.guild:
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return

        cases = service.list_user_cases(ctx.guild.id, membro.id, limit=12)
        active_warns = service.count_active_warns(ctx.guild.id, membro.id)
        embed = discord.Embed(
            title=f"Historico de {membro.display_name}",
            description=f"Advertencias ativas: **{active_warns}**",
            color=0x5865F2,
            timestamp=utcnow(),
        )
        embed.set_thumbnail(url=membro.display_avatar.replace(format="png", size=128).url)
        if not cases:
            embed.add_field(name="Registros", value="Nenhum caso encontrado neste servidor.", inline=False)
        else:
            for item in cases:
                label = ACTION_LABELS.get(item.action, item.action)
                status_tag = ""
                if item.action == "warn":
                    status_tag = " [ATIVA]" if item.active else " [REMOVIDA]"
                duration_tag = f" ({format_duration(item.duration_seconds)})" if item.duration_seconds else ""
                embed.add_field(
                    name=f"Caso #{item.id} • {label}{duration_tag}{status_tag}",
                    value=f"**Motivo:** {item.reason}\n**Moderador:** <@{item.moderator_id}> • `{item.created_at}`",
                    inline=False,
                )
        await _reply(ctx, embed=embed)

    @bot.hybrid_command(name="removerwarn", aliases=["delwarn"], description="Remove uma advertencia especifica pelo ID do caso.")
    @commands.check(staging_prefix_check("moderate_members"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(caso_id="ID numerico do caso de advertencia.")
    async def remove_warn_command(ctx: commands.Context, caso_id: int) -> None:
        if not ctx.guild:
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        removed = service.deactivate_warn(ctx.guild.id, caso_id)
        if not removed:
            await _reply(ctx, f"Nao encontrei advertencia ativa com o caso `#{caso_id}`.", ephemeral=True)
            return
        await _reply(ctx, f"Advertencia `#{caso_id}` removida com sucesso.")

    @bot.hybrid_command(name="limparwarns", aliases=["clearwarns"], description="Limpa todas as advertencias ativas de um membro.")
    @commands.check(staging_prefix_check("moderate_members"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(membro="Membro que tera as advertencias limpas.")
    async def clear_warns_command(ctx: commands.Context, membro: discord.User) -> None:
        if not ctx.guild:
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        cleared = service.clear_user_warns(ctx.guild.id, membro.id)
        await _reply(ctx, f"**{cleared}** advertencia(s) ativa(s) removida(s) de {membro.mention}.")

    @bot.hybrid_command(name="purge", aliases=["limpar", "apagar"], description="Apaga mensagens recentes do canal.")
    @commands.check(staging_prefix_check("manage_messages"))
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        quantidade="Quantidade de mensagens para apagar (1 a 100).",
        membro="Opcional: apaga apenas mensagens enviadas por este membro.",
    )
    async def clear_command(
        ctx: commands.Context,
        quantidade: int,
        membro: discord.Member | None = None,
    ) -> None:
        if not ctx.guild or not isinstance(ctx.channel, (discord.TextChannel, discord.Thread)):
            await _reply(ctx, "Esse comando funciona em canais de texto do servidor.", ephemeral=True)
            return
        if not (1 <= quantidade <= 100):
            await _reply(ctx, "Informe uma quantidade entre `1` e `100`.", ephemeral=True)
            return

        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=True)
        elif ctx.message:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

        check = (lambda msg: msg.author.id == membro.id) if membro else (lambda msg: True)
        try:
            deleted = await ctx.channel.purge(limit=quantidade, check=check)
        except discord.Forbidden:
            await _reply(ctx, "Nao tenho permissao para apagar mensagens neste canal.", ephemeral=True)
            return
        except discord.HTTPException as error:
            await _reply(ctx, f"Nao foi possivel apagar as mensagens: {error}", ephemeral=True)
            return

        target_text = f" de {membro.mention}" if membro else ""
        embed = discord.Embed(
            title="Limpeza de mensagens",
            description=f"**{len(deleted)}** mensagem(ns){target_text} apagada(s).",
            color=0x3498DB,
            timestamp=utcnow(),
        )
        embed.set_footer(text=f"Moderador: {ctx.author.display_name}")

        if ctx.interaction:
            await ctx.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.channel.send(embed=embed, delete_after=5)

    @bot.hybrid_command(name="slowmode", aliases=["modolento"], description="Define o modo lento de um canal em segundos.")
    @commands.check(staging_prefix_check("manage_channels"))
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(segundos="Intervalo entre mensagens (0 para desativar, max 21600).", canal="Canal alvo (padrao: atual).")
    async def slowmode_command(
        ctx: commands.Context,
        segundos: int,
        canal: discord.TextChannel | None = None,
    ) -> None:
        if not ctx.guild:
            await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
            return
        target = canal or ctx.channel
        if not isinstance(target, discord.TextChannel):
            await _reply(ctx, "Selecione um canal de texto valido.", ephemeral=True)
            return
        if not (0 <= segundos <= 21600):
            await _reply(ctx, "O modo lento deve ficar entre `0` e `21600` segundos (6 horas).", ephemeral=True)
            return

        try:
            await target.edit(slowmode_delay=segundos, reason=f"Slowmode alterado por {ctx.author}")
        except discord.Forbidden:
            await _reply(ctx, "Nao tenho permissao para editar esse canal.", ephemeral=True)
            return

        if segundos == 0:
            await _reply(ctx, f"Modo lento desativado em {target.mention}.")
        else:
            await _reply(ctx, f"Modo lento de {target.mention} definido para **{segundos}s**.")

    @bot.hybrid_command(name="lock", aliases=["trancar"], description="Bloqueia o envio de mensagens para @everyone no canal.")
    @commands.check(staging_prefix_check("manage_channels"))
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(canal="Canal a ser trancado (padrao: atual).", motivo="Motivo do bloqueio.")
    async def lock_command(
        ctx: commands.Context,
        canal: discord.TextChannel | None = None,
        *,
        motivo: str = "Canal trancado pela moderacao",
    ) -> None:
        await _set_channel_lock(ctx, canal, locked=True, motivo=motivo)

    @bot.hybrid_command(name="unlock", aliases=["destrancar"], description="Libera o envio de mensagens para @everyone no canal.")
    @commands.check(staging_prefix_check("manage_channels"))
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(canal="Canal a ser destrancado (padrao: atual).", motivo="Motivo da liberacao.")
    async def unlock_command(
        ctx: commands.Context,
        canal: discord.TextChannel | None = None,
        *,
        motivo: str = "Canal destrancado pela moderacao",
    ) -> None:
        await _set_channel_lock(ctx, canal, locked=False, motivo=motivo)

    modconfig = app_commands.Group(name="modconfig", description="Configuracoes de moderacao do servidor.")

    @modconfig.command(name="canal", description="Define o canal de logs de moderacao.")
    @app_commands.check(staging_app_command_check("manage_guild"))
    @app_commands.describe(canal="Canal que recebera os logs de moderacao.")
    async def modconfig_channel(interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse comando precisa ser usado dentro de um servidor.", ephemeral=True)
            return
        service.set_log_channel_id(interaction.guild.id, canal.id)
        await interaction.response.send_message(f"Canal de logs de moderacao definido para {canal.mention}.", ephemeral=True)

    @modconfig.command(name="status", description="Mostra o canal de logs de moderacao configurado.")
    @app_commands.check(staging_app_command_check("manage_guild"))
    async def modconfig_status(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse comando precisa ser usado dentro de um servidor.", ephemeral=True)
            return
        channel_id = service.get_log_channel_id(interaction.guild.id)
        if channel_id:
            await interaction.response.send_message(f"Canal de logs de moderacao: <#{channel_id}>.", ephemeral=True)
            return
        await interaction.response.send_message("Nenhum canal de logs de moderacao configurado.", ephemeral=True)

    @modconfig.command(name="limpar", description="Remove o canal de logs de moderacao.")
    @app_commands.check(staging_app_command_check("manage_guild"))
    async def modconfig_clear(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse comando precisa ser usado dentro de um servidor.", ephemeral=True)
            return
        service.clear_log_channel_id(interaction.guild.id)
        await interaction.response.send_message("Canal de logs de moderacao removido.", ephemeral=True)

    bot.tree.add_command(modconfig)
    return service, embeds


async def _set_channel_lock(
    ctx: commands.Context,
    canal: discord.TextChannel | None,
    *,
    locked: bool,
    motivo: str,
) -> None:
    if not ctx.guild:
        await _reply(ctx, "Esse comando so pode ser usado dentro de um servidor.", ephemeral=True)
        return
    target = canal or ctx.channel
    if not isinstance(target, discord.TextChannel):
        await _reply(ctx, "Selecione um canal de texto valido.", ephemeral=True)
        return

    overwrite = target.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False if locked else None
    try:
        await target.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"[{ctx.author}] {motivo}"[:512])
    except discord.Forbidden:
        await _reply(ctx, "Nao tenho permissao para alterar permissoes nesse canal.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Canal trancado" if locked else "Canal destrancado",
        description=f"{target.mention} foi {'trancado' if locked else 'destrancado'}.\n**Motivo:** {motivo}",
        color=0xE74C3C if locked else 0x2ECC71,
        timestamp=utcnow(),
    )
    embed.set_footer(text=f"Moderador: {ctx.author.display_name}")
    await _reply(ctx, embed=embed)


def _check_hierarchy(
    guild: discord.Guild,
    actor: discord.Member,
    target: discord.Member,
    bot_user: discord.ClientUser | None,
) -> str | None:
    if target.id == actor.id:
        return "Voce nao pode aplicar essa acao em si mesmo."
    if bot_user and target.id == bot_user.id:
        return "Eu nao posso aplicar essa acao em mim mesma."
    if target.id == guild.owner_id:
        return "Nao e possivel moderar o dono do servidor."
    if actor.id != guild.owner_id and actor.top_role <= target.top_role:
        return "Seu cargo precisa ser superior ao do membro alvo."
    me = guild.me
    if me is not None and me.top_role <= target.top_role:
        return "Meu cargo precisa ser superior ao do membro alvo."
    return None


def _parse_user_id(raw: str) -> int | None:
    cleaned = (raw or "").strip()
    if cleaned.startswith("<@") and cleaned.endswith(">"):
        cleaned = cleaned[2:-1].lstrip("!")
    if cleaned.isdigit():
        return int(cleaned)
    return None


def _build_member_context(
    *,
    guild: discord.Guild,
    target: discord.Member | discord.User,
    moderator: discord.Member,
    reason: str,
    case_id: int,
    duration: str = "",
    warns_count: int = 0,
) -> dict[str, object]:
    return {
        "user": target.display_name,
        "user_mention": target.mention,
        "user_tag": str(target),
        "user_id": target.id,
        "moderator": moderator.display_name,
        "moderator_mention": moderator.mention,
        "reason": reason,
        "duration": duration or "permanente",
        "case_id": case_id,
        "guild": guild.name,
        "warns_count": warns_count,
    }


async def _maybe_dm_member(
    embed_store: ModerationEmbedConfigStore,
    action: str,
    target: discord.Member | discord.User,
    context: dict[str, object],
    guild: discord.Guild,
) -> None:
    config = embed_store.get_action(action)
    if not config.get("dmUser", False) or getattr(target, "bot", False):
        return
    embed, attachment = embed_store.build_embed(
        action,
        context,
        user_avatar_url=target.display_avatar.replace(format="png", size=256).url,
        guild_icon_url=guild.icon.replace(format="png", size=128).url if guild.icon else None,
    )
    try:
        if attachment is not None:
            await target.send(embed=embed, file=attachment)
        else:
            await target.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def _send_member_moderation_response(
    ctx: commands.Context,
    service: ModerationService,
    embed_store: ModerationEmbedConfigStore,
    action: str,
    context: dict[str, object],
    target: discord.Member | discord.User,
) -> None:
    user_avatar = target.display_avatar.replace(format="png", size=256).url
    guild_icon = ctx.guild.icon.replace(format="png", size=128).url if ctx.guild and ctx.guild.icon else None
    config = embed_store.get_action(action)

    if config.get("enabled", True):
        embed, attachment = embed_store.build_embed(
            action,
            context,
            user_avatar_url=user_avatar,
            guild_icon_url=guild_icon,
        )
        if attachment is not None:
            await _reply(ctx, embed=embed, file=attachment)
        else:
            await _reply(ctx, embed=embed)
    else:
        await _reply(ctx, f"Acao `{action}` aplicada em {target.mention} (Caso #{context['case_id']}).")

    if ctx.guild:
        log_channel_id = service.get_log_channel_id(ctx.guild.id)
        if log_channel_id and ctx.channel and log_channel_id != ctx.channel.id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            if isinstance(log_channel, discord.TextChannel):
                log_embed, log_attachment = embed_store.build_embed(
                    action,
                    context,
                    user_avatar_url=user_avatar,
                    guild_icon_url=guild_icon,
                )
                try:
                    if log_attachment is not None:
                        await log_channel.send(embed=log_embed, file=log_attachment)
                    else:
                        await log_channel.send(embed=log_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass


async def _defer_if_slash(ctx: commands.Context) -> None:
    if ctx.interaction and not ctx.interaction.response.is_done():
        await ctx.defer()


async def _reply(ctx: commands.Context, content: str | None = None, *, ephemeral: bool = False, **kwargs) -> None:
    if ctx.interaction:
        if ctx.interaction.response.is_done():
            await ctx.followup.send(content=content, ephemeral=ephemeral, **kwargs)
        else:
            await ctx.interaction.response.send_message(content=content, ephemeral=ephemeral, **kwargs)
        return
    await ctx.send(content=content, **kwargs)
