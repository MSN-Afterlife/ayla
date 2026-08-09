import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.presence_service import PresenceConfig
from bot.services.presence_service import PresenceConfigStore
from bot.services.presence_service import PresenceEntry
from bot.services.presence_service import build_presence
from bot.services.presence_service import format_entry
from bot.services.presence_preview import build_presence_preview
from bot.services.presence_preview import build_presence_order_preview
from bot.services.presence_preview import parse_presence_text


def setup_presence_commands(bot: commands.Bot, settings: Settings) -> None:
    store = PresenceConfigStore(settings.presence_config_path)
    bot._presence_store = store
    bot._presence_rotation_index = 0
    slash_group = app_commands.Group(name="statusayla", description="Configura a presenca/status da Ayla.")

    @commands.group(name="statusayla", aliases=["presenca", "presence", "aylapresence"], invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def presence_group(ctx: commands.Context) -> None:
        await ctx.send(embed=_build_presence_embed(store.get()))

    @presence_group.command(name="listar", aliases=["list", "status"])
    @commands.has_permissions(administrator=True)
    async def list_presence(ctx: commands.Context) -> None:
        await ctx.send(embed=_build_presence_embed(store.get()))

    @presence_group.command(name="modo", aliases=["mode"])
    @commands.has_permissions(administrator=True)
    async def set_mode(ctx: commands.Context, mode: str) -> None:
        try:
            config = store.set_mode(mode)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await apply_presence(bot)
        await ctx.send(f"Modo atualizado para `{config.mode}`.")

    @presence_group.command(name="intervalo", aliases=["tempo", "interval"])
    @commands.has_permissions(administrator=True)
    async def set_interval(ctx: commands.Context, seconds: int) -> None:
        try:
            config = store.set_interval(seconds)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Intervalo de rotacao atualizado para `{config.interval_seconds}` segundos.")

    @presence_group.command(name="usar", aliases=["ativo", "use"])
    @commands.has_permissions(administrator=True)
    async def set_active(ctx: commands.Context, index: int) -> None:
        await _send_use_preview(ctx, bot, store, index)

    @presence_group.command(name="confirmaruso", aliases=["useagora"])
    @commands.has_permissions(administrator=True)
    async def confirm_active_legacy(ctx: commands.Context, index: int) -> None:
        try:
            config = store.set_active(index)
        except ValueError as error:
            await ctx.send(str(error))
            return
        bot._presence_rotation_index = config.active_index
        await apply_presence(bot)
        await ctx.send(f"Status ativo atualizado para `{index}`.")

    @presence_group.command(name="add", aliases=["adicionar"])
    @commands.has_permissions(administrator=True)
    async def add_presence(ctx: commands.Context, status: str, activity_type: str, *, text: str = "") -> None:
        try:
            cleaned_text, emoji = parse_presence_text(text)
            entry = PresenceEntry(status=status, activity_type=activity_type, text=cleaned_text, emoji=emoji)
            build_presence(entry)
        except ValueError as error:
            await ctx.send(str(error))
            return
        file = await build_presence_preview(bot.user, entry)
        await ctx.send(
            embed=_build_confirmation_embed("Confirmar novo status", entry),
            file=file,
            view=PresenceConfirmView(bot, store, "add", entry=entry),
        )

    @presence_group.command(name="set", aliases=["editar"])
    @commands.has_permissions(administrator=True)
    async def update_presence(ctx: commands.Context, index: int, status: str, activity_type: str, *, text: str = "") -> None:
        try:
            cleaned_text, emoji = parse_presence_text(text)
            entry = PresenceEntry(status=status, activity_type=activity_type, text=cleaned_text, emoji=emoji)
            build_presence(entry)
        except ValueError as error:
            await ctx.send(str(error))
            return
        file = await build_presence_preview(bot.user, entry)
        await ctx.send(
            embed=_build_confirmation_embed("Confirmar edicao de status", entry, extra=f"Editar item `{index}`."),
            file=file,
            view=PresenceConfirmView(bot, store, "set", entry=entry, index=index),
        )

    @presence_group.command(name="remover", aliases=["remove", "del", "apagar"])
    @commands.has_permissions(administrator=True)
    async def remove_presence(ctx: commands.Context, index: int) -> None:
        try:
            config = store.remove_entry(index)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await apply_presence(bot)
        await ctx.send(embed=_build_presence_embed(config, title="Status removido"))

    @presence_group.command(name="mover", aliases=["move"])
    @commands.has_permissions(administrator=True)
    async def move_presence(ctx: commands.Context, source: int, target: int) -> None:
        await _send_move_preview(ctx, bot, store, source, target)

    @presence_group.command(name="ordem", aliases=["reordenar", "order"])
    @commands.has_permissions(administrator=True)
    async def reorder_presence(ctx: commands.Context, *order: int) -> None:
        config = store.get()
        try:
            entries = _entries_in_order(config, list(order))
            active_entry = config.entries[config.active_index]
            active_index = entries.index(active_entry)
        except ValueError as error:
            await ctx.send(str(error))
            return
        file = await build_presence_order_preview(bot.user, entries, active_index, "Previa da ordem em lote")
        await ctx.send(
            embed=_build_order_confirmation_embed("Confirmar ordem em lote", entries, f"Nova ordem: `{' '.join(str(item) for item in order)}`."),
            file=file,
            view=PresenceConfirmView(bot, store, "reorder", order=list(order)),
        )

    @presence_group.command(name="confirmarmover", aliases=["moveagora"])
    @commands.has_permissions(administrator=True)
    async def confirm_move_legacy(ctx: commands.Context, source: int, target: int) -> None:
        try:
            config = store.move_entry(source, target)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await apply_presence(bot)
        await ctx.send(embed=_build_presence_embed(config, title="Ordem atualizada"))

    @presence_group.command(name="limpar", aliases=["clear"])
    @commands.has_permissions(administrator=True)
    async def clear_presence(ctx: commands.Context) -> None:
        config = store.clear()
        await apply_presence(bot)
        await ctx.send(embed=_build_presence_embed(config, title="Presenca limpa"))

    @presence_group.command(name="aplicar", aliases=["apply"])
    @commands.has_permissions(administrator=True)
    async def apply_presence_command(ctx: commands.Context) -> None:
        await apply_presence(bot)
        await ctx.send("Presenca aplicada.")

    bot.add_command(presence_group)

    @slash_group.command(name="painel", description="Abre o painel visual para configurar o status da Ayla.")
    @app_commands.default_permissions(administrator=True)
    async def presence_panel(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=_build_presence_embed(store.get(), title="Painel de presenca da Ayla"),
            view=PresencePanelView(bot, store),
            ephemeral=True,
        )

    @slash_group.command(name="listar", description="Lista os status salvos da Ayla.")
    @app_commands.default_permissions(administrator=True)
    async def presence_list_slash(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_build_presence_embed(store.get()), ephemeral=True)

    @slash_group.command(name="modo", description="Define se a Ayla usa um status fixo ou alterna entre os salvos.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(modo=[app_commands.Choice(name="single", value="single"), app_commands.Choice(name="rotate", value="rotate")])
    async def presence_mode_slash(interaction: discord.Interaction, modo: app_commands.Choice[str]) -> None:
        config = store.set_mode(modo.value)
        await apply_presence(bot)
        await interaction.response.send_message(embed=_build_presence_embed(config, title="Modo atualizado"), ephemeral=True)

    @slash_group.command(name="intervalo", description="Define o intervalo da rotacao em segundos.")
    @app_commands.default_permissions(administrator=True)
    async def presence_interval_slash(interaction: discord.Interaction, segundos: int) -> None:
        try:
            config = store.set_interval(segundos)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.send_message(embed=_build_presence_embed(config, title="Intervalo atualizado"), ephemeral=True)

    @slash_group.command(name="adicionar", description="Mostra previa e confirma um novo status.")
    @app_commands.default_permissions(administrator=True)
    async def presence_add_slash(interaction: discord.Interaction, status: str, tipo: str, texto: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await _send_entry_preview(interaction, bot, store, "add", status, tipo, texto)

    @slash_group.command(name="editar", description="Mostra previa e confirma a edicao de um status salvo.")
    @app_commands.default_permissions(administrator=True)
    async def presence_edit_slash(interaction: discord.Interaction, indice: int, status: str, tipo: str, texto: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await _send_entry_preview(interaction, bot, store, "set", status, tipo, texto, index=indice)

    @slash_group.command(name="usar", description="Mostra uma previa e confirma o status ativo.")
    @app_commands.default_permissions(administrator=True)
    async def presence_use_slash(interaction: discord.Interaction, indice: int) -> None:
        await interaction.response.defer(ephemeral=True)
        await _send_use_preview(interaction, bot, store, indice)

    @slash_group.command(name="mover", description="Mostra uma previa e confirma uma mudanca de ordem.")
    @app_commands.default_permissions(administrator=True)
    async def presence_move_slash(interaction: discord.Interaction, origem: int, destino: int) -> None:
        await interaction.response.defer(ephemeral=True)
        await _send_move_preview(interaction, bot, store, origem, destino)

    @slash_group.command(name="ordem", description="Mostra uma previa e confirma uma nova ordem em lote. Ex: 3 1 2")
    @app_commands.default_permissions(administrator=True)
    async def presence_order_slash(interaction: discord.Interaction, ordem: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            parsed_order = [int(part) for part in ordem.replace(",", " ").split()]
        except ValueError:
            await interaction.followup.send("Ordem invalida. Use numeros separados por espaco.", ephemeral=True)
            return
        await _send_order_preview(interaction, bot, store, parsed_order)

    @slash_group.command(name="remover", description="Remove um status salvo.")
    @app_commands.default_permissions(administrator=True)
    async def presence_remove_slash(interaction: discord.Interaction, indice: int) -> None:
        try:
            config = store.remove_entry(indice)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await apply_presence(bot)
        await interaction.response.send_message(embed=_build_presence_embed(config, title="Status removido"), ephemeral=True)

    @slash_group.command(name="limpar", description="Limpa os status salvos da Ayla.")
    @app_commands.default_permissions(administrator=True)
    async def presence_clear_slash(interaction: discord.Interaction) -> None:
        config = store.clear()
        await apply_presence(bot)
        await interaction.response.send_message(embed=_build_presence_embed(config, title="Presenca limpa"), ephemeral=True)

    bot.tree.add_command(slash_group)


async def start_presence_rotation(bot: commands.Bot) -> None:
    if getattr(bot, "_presence_task_started", False):
        return

    bot._presence_task_started = True
    await bot.wait_until_ready()
    await apply_presence(bot)

    while not bot.is_closed():
        store = getattr(bot, "_presence_store", None)
        if not store:
            await asyncio.sleep(60)
            continue

        config = store.get()
        await asyncio.sleep(config.interval_seconds)
        if config.mode != "rotate" or len(config.entries) <= 1:
            continue

        bot._presence_rotation_index = (getattr(bot, "_presence_rotation_index", config.active_index) + 1) % len(config.entries)
        await apply_presence(bot)


async def apply_presence(bot: commands.Bot) -> None:
    store: PresenceConfigStore | None = getattr(bot, "_presence_store", None)
    if not store:
        return

    config = store.get()
    if config.mode == "rotate":
        index = getattr(bot, "_presence_rotation_index", config.active_index) % len(config.entries)
    else:
        index = config.active_index
        bot._presence_rotation_index = index

    entry = config.entries[index]
    status, activity = build_presence(entry)
    await bot.change_presence(status=status, activity=activity)


def _build_presence_embed(config: PresenceConfig, *, title: str = "Presenca da Ayla") -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description="Configuracao persistente do status e atividade da Ayla.",
        color=0x5865F2,
    )
    embed.add_field(name="Modo", value=f"`{config.mode}`", inline=True)
    embed.add_field(name="Intervalo", value=f"`{config.interval_seconds}s`", inline=True)
    embed.add_field(name="Ativo", value=f"`{config.active_index + 1}`", inline=True)

    entries = [format_entry(index, entry, active=index == config.active_index + 1) for index, entry in enumerate(config.entries, start=1)]
    embed.add_field(name="Ordem", value="\n".join(entries)[:1024], inline=False)
    embed.add_field(
        name="Exemplos",
        value=(
            "`a!statusayla add online custom faz sol hoje | a!help`\n"
            "`a!statusayla add dnd jogando sua mae na cama`\n"
            "`a!statusayla modo rotate`\n"
            "`a!statusayla intervalo 300`\n"
            "`a!statusayla mover 3 1`"
        ),
        inline=False,
    )
    return embed


def _build_confirmation_embed(title: str, entry: PresenceEntry, *, extra: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=extra or "Confira a imagem antes de salvar a alteracao.",
        color=0x5865F2,
    )
    embed.add_field(name="Status", value=f"`{entry.status}`", inline=True)
    embed.add_field(name="Tipo", value=f"`{entry.activity_type}`", inline=True)
    embed.add_field(name="Emoji", value=entry.emoji or "`nenhum`", inline=True)
    embed.add_field(name="Texto", value=entry.text or "`sem atividade`", inline=False)
    embed.set_image(url="attachment://presence-preview.png")
    return embed


def _build_order_confirmation_embed(title: str, entries: list[PresenceEntry], description: str) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"{description}\nConfira a imagem antes de salvar a nova ordem.",
        color=0x5865F2,
    )
    lines = [f"`{index}` {entry.status} | {entry.activity_type} | {entry.emoji or ''} {entry.text or 'sem atividade'}".strip() for index, entry in enumerate(entries, start=1)]
    embed.add_field(name="Nova ordem", value="\n".join(lines)[:1024], inline=False)
    embed.set_image(url="attachment://presence-order-preview.png")
    return embed


def _entries_in_order(config: PresenceConfig, order: list[int]) -> list[PresenceEntry]:
    expected = list(range(1, len(config.entries) + 1))
    if sorted(order) != expected:
        raise ValueError(f"Ordem invalida. Use todos os indices uma vez: {' '.join(str(item) for item in expected)}.")
    return [config.entries[index - 1] for index in order]


async def _send_use_preview(target, bot: commands.Bot, store: PresenceConfigStore, index: int) -> None:
    config = store.get()
    if index < 1 or index > len(config.entries):
        await _send_response(target, "Indice invalido.", ephemeral=True)
        return
    entry = config.entries[index - 1]
    file = await build_presence_preview(bot.user, entry)
    await _send_response(
        target,
        embed=_build_confirmation_embed("Confirmar status ativo", entry, extra=f"Usar item `{index}` como status ativo."),
        file=file,
        view=PresenceConfirmView(bot, store, "use", index=index),
        ephemeral=True,
    )


async def _send_move_preview(target, bot: commands.Bot, store: PresenceConfigStore, source: int, target_index: int) -> None:
    try:
        config = store.get()
        entries = list(config.entries)
        if source < 1 or source > len(entries) or target_index < 1 or target_index > len(entries):
            raise ValueError("Indice invalido.")
        entry = entries.pop(source - 1)
        entries.insert(target_index - 1, entry)
        active_entry = config.entries[config.active_index]
        active_index = entries.index(active_entry)
    except ValueError as error:
        await _send_response(target, str(error), ephemeral=True)
        return
    file = await build_presence_order_preview(bot.user, entries, active_index, "Previa da nova ordem")
    await _send_response(
        target,
        embed=_build_order_confirmation_embed("Confirmar movimento", entries, f"Mover `{source}` para `{target_index}`."),
        file=file,
        view=PresenceConfirmView(bot, store, "move", source=source, target=target_index),
        ephemeral=True,
    )


async def _send_order_preview(target, bot: commands.Bot, store: PresenceConfigStore, order: list[int]) -> None:
    config = store.get()
    try:
        entries = _entries_in_order(config, order)
        active_entry = config.entries[config.active_index]
        active_index = entries.index(active_entry)
    except ValueError as error:
        await _send_response(target, str(error), ephemeral=True)
        return
    file = await build_presence_order_preview(bot.user, entries, active_index, "Previa da ordem em lote")
    await _send_response(
        target,
        embed=_build_order_confirmation_embed("Confirmar ordem em lote", entries, f"Nova ordem: `{' '.join(str(item) for item in order)}`."),
        file=file,
        view=PresenceConfirmView(bot, store, "reorder", order=order),
        ephemeral=True,
    )


async def _send_entry_preview(
    target,
    bot: commands.Bot,
    store: PresenceConfigStore,
    action: str,
    status: str,
    activity_type: str,
    text: str,
    *,
    index: int | None = None,
) -> None:
    try:
        cleaned_text, emoji = parse_presence_text(text)
        entry = PresenceEntry(status=status, activity_type=activity_type, text=cleaned_text, emoji=emoji)
        build_presence(entry)
    except ValueError as error:
        await _send_response(target, str(error), ephemeral=True)
        return
    file = await build_presence_preview(bot.user, entry)
    await _send_response(
        target,
        embed=_build_confirmation_embed("Confirmar status", entry, extra=f"Editar item `{index}`." if index else None),
        file=file,
        view=PresenceConfirmView(bot, store, action, entry=entry, index=index),
        ephemeral=True,
    )


async def _send_response(target, *args, ephemeral: bool = False, **kwargs) -> None:
    if isinstance(target, discord.Interaction):
        if target.response.is_done():
            await target.followup.send(*args, ephemeral=ephemeral, **kwargs)
        else:
            await target.response.send_message(*args, ephemeral=ephemeral, **kwargs)
        return
    await target.send(*args, **kwargs)


class PresencePanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, store: PresenceConfigStore) -> None:
        super().__init__(timeout=300)
        self._bot = bot
        self._store = store

    @discord.ui.button(label="Adicionar", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PresenceEntryModal(self._bot, self._store, "add"))

    @discord.ui.button(label="Editar", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PresenceEntryModal(self._bot, self._store, "set"))

    @discord.ui.button(label="Usar", style=discord.ButtonStyle.primary)
    async def use(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PresenceIndexModal(self._bot, self._store, "use"))

    @discord.ui.button(label="Ordenar", style=discord.ButtonStyle.secondary)
    async def order(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PresenceOrderModal(self._bot, self._store))

    @discord.ui.button(label="Modo", style=discord.ButtonStyle.secondary)
    async def mode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        config = self._store.get()
        updated = self._store.set_mode("single" if config.mode == "rotate" else "rotate")
        await apply_presence(self._bot)
        await interaction.response.edit_message(embed=_build_presence_embed(updated, title="Modo alternado"), view=self)

    @discord.ui.button(label="Atualizar", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=_build_presence_embed(self._store.get(), title="Painel de presenca da Ayla"), view=self)


class PresenceEntryModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, store: PresenceConfigStore, action: str) -> None:
        super().__init__(title="Status da Ayla")
        self._bot = bot
        self._store = store
        self._action = action
        self.index = discord.ui.TextInput(label="Indice para editar", required=action == "set", placeholder="Ex: 1")
        self.status = discord.ui.TextInput(label="Status", default="online", placeholder="online, idle, dnd, invisible", max_length=20)
        self.kind = discord.ui.TextInput(label="Tipo", default="custom", placeholder="custom, jogando, ouvindo, assistindo...", max_length=20)
        self.text = discord.ui.TextInput(label="Texto / emoji", style=discord.TextStyle.paragraph, placeholder="Ex: <:emoji:123> faz sol hoje | a!help", max_length=120)
        if action == "set":
            self.add_item(self.index)
        self.add_item(self.status)
        self.add_item(self.kind)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            index = int(str(self.index.value)) if self._action == "set" else None
        except ValueError:
            await interaction.followup.send("Indice invalido.", ephemeral=True)
            return
        await _send_entry_preview(interaction, self._bot, self._store, self._action, str(self.status.value), str(self.kind.value), str(self.text.value), index=index)


class PresenceIndexModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, store: PresenceConfigStore, action: str) -> None:
        super().__init__(title="Escolher status ativo")
        self._bot = bot
        self._store = store
        self.index = discord.ui.TextInput(label="Indice", placeholder="Ex: 1", max_length=4)
        self.add_item(self.index)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            index = int(str(self.index.value))
        except ValueError:
            await interaction.followup.send("Indice invalido.", ephemeral=True)
            return
        await _send_use_preview(interaction, self._bot, self._store, index)


class PresenceOrderModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, store: PresenceConfigStore) -> None:
        super().__init__(title="Ordenar status da Ayla")
        self._bot = bot
        self._store = store
        self.order = discord.ui.TextInput(label="Nova ordem", placeholder="Ex: 3 1 2", max_length=80)
        self.add_item(self.order)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            order = [int(part) for part in str(self.order.value).replace(",", " ").split()]
        except ValueError:
            await interaction.followup.send("Ordem invalida. Use numeros separados por espaco.", ephemeral=True)
            return
        await _send_order_preview(interaction, self._bot, self._store, order)


class PresenceConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        store: PresenceConfigStore,
        action: str,
        *,
        entry: PresenceEntry | None = None,
        index: int | None = None,
        source: int | None = None,
        target: int | None = None,
        order: list[int] | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._bot = bot
        self._store = store
        self._action = action
        self._entry = entry
        self._index = index
        self._source = source
        self._target = target
        self._order = order

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message("So admins podem confirmar essa alteracao.", ephemeral=True)
            return

        try:
            if self._action == "add" and self._entry:
                config = self._store.add_entry(self._entry.status, self._entry.activity_type, self._entry.text, self._entry.emoji)
                title = "Status adicionado"
            elif self._action == "set" and self._entry and self._index:
                config = self._store.update_entry(self._index, self._entry.status, self._entry.activity_type, self._entry.text, self._entry.emoji)
                title = "Status editado"
            elif self._action == "use" and self._index:
                config = self._store.set_active(self._index)
                self._bot._presence_rotation_index = config.active_index
                title = "Status ativo atualizado"
            elif self._action == "move" and self._source and self._target:
                config = self._store.move_entry(self._source, self._target)
                title = "Ordem atualizada"
            elif self._action == "reorder" and self._order:
                config = self._store.reorder_entries(self._order)
                title = "Ordem atualizada"
            else:
                await interaction.response.send_message("Confirmacao invalida.", ephemeral=True)
                return
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        await apply_presence(self._bot)
        await interaction.response.edit_message(embed=_build_presence_embed(config, title=title), attachments=[], view=None)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not _is_admin(interaction):
            await interaction.response.send_message("So admins podem cancelar essa alteracao.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Alteracao cancelada.", embed=None, attachments=[], view=None)


def _is_admin(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)
