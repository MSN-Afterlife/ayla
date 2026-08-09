import re
import unicodedata
from io import BytesIO

import aiohttp
import discord
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from PIL import ImageOps
from PIL import UnidentifiedImageError

from bot.services.presence_service import PresenceEntry


WIDTH = 760
HEIGHT = 560
CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):(\d+)>")


async def build_presence_preview(bot_user: discord.ClientUser | discord.User | None, entry: PresenceEntry) -> discord.File:
    image = Image.new("RGB", (WIDTH, HEIGHT), (49, 51, 56))
    draw = ImageDraw.Draw(image, "RGBA")

    _draw_profile_shell(draw, HEIGHT)

    avatar_url = bot_user.display_avatar.replace(format="png", size=256).url if bot_user else None
    avatar = await _remote_image(avatar_url)
    if avatar is None:
        avatar = _fallback_avatar("A")
    avatar = _circle(avatar.resize((138, 138)))
    image.paste(avatar, (58, 118), avatar)

    status_color = _status_color(entry.status)
    draw.ellipse((158, 214, 202, 258), fill=(43, 45, 49))
    draw.ellipse((166, 222, 194, 250), fill=status_color)

    display_name = bot_user.display_name if bot_user else "Ayla"
    username = str(bot_user) if bot_user else "ayla"
    draw.text((58, 282), display_name, fill=(242, 243, 245), font=_font(36, bold=True))
    draw.text((58, 326), username, fill=(181, 186, 193), font=_font(21))

    status_label = _status_label(entry.status)
    draw.rounded_rectangle((58, 365, 260, 401), radius=18, fill=(30, 31, 34))
    draw.ellipse((76, 376, 92, 392), fill=status_color)
    draw.text((104, 371), status_label, fill=(219, 222, 225), font=_font(19, bold=True))

    draw.rounded_rectangle((320, 260, WIDTH - 58, 430), radius=14, fill=(35, 36, 40))
    activity_image = await _activity_image(entry.image_url, entry.image_mode, 112, 112)
    if activity_image:
        image.paste(activity_image, (350, 294), activity_image)
        text_x = 482
    else:
        draw.rounded_rectangle((350, 294, 462, 406), radius=16, fill=(47, 49, 54), outline=(78, 80, 88), width=2)
        question = "?"
        question_font = _font(64, bold=True)
        box = draw.textbbox((0, 0), question, font=question_font)
        draw.text((406 - (box[2] - box[0]) / 2, 350 - (box[3] - box[1]) / 2 - 8), question, fill=(181, 186, 193), font=question_font)
        text_x = 482

    draw.text((text_x, 288), "COMO VAI APARECER", fill=(181, 186, 193), font=_font(15, bold=True))
    label = _activity_label(entry.activity_type)
    draw.text((text_x, 316), label, fill=(242, 243, 245), font=_font(28, bold=True))

    x = text_x
    custom_emoji = entry.emoji or _first_custom_emoji(entry.text)
    if custom_emoji:
        emoji_image = await _custom_emoji_image(custom_emoji)
        if emoji_image:
            emoji_image = emoji_image.resize((34, 34))
            image.paste(emoji_image, (x, 366), emoji_image)
            x += 44
        else:
            draw.text((x, 368), custom_emoji, fill=(219, 222, 225), font=_font(22))
            x += min(160, int(draw.textlength(custom_emoji, font=_font(22))) + 10)

    text = _strip_custom_emoji(entry.text) if entry.emoji else entry.text
    draw.text((x, 368), _fit_text(draw, text or "sem atividade", _font(23), WIDTH - x - 86), fill=(219, 222, 225), font=_font(23))

    draw.rounded_rectangle((58, 462, WIDTH - 58, 505), radius=16, fill=(30, 31, 34))
    draw.text((82, 473), f"Estado: {status_label}   Atividade: {label}", fill=(181, 186, 193), font=_font(18))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="presence-preview.png")


