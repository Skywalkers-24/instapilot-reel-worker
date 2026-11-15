"""Conservative OCR screening and attribution-preserving reel rendering."""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


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
                if float(row.get('conf', '-1')) < 35 or not text:
                    continue
                # OCR text at any edge is conservatively treated as branding;
                # handles, URLs and explicit branding terms are flagged anywhere.
                edge = (int(row['left']) < 160 or int(row['left']) + int(row['width']) > 920
                        or int(row['top']) < frame_height * .15
                        or int(row['top']) + int(row['height']) > frame_height * .85)
                branded = re.search(r'@|www\.|\.com|instagram|tiktok|reelbundle|watermark|follow', text, re.I)
                if edge or branded:
                    findings.append({'sample': index, 'text': text,
                                     'box': [int(row[k]) for k in ('left', 'top', 'width', 'height')]})
        result = {'source': str(source), 'ocr_samples': 12, 'findings': findings,
                  'status': 'skipped_source_mark' if findings else 'rendering',
                  'limitation': 'OCR screening can miss logos, stylized text and moving marks.'}
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2))
        if findings:
            return False
        output.parent.mkdir(parents=True, exist_ok=True)
        # Fit the full frame: no source attribution is cropped away.
        filters = ('scale=1080:1920:force_original_aspect_ratio=decrease,'
                   'pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,'
                   'eq=contrast=1.03:saturation=1.04,unsharp=5:5:0.3:5:5:0,'
                   'drawtext=text=trendyapa:fontsize=28:fontcolor=white@0.8:'
                   'box=1:boxcolor=black@0.3:boxborderw=6:x=w-tw-32:y=h-th-100')
        rendered = temp / 'render.mp4'
        run(['ffmpeg', '-v', 'error', '-i', str(source), '-t', str(duration),
             '-map', '0:v:0', '-map', '0:a:0?', '-vf', filters, '-r', '30',
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
