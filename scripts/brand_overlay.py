"""Single-line monochrome Trendyapa wordmark with transparent corners."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def create_brand_overlay():
    scale = 3
    fonts = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
             '/System/Library/Fonts/Supplemental/Arial Bold.ttf']
    font = next((ImageFont.truetype(path, 29 * scale) for path in fonts if Path(path).exists()),
                ImageFont.load_default())
    width = int(font.getlength('trendyapa') / scale) + 28
    height = 52
    image = Image.new('RGBA', (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, (width - 1) * scale, (height - 1) * scale),
                           radius=10 * scale, fill=(12, 12, 12, 145))
    # Draw the whole word once so all letters share one typographic baseline.
    draw.text((14 * scale, 35 * scale), 'trendyapa', font=font,
              fill=(255, 255, 255, 245), anchor='ls')
    return image.resize((width, height), Image.Resampling.LANCZOS)