async def build_presence_order_preview(
    bot_user: discord.ClientUser | discord.User | None,
    entries: list[PresenceEntry] | tuple[PresenceEntry, ...],
    active_index: int,
    title: str = "Ordem dos status da Ayla",
) -> discord.File:
    row_height = 96
    height = max(260, 150 + len(entries) * row_height)
    image = Image.new("RGB", (WIDTH, height), (49, 51, 56))
    draw = ImageDraw.Draw(image, "RGBA")

    draw.rounded_rectangle((28, 28, WIDTH - 28, height - 28), radius=26, fill=(43, 45, 49))
    draw.rectangle((28, 28, WIDTH - 28, 116), fill=(88, 101, 242))
    draw.text((58, 56), title, fill=(255, 255, 255), font=_font(30, bold=True))

    avatar_url = bot_user.display_avatar.replace(format="png", size=128).url if bot_user else None
    avatar = await _remote_image(avatar_url)
    if avatar is None:
        avatar = _fallback_avatar("A")
    avatar = _circle(avatar.resize((56, 56)))

    for index, entry in enumerate(entries, start=1):
        y = 138 + (index - 1) * row_height
        active = index - 1 == active_index
        fill = (35, 36, 40) if active else (47, 49, 54)
        outline = (88, 101, 242) if active else (63, 66, 72)
        draw.rounded_rectangle((58, y, WIDTH - 58, y + 76), radius=14, fill=fill, outline=outline, width=2)

        draw.text((82, y + 23), f"{index}", fill=(242, 243, 245), font=_font(26, bold=True))
        image.paste(avatar, (130, y + 10), avatar)
        draw.ellipse((172, y + 50, 192, y + 70), fill=(43, 45, 49))
        draw.ellipse((176, y + 54, 188, y + 66), fill=_status_color(entry.status))

        label = _activity_label(entry.activity_type)
        draw.text((210, y + 13), f"{_status_label(entry.status)} | {label}", fill=(242, 243, 245), font=_font(20, bold=True))

        x = 210
        emoji = entry.emoji or _first_custom_emoji(entry.text)
        if emoji:
            emoji_image = await _custom_emoji_image(emoji)
            if emoji_image:
                emoji_image = emoji_image.resize((24, 24))
                image.paste(emoji_image, (x, y + 43), emoji_image)
                x += 32
            else:
                draw.text((x, y + 42), emoji, fill=(219, 222, 225), font=_font(17))
                x += min(130, int(draw.textlength(emoji, font=_font(17))) + 8)

        text = _strip_custom_emoji(entry.text) if entry.emoji else entry.text
        draw.text((x, y + 42), _fit_text(draw, text or "sem atividade", _font(18), WIDTH - x - 118), fill=(219, 222, 225), font=_font(18))
        if active:
            draw.text((WIDTH - 154, y + 28), "ATIVO", fill=(181, 186, 193), font=_font(16, bold=True))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="presence-order-preview.png")


async def build_status_choice_preview(bot_user: discord.ClientUser | discord.User | None) -> discord.File:
    entries = [
        PresenceEntry(status="online", activity_type="none", text=""),
        PresenceEntry(status="idle", activity_type="none", text=""),
        PresenceEntry(status="dnd", activity_type="none", text=""),
        PresenceEntry(status="invisible", activity_type="none", text=""),
    ]
    height = 470
    image = Image.new("RGB", (WIDTH, height), (49, 51, 56))
    draw = ImageDraw.Draw(image, "RGBA")
    _draw_profile_shell(draw, height)

    avatar_url = bot_user.display_avatar.replace(format="png", size=128).url if bot_user else None
    avatar = await _remote_image(avatar_url)
    if avatar is None:
        avatar = _fallback_avatar("A")
    avatar = _circle(avatar.resize((70, 70)))
    name = bot_user.display_name if bot_user else "Ayla"

    draw.text((58, 224), "Escolha um status padrao", fill=(242, 243, 245), font=_font(30, bold=True))
    draw.text((58, 264), "A bolinha ao lado do avatar mostra como o Discord vai exibir.", fill=(181, 186, 193), font=_font(18))

    for index, entry in enumerate(entries):
        x = 58 + (index % 2) * 330
        y = 318 + (index // 2) * 80
        draw.rounded_rectangle((x, y, x + 292, y + 58), radius=16, fill=(35, 36, 40), outline=(63, 66, 72), width=2)
        image.paste(avatar.resize((42, 42)), (x + 16, y + 8), avatar.resize((42, 42)))
        draw.ellipse((x + 45, y + 38, x + 65, y + 58), fill=(35, 36, 40))
        draw.ellipse((x + 49, y + 42, x + 61, y + 54), fill=_status_color(entry.status))
        draw.text((x + 78, y + 17), name, fill=(242, 243, 245), font=_font(18, bold=True))
        draw.text((x + 190, y + 17), _status_label(entry.status), fill=(181, 186, 193), font=_font(16))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="status-choice-preview.png")


def parse_presence_text(text: str) -> tuple[str, str | None]:
    emoji = _first_presence_emoji(text)
    cleaned = _strip_presence_emoji(text, emoji).strip() if emoji else text.strip()
    return cleaned, emoji


async def _custom_emoji_image(value: str) -> Image.Image | None:
    match = CUSTOM_EMOJI_RE.search(value)
    if not match:
        return None

    animated = value.startswith("<a:")
    extension = "gif" if animated else "png"
    return await _remote_image(f"https://cdn.discordapp.com/emojis/{match.group(2)}.{extension}?size=96&quality=lossless")


async def _remote_image(url: str | None) -> Image.Image | None:
    if not url:
        return None
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0 AylaBot/1.0"}) as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                raw = await response.read()
        image = Image.open(BytesIO(raw))
        image = ImageOps.exif_transpose(image)
        return image.convert("RGBA")
    except (OSError, UnidentifiedImageError, aiohttp.ClientError):
        return None


