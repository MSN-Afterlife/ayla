import discord
from discord.ext import commands

from bot.config import Settings
from bot.services.affection_service import AffectionService


def setup_affection_commands(bot: commands.Bot, settings: Settings) -> None:
    affection_service = AffectionService(settings)

    @bot.hybrid_command(name="afeto", aliases=["relacao"], description="Mostra como a Ayla esta se dando com alguem.")
    async def affection(ctx: commands.Context, membro: discord.Member | None = None) -> None:
        target = membro or ctx.author
        profile = affection_service.get_profile(
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=target.id,
            user_name=target.display_name,
        )

        embed = discord.Embed(
            title=f"Relacao da Ayla com {target.display_name}",
            description=_relationship_text(profile.affection),
            color=_relationship_color(profile.affection),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Afeto", value=f"`{profile.affection}/100`", inline=True)
        embed.add_field(name="Respeito", value=f"`{profile.respect}/100`", inline=True)
        embed.add_field(name="Confianca", value=f"`{profile.trust}/100`", inline=True)
        embed.add_field(name="Suspeita", value=f"`{profile.suspicion}/100`", inline=True)
        embed.add_field(name="Caos", value=f"`{profile.chaos}/100`", inline=True)
        embed.add_field(name="Deboche", value=f"`{profile.sass}/100`", inline=True)
        embed.add_field(name="Humor", value=profile.mood_label.capitalize(), inline=False)
        embed.add_field(name="Boas interacoes", value=str(profile.positive_hits), inline=True)
        embed.add_field(name="Provocacoes", value=str(profile.negative_hits), inline=True)
        if profile.last_event:
            embed.add_field(name="Ultima impressao", value=profile.last_event.capitalize(), inline=False)
        if profile.favorite_color:
            embed.add_field(name="Cor lembrada", value=profile.favorite_color.capitalize(), inline=True)
        if profile.memories:
            embed.add_field(name="Memorias recentes", value="\n".join(profile.memories), inline=False)

        await ctx.reply(embed=embed, mention_author=False)

    @bot.hybrid_command(name="afetolimpar", aliases=["resetafeto"], description="Reseta a memoria afetiva da Ayla sobre alguem.")
    @commands.has_permissions(manage_guild=True)
    async def reset_affection(ctx: commands.Context, membro: discord.Member) -> None:
        affection_service.reset_profile(
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=membro.id,
            user_name=membro.display_name,
        )
        await ctx.reply(f"Memoria afetiva sobre {membro.mention} resetada.", mention_author=False)

    @bot.hybrid_command(name="afetoajustar", aliases=["setafeto"], description="Ajusta o afeto/respeito da Ayla por alguem.")
    @commands.has_permissions(manage_guild=True)
    async def set_affection(ctx: commands.Context, membro: discord.Member, afeto: int, respeito: int = 0) -> None:
        profile = affection_service.set_profile(
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=membro.id,
            user_name=membro.display_name,
            affection=afeto,
            respect=respeito,
        )
        await ctx.reply(
            f"Ayla agora esta com afeto `{profile.affection}/100` e respeito `{profile.respect}/100` por {membro.mention}.",
            mention_author=False,
        )


def _relationship_text(affection: int) -> str:
    if affection >= 45:
        return "Ela claramente tem um carinho especial por essa pessoa."
    if affection >= 18:
        return "Ela trata essa pessoa melhor que a media, e provavelmente vai admitir isso fingindo que nao."
    if affection <= -45:
        return "Ela esta bem de saco cheio dessa pessoa."
    if affection <= -18:
        return "Ela lembra das provocacoes e responde com menos paciencia."
    return "Relacao neutra por enquanto."


def _relationship_color(affection: int) -> int:
    if affection >= 18:
        return 0x3BA7FF
    if affection <= -18:
        return 0xE04F5F
    return 0x7CDBD5
