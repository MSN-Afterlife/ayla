from __future__ import annotations

from io import BytesIO
from pathlib import Path

import aiohttp
import discord
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont


CARD_WIDTH = 280
CARD_HEIGHT = 420
HAND_CARD_GAP = 18
HAND_CARDS_PER_ROW = 6

CARD_IMAGE_BASE_URL = "https://raw.githubusercontent.com/john-costanzo/uno-card-images/master"

IMAGE_COLOR_NAMES = {
    "vermelho": "Red",
    "azul": "Blue",
    "verde": "Green",
    "amarelo": "Yellow",
}

IMAGE_VALUE_NAMES = {
    "bloqueio": "Skip",
    "reverso": "Reverse",
    "+2": "Draw_2",
}

COLOR_RGB = {
    "vermelho": (235, 77, 61),
    "azul": (52, 152, 219),
    "verde": (46, 204, 113),
    "amarelo": (241, 196, 15),
}

CHARACTER_NAMES = {
    "godzilla": "Godzilla",
    "kraken": "Kraken",
    "dragao": "Dragao",
    "mothra": "Mothra",
    "minotauro": "Minotauro",
    "medusa": "Medusa",
    "yeti": "Yeti",
    "fenix": "Fenix",
    "cthulhu": "Cthulhu",
    "king_kong": "King Kong",
    "slime": "Slime Mutante",
    "robo_caos": "Robo Caos",
    "ayla_caotica": "Ayla Caotica",
}
CHARACTER_COLORS = [
    (214, 64, 69),
    (44, 116, 178),
    (117, 72, 168),
    (30, 148, 113),
    (208, 121, 39),
]
ACTION_STYLES = {
    "attack": ((205, 58, 67), "ATAQUE", "!"),
    "benefit": ((38, 170, 111), "BONUS", "+"),
    "transform": ((139, 82, 206), "MUTACAO", "*"),
    "control": ((224, 139, 45), "CONTROLE", "#"),
    "chaos": ((43, 133, 196), "CAOS", "?"),
}
ACTION_GROUPS = {
    "attack": {"rampage", "roar", "tentacles", "drown", "fire", "burn", "smash", "charge", "avalanche", "overload", "tide", "blizzard", "snowball", "flames", "steal", "hoard", "throw", "prank", "dust", "swarm", "snakes", "whispers", "madness", "curse", "melt", "absorb"},
    "benefit": {"tailwind", "flight", "flutter", "bounce", "spark", "sun", "gift", "blessing", "heal", "protect", "repair", "favor", "scales", "guard", "rage", "rebirth", "ashes", "dream", "clone", "split"},
    "transform": {"atomic", "laser", "gaze", "void", "axe"},
    "control": {"stomp", "petrify", "freeze", "hack", "labyrinth", "mirror", "shuffle", "glitch", "roulette"},
}