async def can_render_presence_image(url: str) -> bool:
    return await _remote_image(url) is not None


async def _activity_image(url: str | None, mode: str, width: int, height: int) -> Image.Image | None:
    image = await _remote_image(url)
    if image is None:
        return None
    if mode == "contain":
        fitted = _contain(image, width, height)
    elif mode == "stretch":
        fitted = image.resize((width, height))
    else:
        fitted = _cover(image, width, height)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=16, fill=255)
    fitted = fitted.convert("RGBA")
    fitted.putalpha(mask)
    return fitted


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    image_ratio = image.width / image.height
    target_ratio = width / height
    if image_ratio > target_ratio:
        new_height = height
        new_width = round(height * image_ratio)
    else:
        new_width = width
        new_height = round(width / image_ratio)
    resized = image.resize((new_width, new_height))
    left = max((new_width - width) // 2, 0)
    top = max((new_height - height) // 2, 0)
    return resized.crop((left, top, left + width, top + height))


def _contain(image: Image.Image, width: int, height: int) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (30, 31, 34, 255))
    copy = image.copy()
    copy.thumbnail((width, height))
    canvas.paste(copy, ((width - copy.width) // 2, (height - copy.height) // 2), copy if copy.mode == "RGBA" else None)
    return canvas


def _first_custom_emoji(text: str) -> str | None:
    match = CUSTOM_EMOJI_RE.search(text)
    return match.group(0) if match else None


def _strip_custom_emoji(text: str) -> str:
    return CUSTOM_EMOJI_RE.sub("", text, count=1).strip()


def _first_presence_emoji(text: str) -> str | None:
    custom = _first_custom_emoji(text)
    if custom:
        return custom

    token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if token and _looks_like_unicode_emoji(token):
        return token
    return None


def _strip_presence_emoji(text: str, emoji: str | None) -> str:
    if not emoji:
        return text.strip()
    if CUSTOM_EMOJI_RE.fullmatch(emoji):
        return _strip_custom_emoji(text)
    stripped = text.strip()
    if stripped.startswith(emoji):
        return stripped[len(emoji) :].strip()
    return stripped


def _looks_like_unicode_emoji(value: str) -> bool:
    if len(value) > 12:
        return False
    return any(
        unicodedata.category(character) in {"So", "Sk"} or character in {"\ufe0f", "\u200d"}
        for character in value
    )


def _circle(image: Image.Image) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, image.width, image.height), fill=255)
    image.putalpha(mask)
    return image


def _fallback_avatar(initial: str) -> Image.Image:
    image = Image.new("RGBA", (132, 132), (88, 101, 242))
    draw = ImageDraw.Draw(image)
    font = _font(64, bold=True)
    box = draw.textbbox((0, 0), initial, font=font)
    draw.text(((132 - box[2] + box[0]) / 2, (132 - box[3] + box[1]) / 2 - 5), initial, fill=(255, 255, 255), font=font)
    return image


def _draw_profile_shell(draw: ImageDraw.ImageDraw, height: int) -> None:
    draw.rounded_rectangle((28, 28, WIDTH - 28, height - 28), radius=26, fill=(43, 45, 49))
    for y in range(34, 206):
        ratio = (y - 34) / 172
        r = int(72 + 40 * ratio)
        g = int(84 + 28 * ratio)
        b = int(196 + 38 * ratio)
        draw.line((34, y, WIDTH - 34, y), fill=(r, g, b, 255))
    draw.rounded_rectangle((28, 28, WIDTH - 28, height - 28), radius=26, outline=(30, 31, 34), width=2)
    draw.rectangle((34, 176, WIDTH - 34, 206), fill=(43, 45, 49))


def _status_color(status: str) -> tuple[int, int, int]:
    status = _normalize_status(status)
    return {
        "online": (35, 165, 90),
        "idle": (240, 178, 50),
        "dnd": (242, 63, 66),
        "invisible": (128, 132, 142),
    }.get(status, (35, 165, 90))


def _status_label(status: str) -> str:
    return {
        "online": "Online",
        "idle": "Ausente",
        "dnd": "Nao perturbe",
        "invisible": "Invisivel",
    }.get(_normalize_status(status), status)


def _normalize_status(status: str) -> str:
    return {
        "ausente": "idle",
        "ocupado": "dnd",
        "naoperturbe": "dnd",
        "nao-perturbe": "dnd",
        "invisivel": "invisible",
    }.get(str(status).lower(), str(status).lower())


def _activity_label(activity_type: str) -> str:
    return {
        "custom": "Status personalizado",
        "playing": "Jogando",
        "listening": "Ouvindo",
        "watching": "Assistindo",
        "competing": "Competindo",
        "none": "Sem atividade",
    }.get(activity_type, activity_type)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf"]
        if bold
        else ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf"]
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
