from io import BytesIO

import discord
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont


WIDTH = 900
HEIGHT = 360

SYMBOLS = {
    "7": "7",
    "estrela": "*",
    "cereja": "C",
    "sino": "B",
    "diamante": "D",
}


def build_bet_card(
    title: str,
    subtitle: str,
    values: list[str],
    *,
    won: bool,
    amount: int,
    balance: int,
    delta: int | None = None,
    filename: str = "aposta.png",
) -> discord.File:
    image = _background(won)
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = _font(42, bold=True)
    subtitle_font = _font(22)
    value_font = _font(74, bold=True)
    label_font = _font(18, bold=True)
    small_font = _font(22, bold=True)

    draw.text((42, 32), title, fill=(250, 252, 255), font=title_font)
    draw.text((44, 86), subtitle, fill=(186, 207, 238), font=subtitle_font)

    badge_fill = (43, 160, 104, 230) if won else (186, 60, 62, 230)
    badge_text = "VITORIA" if won else "DERROTA"
    draw.rounded_rectangle((692, 34, 850, 78), radius=14, fill=badge_fill)
    _center_text(draw, (692, 34, 850, 78), badge_text, label_font, (255, 255, 255))

    _draw_value_row(draw, values, value_font)
    _metric(draw, 42, 278, "APOSTA", _currency_text(amount), small_font, label_font)
    _metric(draw, 284, 278, "SALDO", _currency_text(balance), small_font, label_font)
    delta = amount if delta is None else delta
    delta_prefix = "+" if delta > 0 else ""
    _metric(draw, 526, 278, "GANHO/PERDA", delta_prefix + _currency_text(delta), small_font, label_font)

    return _file(image, filename)


def _background(won: bool) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (10, 14, 26))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(HEIGHT):
        ratio = y / max(HEIGHT - 1, 1)
        r = int(12 + 18 * ratio)
        g = int(18 + 20 * ratio)
        b = int(38 + 28 * ratio)
        draw.line((0, y, WIDTH, y), fill=(r, g, b, 255))

    accent = (80, 220, 156, 82) if won else (242, 92, 92, 76)
    draw.ellipse((WIDTH - 300, -120, WIDTH + 120, 250), fill=accent)
    draw.ellipse((-150, 190, 280, HEIGHT + 150), fill=(95, 143, 255, 58))
    draw.rounded_rectangle((28, 22, WIDTH - 28, HEIGHT - 24), radius=26, fill=(255, 255, 255, 22), outline=(255, 255, 255, 64), width=2)
    return image


def _draw_value_row(draw: ImageDraw.ImageDraw, values: list[str], font: ImageFont.ImageFont) -> None:
    count = max(len(values), 1)
    base_width = 260 if count <= 2 else 150
    total_width = min(760, count * base_width + (count - 1) * 20)
    start_x = (WIDTH - total_width) // 2
    y = 132
    box_width = (total_width - (count - 1) * 20) // count
    for index, value in enumerate(values):
        x = start_x + index * (box_width + 20)
        draw.rounded_rectangle((x, y, x + box_width, y + 118), radius=20, fill=(255, 255, 255, 238), outline=(126, 159, 255, 170), width=3)
        _center_text(draw, (x, y, x + box_width, y + 118), value, font, (18, 35, 68))


def _metric(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str, value_font: ImageFont.ImageFont, label_font: ImageFont.ImageFont) -> None:
    draw.rounded_rectangle((x, y, x + 210, y + 54), radius=14, fill=(8, 12, 24, 180), outline=(108, 132, 180, 110), width=1)
    draw.text((x + 16, y + 8), label, fill=(146, 164, 198), font=label_font)
    draw.text((x + 16, y + 27), _fit_text(draw, value, value_font, 174), fill=(250, 252, 255), font=value_font)


def _center_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    text_box = draw.textbbox((0, 0), text, font=font)
    width = text_box[2] - text_box[0]
    height = text_box[3] - text_box[1]
    x = box[0] + (box[2] - box[0] - width) / 2
    y = box[1] + (box[3] - box[1] - height) / 2 - 3
    draw.text((x, y), text, fill=fill, font=font)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    trimmed = text
    while trimmed and draw.textlength(f"{trimmed}...", font=font) > max_width:
        trimmed = trimmed[:-1]
    return f"{trimmed}..."


def _currency_text(amount: int) -> str:
    return f"{amount} wink" if abs(amount) == 1 else f"{amount} winks"


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
