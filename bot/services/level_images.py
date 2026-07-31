from io import BytesIO

import aiohttp
import discord
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from bot.services.level_service import LevelProfile


WIDTH = 980
ROW_HEIGHT = 92


async def build_profile_card(member: discord.Member | discord.User, profile: LevelProfile, scope: str) -> discord.File:
    image = _background(WIDTH, 360)
    draw = ImageDraw.Draw(image)
    font_big = _font(42, bold=True)
    font_medium = _font(28, bold=True)
    font_small = _font(22)

    avatar = await _avatar_image(member.display_avatar.url, 142)
    image.paste(avatar, (52, 92), avatar)

    draw.text((52, 34), scope.upper(), fill=(170, 190, 225), font=font_small)
    draw.text((220, 88), _fit_text(draw, member.display_name, font_big, 520), fill=(250, 252, 255), font=font_big)
    draw.text((222, 138), f"#{profile.rank} no ranking", fill=(135, 225, 170), font=font_medium)

    progress = max(profile.xp - profile.current_level_xp, 0)
    needed = max(profile.next_level_xp - profile.current_level_xp, 1)
    percent = min(progress / needed, 1)

    _stat_box(draw, 220, 202, "LEVEL", str(profile.level))
    _stat_box(draw, 404, 202, "XP TOTAL", str(profile.xp))
    _stat_box(draw, 622, 202, "PROGRESSO", f"{progress}/{needed}")
    _progress(draw, 220, 306, 700, 20, percent)

    return _file(image, "rank-card.png")


async def build_leaderboard_card(title: str, scope: str, profiles: list[LevelProfile], guild: discord.Guild | None) -> discord.File:
    height = 170 + max(len(profiles), 1) * ROW_HEIGHT
    image = _background(WIDTH, height)
    draw = ImageDraw.Draw(image)
    font_title = _font(42, bold=True)
    font_scope = _font(22)
    font_name = _font(26, bold=True)
    font_meta = _font(20)
    font_rank = _font(30, bold=True)

    draw.text((48, 36), title, fill=(250, 252, 255), font=font_title)
    draw.text((50, 88), scope, fill=(170, 190, 225), font=font_scope)

    if not profiles:
        draw.text((50, 170), "Ainda nao tem ninguem nesse ranking.", fill=(235, 238, 245), font=font_name)
        return _file(image, "leaderboard.png")

    for index, profile in enumerate(profiles):
        y = 140 + index * ROW_HEIGHT
        member = guild.get_member(profile.user_id) if guild else None
        name = member.display_name if member else profile.user_name

        accent = (255, 207, 86) if index == 0 else (112, 180, 255) if index == 1 else (148, 232, 180)
        draw.rounded_rectangle((42, y, WIDTH - 42, y + 74), radius=18, fill=(20, 27, 43), outline=(48, 62, 92), width=1)
        draw.rounded_rectangle((42, y, 52, y + 74), radius=5, fill=accent)
        draw.text((72, y + 20), f"#{profile.rank}", fill=accent, font=font_rank)

        avatar_url = member.display_avatar.url if member else None
        avatar = await _avatar_image(avatar_url, 58, fallback=name)
        image.paste(avatar, (150, y + 8), avatar)

        draw.text((226, y + 11), _fit_text(draw, name, font_name, 430), fill=(250, 252, 255), font=font_name)
        draw.text((226, y + 43), f"Level {profile.level}  |  {profile.xp} XP", fill=(170, 190, 225), font=font_meta)

        progress = max(profile.xp - profile.current_level_xp, 0)
        needed = max(profile.next_level_xp - profile.current_level_xp, 1)
        _progress(draw, 690, y + 29, 210, 12, min(progress / needed, 1))

    return _file(image, "leaderboard.png")


def _background(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), (12, 16, 27))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        blue = 27 + int(34 * y / max(height, 1))
        draw.line((0, y, width, y), fill=(12, 16, blue))

    draw.ellipse((width - 280, -160, width + 120, 240), fill=(30, 62, 112))
    draw.ellipse((-140, height - 220, 240, height + 150), fill=(32, 86, 76))
    return image


async def _avatar_image(url: str | None, size: int, fallback: str = "?") -> Image.Image:
    image = None
    if url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=8) as response:
                    if response.status == 200:
                        image = Image.open(BytesIO(await response.read())).convert("RGB")
        except Exception:
            image = None

    if image is None:
        image = Image.new("RGB", (size, size), (56, 73, 108))
        draw = ImageDraw.Draw(image)
        initial = (fallback.strip()[:1] or "?").upper()
        font = _font(max(size // 2, 18), bold=True)
        box = draw.textbbox((0, 0), initial, font=font)
        draw.text(((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - 4), initial, fill=(255, 255, 255), font=font)

    image = image.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    image.putalpha(mask)
    return image


def _stat_box(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str) -> None:
    draw.rounded_rectangle((x, y, x + 160, y + 72), radius=14, fill=(20, 27, 43), outline=(48, 62, 92))
    draw.text((x + 18, y + 12), label, fill=(146, 164, 198), font=_font(16, bold=True))
    draw.text((x + 18, y + 34), value, fill=(250, 252, 255), font=_font(25, bold=True))


def _progress(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, percent: float) -> None:
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=(38, 48, 70))
    fill_width = max(int(width * percent), height)
    draw.rounded_rectangle((x, y, x + fill_width, y + height), radius=height // 2, fill=(92, 220, 156))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["arialbd.ttf", "arial.ttf"] if bold else ["arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text

    trimmed = text
    while trimmed and draw.textlength(f"{trimmed}...", font=font) > max_width:
        trimmed = trimmed[:-1]
    return f"{trimmed}..."


def _file(image: Image.Image, filename: str) -> discord.File:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=filename)
