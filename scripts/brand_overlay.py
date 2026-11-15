"""Code-rendered, antialiased Trendyapa wordmark with a transparent surround."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def create_brand_overlay():
    scale=3
    fonts=['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
           '/System/Library/Fonts/Supplemental/Arial Bold.ttf']
    font=next((ImageFont.truetype(path,29*scale) for path in fonts if Path(path).exists()),ImageFont.load_default())
    width=int(font.getlength('trendyapa')/scale)+78
    image=Image.new('RGBA',(width*scale,64*scale),(0,0,0,0))
    draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((0,0,(width-1)*scale,63*scale),radius=22*scale,
                           fill=(15,18,25,174),outline=(255,255,255,65),width=scale)
    # Warm gradient icon, clipped to a rounded square.
    size=42*scale
    gradient=Image.new('RGBA',(size,size))
    pixels=ImageDraw.Draw(gradient)
    for y in range(size):
        t=y/max(1,size-1)
        pixels.line((0,y,size,y),fill=(255,int(163-68*t),int(82-20*t),255))
    mask=Image.new('L',(size,size));ImageDraw.Draw(mask).rounded_rectangle((0,0,size-1,size-1),radius=14*scale,fill=255)
    image.paste(gradient,(10*scale,11*scale),mask)
    draw=ImageDraw.Draw(image)
    draw.polygon([(27*scale,23*scale),(27*scale,41*scale),(40*scale,32*scale)],fill='white')
    x=63*scale
    draw.text((x,18*scale),'trendy',font=font,fill=(255,255,255,255),stroke_width=0,anchor='lt')
    x+=draw.textlength('trendy',font=font)
    draw.text((x,18*scale),'apa',font=font,fill=(255,171,107,255),anchor='lt')
    return image.resize((width,64),Image.Resampling.LANCZOS)
