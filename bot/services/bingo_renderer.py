from __future__ import annotations
import io
from PIL import Image, ImageDraw, ImageFont
from .bingo_service import Card

def _font(size, bold=False):
    for name in (["arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else ["arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]):
        try: return ImageFont.truetype(name, size)
        except OSError: pass
    return ImageFont.load_default()

def render_card(card: Card, label: str = "BINGO DA AYLA") -> io.BytesIO:
    image=Image.new("RGB",(900,980),(44,166,105)); draw=ImageDraw.Draw(image); white=(248,252,250); green=(20,125,76); dark=(24,38,33); yellow=(255,224,86)
    draw.rounded_rectangle((35,35,865,945),radius=28,fill=white); draw.text((65,52),label,fill=green,font=_font(38,True)); draw.text((65,104),f"Cartela Nº {card.card_id[:8].upper()}",fill=green,font=_font(22))
    left,top,cell=65,165,154
    for c,letter in enumerate("BINGO"):
        x=left+c*cell; draw.rounded_rectangle((x,top,x+cell-8,top+74),radius=15,fill=green); _center(draw,letter,(x,top,x+cell-8,top+74),_font(45,True),white)
        for r in range(5):
            y=top+84+r*132; value=card.numbers[r][c]; active=value is None or value in card.marked; draw.rounded_rectangle((x,y,x+cell-8,y+122),radius=10,fill=(225,246,235) if active else white,outline=green,width=3)
            _center(draw,"★" if value is None else str(value),(x,y,x+cell-8,y+122),_font(45 if value is None else 40,True),green if value is None else dark)
            if value is not None and active: _center(draw,"X",(x,y,x+cell-8,y+122),_font(58,True),yellow)
    draw.text((65,900),"★ FREE SPACE  •  X = marcado",fill=green,font=_font(22,True)); output=io.BytesIO(); image.save(output,"PNG"); output.seek(0); return output

def _center(draw,text,box,font,fill):
    b=draw.textbbox((0,0),text,font=font); draw.text((box[0]+(box[2]-box[0]-(b[2]-b[0]))/2,box[1]+(box[3]-box[1]-(b[3]-b[1]))/2-b[1]),text,font=font,fill=fill)
