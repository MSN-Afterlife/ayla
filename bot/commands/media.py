from discord.ext import commands

from bot.config import Settings
from bot.services.media_search import MediaSearch
from bot.services.media_search import MediaSearchError


def setup_media_commands(bot: commands.Bot, settings: Settings) -> None:
    media_search = MediaSearch(settings)

    @bot.command(name="gif")
    async def gif(ctx: commands.Context, *, query: str) -> None:
        await send_first_media_result(ctx, media_search.search_gif, query)

    @bot.command(name="imagem", aliases=["image", "img"])
    async def image(ctx: commands.Context, *, query: str) -> None:
        await send_first_media_result(ctx, media_search.search_image, query)

    @bot.command(name="youtube", aliases=["yt", "video"])
    async def youtube(ctx: commands.Context, *, query: str) -> None:
        await send_first_media_result(ctx, media_search.search_youtube, query)


async def send_first_media_result(ctx: commands.Context, search, query: str) -> None:
    try:
        url = await search(query)
    except MediaSearchError as error:
        await ctx.send(str(error))
        return

    await ctx.send(url)
