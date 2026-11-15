"""OCR watermark masking and branded reel rendering."""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                           '-of', 'json', str(path)]))


def process(source, output, report, max_seconds=30):
    for tool in ('ffmpeg', 'ffprobe', 'tesseract'):
        if not shutil.which(tool):
            raise RuntimeError(f'Required tool missing: {tool}')
    info = probe(source)
    next(s for s in info['streams'] if s['codec_type'] == 'video')
    duration = min(float(info['format']['duration']), max_seconds)
    if duration <= 5:
        raise ValueError('Video must be longer than five seconds')
    findings = []
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        # Sample the entire source so later watermarks are not ignored by a trim.
        full_duration = float(info['format']['duration'])
        for index in range(12):
            frame = temp / f'{index}.png'
            run(['ffmpeg', '-v', 'error', '-ss', str(full_duration * (index + .5) / 12),
                 '-i', str(source), '-frames:v', '1', '-vf', 'scale=1080:-2', str(frame)])
            frame_info = probe(frame)['streams'][0]
            frame_height = frame_info['height']
            tsv = run(['tesseract', str(frame), 'stdout', '--psm', '11', 'tsv'])
            for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
                text = (row.get('text') or '').strip()
                if float(row.get('conf', '-1')) < 65 or len(re.sub(r'[^a-zA-Z0-9]', '', text)) < 4:
                    continue
                # Mask explicit branding matches only. Edge text alone produced
                # false positives on artwork and obscured most of the frame.
                branded = re.search(r'@[a-z0-9_.]{3,}|www\.|\.com|instagram|tiktok|youtube|reelbundle|watermark', text, re.I)
                if branded:
                    findings.append({'sample': index, 'text': text,
                                     'box': [int(row[k]) for k in ('left', 'top', 'width', 'height')],
                                     'frame_height': frame_height})
        result = {'source': str(source), 'ocr_samples': 12, 'findings': findings,
                  'status': 'rendering',
                  'limitation': 'OCR screening can miss logos, stylized text and moving marks.'}
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2))
        output.parent.mkdir(parents=True, exist_ok=True)
        # Mask sampled regions throughout the clip, including moving marks.
        # Coordinates are normalized before fitting the frame to portrait output.
        masks = set()
        for finding in findings:
            left, top, width, height = finding['box']
            masks.add((max(0, left - 12), max(0, top - 12),
                       min(1080, left + width + 12),
                       min(finding['frame_height'], top + height + 12)))
        mask_filters = 'scale=1080:-2,' + ''.join(
            f'drawbox=x={x}:y={y}:w={right-x}:h={bottom-y}:color=black:t=fill,'
            for x, y, right, bottom in sorted(masks))
        filters = mask_filters + ('scale=1080:1920:force_original_aspect_ratio=decrease,'
                   'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,'
                   'eq=contrast=1.03:saturation=1.04,unsharp=5:5:0.3:5:5:0')
        branding = Image.new('RGBA', (170, 48), (0, 0, 0, 90))
        draw = ImageDraw.Draw(branding)
        fonts = ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                 '/System/Library/Fonts/Supplemental/Arial.ttf']
        font = next((ImageFont.truetype(f, 28) for f in fonts if Path(f).exists()), ImageFont.load_default())
        draw.text((7, 6), 'trendyapa', font=font, fill=(255, 255, 255, 210))
        brand_path = temp / 'brand.png'
        branding.save(brand_path)
        rendered = temp / 'render.mp4'
        run(['ffmpeg', '-v', 'error', '-i', str(source), '-i', str(brand_path), '-t', str(duration), '-filter_complex',
             f'[0:v]{filters}[base];[base][1:v]overlay=W-w-32:H-h-100[v]',
             '-map', '[v]', '-map', '0:a:0?', '-r', '30',
             '-c:v', 'libx264', '-crf', '20', '-preset', 'medium', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(rendered)])
        final = probe(rendered)
        stream = next(s for s in final['streams'] if s['codec_type'] == 'video')
        if float(final['format']['duration']) <= 5 or (stream['width'], stream['height']) != (1080, 1920):
            raise ValueError('Rendered output failed duration or dimension validation')
        shutil.copyfile(rendered, output)
        result.update(status='rendered', output=str(output), duration=final['format']['duration'])
        report.write_text(json.dumps(result, indent=2))
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    process(args.source, args.output, args.report)