class UnoImageBuilder:
    def __init__(self) -> None:
        self._cache_dir = Path("data") / "uno_cards"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def build_hand_image(self, cards: list[object], *, current_color: str | None, top_card: object) -> discord.File:
        images = [await self._card_image(card, current_color=current_color) for card in cards]
        top_image = await self._card_image(top_card, current_color=current_color)

        shown_images = images
        count = len(shown_images)
        rows = max(1, (count + HAND_CARDS_PER_ROW - 1) // HAND_CARDS_PER_ROW)
        hand_width = HAND_CARDS_PER_ROW * CARD_WIDTH + (HAND_CARDS_PER_ROW - 1) * HAND_CARD_GAP
        width = 340 + hand_width + 40
        height = 100 + rows * CARD_HEIGHT + max(rows - 1, 0) * HAND_CARD_GAP + 40
        canvas = Image.new("RGBA", (width, height), (14, 18, 28, 255))
        draw = ImageDraw.Draw(canvas)
        title_font = _font(28, bold=True)
        label_font = _font(20, bold=True)

        draw.text((40, 26), "Sua mao de UNO", fill=(248, 250, 255), font=title_font)
        draw.text((40, 72), "Topo da mesa", fill=(180, 193, 216), font=label_font)
        canvas.alpha_composite(top_image, (40, 100))

        hand_label = "Cartas na mao (envie o numero da carta)"
        draw.text((340, 72), hand_label, fill=(180, 193, 216), font=label_font)
        for index, image in enumerate(shown_images):
            row, column = divmod(index, HAND_CARDS_PER_ROW)
            x = 340 + column * (CARD_WIDTH + HAND_CARD_GAP)
            y = 100 + row * (CARD_HEIGHT + HAND_CARD_GAP)
            canvas.alpha_composite(image, (x, y))
            number = str(index + 1)
            draw.rounded_rectangle((x + 12, y + 12, x + 74, y + 70), radius=14, fill=(8, 12, 24, 235), outline=(255, 255, 255, 220), width=2)
            draw.text((x + 31, y + 20), number, fill=(255, 255, 255), font=_font(28, bold=True))

        return _file(canvas, "uno-mao.png")

    async def build_table_image(
        self,
        top_card: object,
        *,
        current_color: str | None,
        character: str | None = None,
        character_url: str | None = None,
        character_effect: str | None = None,
        character_target: str | None = None,
    ) -> discord.File:
        card = await self._card_image(top_card, current_color=current_color)
        width = 820 if character else 420
        canvas = Image.new("RGBA", (width, 520), (14, 18, 28, 255))
        draw = ImageDraw.Draw(canvas)
        title_font = _font(30, bold=True)
        label_font = _font(20, bold=True)

        draw.text((40, 26), "Mesa de UNO", fill=(248, 250, 255), font=title_font)
        draw.text((40, 74), "Carta atual", fill=(180, 193, 216), font=label_font)
        canvas.alpha_composite(card, (70, 110))
        if character:
            portrait = await self._character_image(character, character_url, character_effect, character_target)
            draw.text((460, 74), "Personagem do caos", fill=(180, 193, 216), font=label_font)
            canvas.alpha_composite(portrait, (460, 100))
        return _file(canvas, "uno-mesa.png")

    async def _character_image(
        self,
        character: str,
        url: str | None,
        effect: str | None,
        target: str | None,
    ) -> Image.Image:
        if url:
            remote = await _download_image(url, self._cache_dir / _safe_name(url))
            if remote is not None:
                return _fit_character(remote, CHARACTER_NAMES.get(character, character), effect, target)
        return _draw_character_art(character, effect, target)

    async def _card_image(self, card: object, *, current_color: str | None) -> Image.Image:
        color = getattr(card, "color", None)
        value = getattr(card, "value", "")
        url = _card_url(color, value)

        if url:
            remote = await _download_image(url, self._cache_dir / _safe_name(url))
            if remote is not None:
                return _fit_card(remote)

        return _draw_card_face(color=color, value=value, current_color=current_color)


def _card_url(color: str | None, value: str) -> str | None:
    if value == "coringa":
        filename = "Wild_Card_Change_Colour.png"
    elif value == "+4":
        filename = "Wild_Card_Draw_4.png"
    elif color in IMAGE_COLOR_NAMES and value.isdigit() and 0 <= int(value) <= 9:
        filename = f"{IMAGE_COLOR_NAMES[color]}_{value}.png"
    elif color in IMAGE_COLOR_NAMES and value in IMAGE_VALUE_NAMES:
        filename = f"{IMAGE_COLOR_NAMES[color]}_{IMAGE_VALUE_NAMES[value]}.png"
    else:
        return None
    return f"{CARD_IMAGE_BASE_URL}/{filename}"


def _draw_card_face(*, color: str | None, value: str, current_color: str | None) -> Image.Image:
    fill = COLOR_RGB.get(color or current_color or "vermelho", (255, 255, 255))
    image = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((6, 6, CARD_WIDTH - 6, CARD_HEIGHT - 6), radius=34, fill=(255, 255, 255, 255))
    draw.rounded_rectangle((22, 22, CARD_WIDTH - 22, CARD_HEIGHT - 22), radius=28, fill=fill)
    draw.rounded_rectangle((48, 46, CARD_WIDTH - 48, CARD_HEIGHT - 46), radius=22, outline=(255, 255, 255, 200), width=4)
    label = value.upper()
    font_big = _font(120 if len(label) <= 2 else 76, bold=True)
    font_small = _font(34, bold=True)
    text_fill = (255, 255, 255)
    bbox = draw.textbbox((0, 0), label, font=font_big)
    draw.text(((CARD_WIDTH - (bbox[2] - bbox[0])) / 2, 152), label, fill=text_fill, font=font_big)
    if color:
        draw.text((42, 32), color[:1].upper(), fill=text_fill, font=font_small)
        draw.text((CARD_WIDTH - 70, CARD_HEIGHT - 82), color[:1].upper(), fill=text_fill, font=font_small)
    else:
        draw.text((52, 32), "UNO", fill=text_fill, font=font_small)
    return image


def _fit_card(image: Image.Image) -> Image.Image:
    return image.convert("RGBA").resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)


