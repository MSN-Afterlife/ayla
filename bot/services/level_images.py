from io import BytesIO

import aiohttp
import discord
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from bot.services.level_service import LevelProfile
from bot.services.level_service import ProfileCustomization
from bot.services.economy_service import EconomyProfile


WIDTH = 980
PROFILE_HEIGHT = 620
LEVEL_HEIGHT = 420
ROW_HEIGHT = 92


async def build_profile_card(
    member: discord.Member | discord.User,
    profile: LevelProfile,
    scope: str,
    customization: ProfileCustomization | None = None,
    guild: discord.Guild | None = None,
    economy: EconomyProfile | None = None,
) -> discord.File:
    customization = customization or ProfileCustomization()
    image = await _profile_background(WIDTH, PROFILE_HEIGHT, customization.background_url, customization.background_mode)
    draw = ImageDraw.Draw(image)
    font_big = _font(52, bold=True)
    font_medium = _font(30, bold=True)
    font_small = _font(25)
    font_about = _font(25)
    font_tiny = _font(18, bold=True)

    avatar = await _avatar_image(_avatar_url(member, 256), 166, fallback=member.display_name)
    image.paste(avatar, (56, 252), avatar)

    await _draw_guild_badge(image, draw, guild, scope)
    draw.text((250, 256), _fit_text(draw, member.display_name, font_big, 620), fill=(250, 252, 255), font=font_big)
    draw.text((252, 318), f"{scope}", fill=(178, 205, 238), font=font_small)
    draw.text((252, 358), f"Level {profile.level}  |  Rank #{profile.rank}", fill=(135, 225, 170), font=font_medium)

    balance = economy.balance if economy else 0
    streak = economy.daily_streak if economy else 0
    _detail_box(draw, 252, 412, "MOEDAS", str(balance))
    _detail_box(draw, 472, 412, "DAILY", f"{streak} dia(s)")
    _detail_box(draw, 692, 412, "XP", str(profile.xp))

    draw.rounded_rectangle((48, 502, WIDTH - 48, 586), radius=18, fill=(8, 12, 22, 132))
    about = customization.about or "Sem sobre mim ainda."
    draw.text((72, 516), "SOBRE MIM", fill=(170, 190, 225), font=font_tiny)
    draw.text((72, 540), _fit_text(draw, about, font_about, 820), fill=(245, 248, 255), font=font_about)

    return _file(image, "profile-card.png")


async def build_level_card(
    member: discord.Member | discord.User,
    profile: LevelProfile,
    scope: str,
    guild: discord.Guild | None = None,
) -> discord.File:
    image = _background(WIDTH, LEVEL_HEIGHT)
    draw = ImageDraw.Draw(image)
    font_big = _font(48, bold=True)
    font_level = _font(86, bold=True)
    font_medium = _font(30, bold=True)
    font_small = _font(24)

    await _draw_guild_badge(image, draw, guild, scope)
    avatar = await _avatar_image(_avatar_url(member, 256), 150, fallback=member.display_name)
    image.paste(avatar, (58, 132), avatar)

    progress = max(profile.xp - profile.current_level_xp, 0)
    needed = max(profile.next_level_xp - profile.current_level_xp, 1)
    percent = min(progress / needed, 1)

    draw.text((238, 112), _fit_text(draw, member.display_name, font_big, 580), fill=(250, 252, 255), font=font_big)
    draw.text((242, 174), f"Rank #{profile.rank}  |  {profile.xp} XP total", fill=(174, 200, 232), font=font_small)
    draw.text((242, 220), "LEVEL", fill=(146, 164, 198), font=font_medium)
    draw.text((352, 188), str(profile.level), fill=(255, 255, 255), font=font_level)
    draw.text((242, 310), f"Faltam {max(needed - progress, 0)} XP para o level {profile.level + 1}", fill=(235, 240, 248), font=font_small)
    _progress(draw, 242, 352, 650, 28, percent)
    draw.text((242, 386), f"{progress}/{needed} XP", fill=(170, 190, 225), font=_font(20, bold=True))

    return _file(image, "level-card.png")


