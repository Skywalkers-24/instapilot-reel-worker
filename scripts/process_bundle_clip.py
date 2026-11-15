"""OCR watermark masking and branded reel rendering."""
import argparse
import csv
import math
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from PIL import Image
from brand_overlay import create_brand_overlay
from watermark_detection import text_candidate, sparkle_candidates, stable_regions, youtube_candidates, timed_text_regions


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
        # Sample the portion that will actually be rendered at up to two frames/second.
        sample_count=max(12,min(60,math.ceil(duration*2)))
        step=duration/sample_count
        for index in range(sample_count):
            timestamp=step*(index+.5)
            frame = temp / f'{index}.png'
            run(['ffmpeg', '-v', 'error', '-ss', str(timestamp),
                 '-i', str(source), '-frames:v', '1', '-vf', 'scale=1080:-2', str(frame)])
            if not frame.exists():
                continue
            with Image.open(frame) as image:
                frame_height = image.height
            tsv = run(['tesseract', str(frame), 'stdout', '--psm', '11', 'tsv'])
            for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
                finding = text_candidate(row, 1080, frame_height)
                if finding:
                    findings.append({**finding, 'sample':index, 'time':timestamp})
            with Image.open(frame) as image:
                findings.extend({**f, 'sample':index, 'time':timestamp} for f in sparkle_candidates(image)+youtube_candidates(image))
        regions = stable_regions([f for f in findings if f['kind']!='creator_text'],1080,frame_height)
        regions += timed_text_regions(findings,1080,frame_height,step,duration)
        budget_exceeded = any(
            sum(r['box'][2]*r['box'][3] for r in regions
                if 'start' not in r or r['start'] <= step*(i+.5) < r['end'])
            > 1080*frame_height*.04 for i in range(sample_count))
        if budget_exceeded:
            regions = []
        result = {'source': str(source), 'ocr_samples': sample_count, 'findings': findings, 'regions':regions,
                  'status': 'rendering', 'mask_budget_exceeded':budget_exceeded,
                  'limitation': 'Unknown graphics and low-confidence text can remain. Fast motion between samples can leave residual marks.'}
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2))
        output.parent.mkdir(parents=True, exist_ok=True)
        # Blend only bounded, repeated edge regions; never union all OCR words.
        mask_filters = 'scale=1080:-2,' + ''.join(
            f"delogo=x={r['box'][0]}:y={r['box'][1]}:w={r['box'][2]}:h={r['box'][3]}"
            + (f":enable='between(t,{r['start']:.4f},{r['end']:.4f})'" if 'start' in r else '') + ','
            for r in regions)
        filters = mask_filters + ('scale=1080:1920:force_original_aspect_ratio=decrease,'
                   'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,'
                   'eq=contrast=1.03:saturation=1.04,unsharp=5:5:0.3:5:5:0')
        branding = create_brand_overlay()
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