def _fit_character(image: Image.Image, name: str, effect: str | None, target: str | None) -> Image.Image:
    portrait = image.convert("RGBA")
    portrait.thumbnail((330, 380), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (330, 420), (22, 28, 45, 255))
    x = (330 - portrait.width) // 2
    y = 12 + (380 - portrait.height) // 2
    canvas.alpha_composite(portrait, (x, y))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((3, 3, 327, 417), radius=24, outline=(255, 255, 255, 180), width=3)
    draw.text((18, 384), name, fill=(248, 250, 255), font=_font(22, bold=True))
    _draw_action_overlay(draw, effect, target)
    return canvas


def _draw_character_art(character: str, effect: str | None, target: str | None) -> Image.Image:
    palette_index = sum(ord(char) for char in character) % len(CHARACTER_COLORS)
    accent = CHARACTER_COLORS[palette_index]
    image = Image.new("RGBA", (330, 420), (22, 28, 45, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((30, 45, 300, 350), fill=accent, outline=(255, 255, 255, 180), width=4)
    draw.ellipse((78, 145, 130, 205), fill=(245, 248, 255, 255))
    draw.ellipse((200, 145, 252, 205), fill=(245, 248, 255, 255))
    draw.ellipse((98, 164, 115, 188), fill=(18, 22, 35, 255))
    draw.ellipse((215, 164, 232, 188), fill=(18, 22, 35, 255))
    draw.arc((92, 218, 240, 310), start=10, end=170, fill=(18, 22, 35, 255), width=8)
    draw.polygon((55, 75, 95, 15, 130, 82), fill=accent, outline=(255, 255, 255, 150))
    draw.polygon((200, 82, 235, 15, 275, 75), fill=accent, outline=(255, 255, 255, 150))
    draw.rounded_rectangle((3, 3, 327, 417), radius=24, outline=(255, 255, 255, 180), width=3)
    draw.text((18, 384), CHARACTER_NAMES.get(character, character), fill=(248, 250, 255), font=_font(22, bold=True))
    _draw_action_overlay(draw, effect, target)
    return image


def _draw_action_overlay(draw: ImageDraw.ImageDraw, effect: str | None, target: str | None) -> None:
    if not effect:
        return
    style_name = next((name for name, effects in ACTION_GROUPS.items() if effect in effects), "chaos")
    color, label, icon = ACTION_STYLES[style_name]
    draw.rounded_rectangle((12, 14, 92, 92), radius=18, fill=color, outline=(255, 255, 255, 210), width=2)
    draw.text((36, 24), icon, fill=(255, 255, 255), font=_font(44, bold=True))
    draw.rounded_rectangle((12, 304, 318, 374), radius=16, fill=(8, 12, 24, 225), outline=color, width=3)
    draw.text((24, 314), f"{label}: {effect.upper()}", fill=(255, 255, 255), font=_font(17, bold=True))
    if target:
        draw.text((24, 341), f"ALVO: {_short_text(target, 25)}", fill=(220, 228, 242), font=_font(15, bold=True))


def _short_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


async def _download_image(url: str, cache_path: Path) -> Image.Image | None:
    if cache_path.exists():
        try:
            return Image.open(cache_path).convert("RGBA")
        except Exception:
            cache_path.unlink(missing_ok=True)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                if response.status != 200:
                    return None
                data = await response.read()
    except Exception:
        return None

    try:
        image = Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        return None

    try:
        image.save(cache_path)
    except Exception:
        pass
    return image


def _safe_name(url: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in url)[:120] + ".png"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ["C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf", "arial.ttf"]
        if bold
        else ["C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf", "arial.ttf"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _file(image: Image.Image, filename: str) -> discord.File:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=filename)