async def build_leaderboard_card(
    title: str,
    scope: str,
    profiles: list[LevelProfile],
    guild: discord.Guild | None,
    users: dict[int, discord.Member | discord.User] | None = None,
) -> discord.File:
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
        member = users.get(profile.user_id) if users else guild.get_member(profile.user_id) if guild else None
        name = member.display_name if member else profile.user_name

        accent = (255, 207, 86) if index == 0 else (112, 180, 255) if index == 1 else (148, 232, 180)
        draw.rounded_rectangle((42, y, WIDTH - 42, y + 74), radius=18, fill=(20, 27, 43), outline=(48, 62, 92), width=1)
        draw.rounded_rectangle((42, y, 52, y + 74), radius=5, fill=accent)
        draw.text((72, y + 20), f"#{profile.rank}", fill=accent, font=font_rank)

        avatar_url = _avatar_url(member, 128) if member else None
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


async def _profile_background(width: int, height: int, url: str | None, mode: str) -> Image.Image:
    image = await _remote_image(url) if url else None
    if image is None:
        return _background(width, height)

    if mode == "contain":
        image = _contain(image, width, height).convert("RGB")
    elif mode == "stretch":
        image = image.resize((width, height)).convert("RGB")
    else:
        image = _cover(image, width, height).convert("RGB")
    overlay = Image.new("RGBA", (width, height), (8, 12, 22, 70))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle((32, 220, width - 32, height - 24), radius=28, fill=(12, 18, 32, 142))
    return Image.alpha_composite(image.convert("RGBA"), panel).convert("RGB")


async def _draw_guild_badge(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    guild: discord.Guild | None,
    fallback_text: str,
) -> None:
    font = _font(20, bold=True)
    small = _font(15)
    text = guild.name if guild else fallback_text
    x = 52
    y = 28

    icon_url = guild.icon.replace(format="png", size=96).url if guild and guild.icon else None
    icon = await _avatar_image(icon_url, 42, fallback=text)
    image.paste(icon, (x, y), icon)
    draw.text((x + 54, y + 1), _fit_text(draw, text, font, 360), fill=(250, 252, 255), font=font)
    draw.text((x + 54, y + 25), "perfil do servidor", fill=(170, 190, 225), font=small)


async def _remote_image(url: str | None) -> Image.Image | None:
    if not url:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                return Image.open(BytesIO(await response.read())).convert("RGB")
    except Exception:
        return None


async def _avatar_image(url: str | None, size: int, fallback: str = "?") -> Image.Image:
    image = None
    if url:
        image = await _remote_image(url)

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


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    source_width, source_height = image.size
    scale = max(width / source_width, height / source_height)
    resized = image.resize((int(source_width * scale), int(source_height * scale)))
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _contain(image: Image.Image, width: int, height: int) -> Image.Image:
    source_width, source_height = image.size
    scale = min(width / source_width, height / source_height)
    resized = image.resize((int(source_width * scale), int(source_height * scale)))
    canvas = Image.new("RGB", (width, height), (12, 16, 27))
    left = (width - resized.width) // 2
    top = (height - resized.height) // 2
    canvas.paste(resized, (left, top))
    return canvas


def _avatar_url(member: discord.Member | discord.User, size: int) -> str:
    return member.display_avatar.replace(format="png", size=size).url


def _stat_box(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str) -> None:
    draw.rounded_rectangle((x, y, x + 160, y + 72), radius=14, fill=(20, 27, 43), outline=(48, 62, 92))
    draw.text((x + 18, y + 12), label, fill=(146, 164, 198), font=_font(16, bold=True))
    draw.text((x + 18, y + 34), value, fill=(250, 252, 255), font=_font(25, bold=True))


def _detail_box(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str) -> None:
    draw.rounded_rectangle((x, y, x + 190, y + 62), radius=14, fill=(18, 25, 40, 172), outline=(70, 90, 125))
    draw.text((x + 16, y + 10), label, fill=(146, 164, 198), font=_font(15, bold=True))
    draw.text((x + 16, y + 30), _fit_text(draw, value, _font(24, bold=True), 150), fill=(250, 252, 255), font=_font(24, bold=True))


def _progress(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, percent: float) -> None:
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=(38, 48, 70))
    fill_width = max(int(width * percent), height)
    draw.rounded_rectangle((x, y, x + fill_width, y + height), radius=height // 2, fill=(92, 220, 156))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "DejaVuSans-Bold.ttf",
            "Arial Bold.ttf",
            "arialbd.ttf",
            "arial.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "DejaVuSans.ttf",
            "Arial.ttf",
            "arial.ttf",
        ]
    )
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
