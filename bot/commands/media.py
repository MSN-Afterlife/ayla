from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.media_search import MediaSearch
from bot.services.media_search import MediaSearchError


def setup_media_commands(bot: commands.Bot, settings: Settings) -> None:
    media_search = MediaSearch(settings)

    @bot.command(name="gif")
    async def gif(ctx: commands.Context, *, query: str) -> None:
        await send_first_media_result(ctx, media_search.search_gif, query)

    @bot.tree.command(name="gif", description="Busca e envia o primeiro GIF encontrado.")
    @app_commands.describe(query="Termo para buscar o GIF.")
    async def gif_slash(interaction, query: str) -> None:
        await send_first_media_result_interaction(interaction, media_search.search_gif, query)

    @bot.command(name="imagem", aliases=["image", "img"])
    async def image(ctx: commands.Context, *, query: str) -> None:
        await send_first_media_result(ctx, media_search.search_image, query)

    @bot.tree.command(name="imagem", description="Busca e envia a primeira imagem encontrada.")
    @app_commands.describe(query="Termo para buscar a imagem.")
    async def image_slash(interaction, query: str) -> None:
        await send_first_media_result_interaction(interaction, media_search.search_image, query)

    @bot.command(name="youtube", aliases=["yt", "video"])
    async def youtube(ctx: commands.Context, *, query: str) -> None:
        await send_first_media_result(ctx, media_search.search_youtube, query)

    @bot.tree.command(name="youtube", description="Busca e envia o primeiro video encontrado no YouTube.")
    @app_commands.describe(query="Termo para buscar o video.")
    async def youtube_slash(interaction, query: str) -> None:
        await send_first_media_result_interaction(interaction, media_search.search_youtube, query)


async def send_first_media_result(ctx: commands.Context, search, query: str) -> None:
    try:
        url = await search(query)
    except MediaSearchError as error:
        await ctx.send(str(error))
        return

    await ctx.send(url)


async def send_first_media_result_interaction(interaction, search, query: str) -> None:
    await interaction.response.defer()
    try:
        url = await search(query)
    except MediaSearchError as error:
        await interaction.followup.send(str(error))
        return

    await interaction.followup.send(url)
