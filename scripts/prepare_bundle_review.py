"""Prepare a local-only 20-video review pack; never contacts the backend."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import zipfile
from PIL import Image, ImageDraw
from process_bundle_clip import process, probe
from source_exclusions import exclusions

ROOT=Path(__file__).resolve().parents[1]


def write_review(output, records):
    (output/'manifest.json').write_text(json.dumps(records,indent=2,ensure_ascii=False))
    fields=['number','bundle','member','source_sha256','source_duration','status','duration','regions','error']
    with (output/'manifest.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(records)
    cards=[]
    for row in records:
        number=row['number'];name=f'{number:02}'
        safe=html.escape
        cards.append(f'''<article><h2>{name} · {safe(row['bundle'])}</h2>
<p>{safe(row['status'])} · {row.get('duration',row['source_duration'])} seconds · {row.get('regions',0)} blend regions</p>
<div class="pair"><section><h3>Processed</h3><video controls preload="none" poster="posters/{name}.jpg" src="processed/{name}.mp4"></video></section>
<section><h3>Original</h3><video controls preload="none" src="originals/{name}.mp4"></video></section></div>
<p><a href="processed/{name}.mp4">Open processed video</a> · <a href="reports/{name}.json">Detection report</a></p>
<details><summary>Source</summary>{safe(row['member'])}<br>SHA256: {row['source_sha256']}</details></article>''')
    page='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Trendyapa · 20 local samples</title>
<style>body{font:16px system-ui;background:#151515;color:#eee;max-width:1100px;margin:32px auto;padding:20px}article{padding:20px;margin:24px 0;background:#242424;border-radius:12px}h2{font-size:18px}h3{font-size:15px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}video{width:100%;height:520px;background:black;object-fit:contain}a{color:#9ecbff}details{overflow-wrap:anywhere} @media(max-width:650px){.pair{grid-template-columns:1fr}video{height:440px}}</style></head><body><h1>20 local reel samples</h1><p>Four per ZIP. Compare the processed version with its original. Nothing in this pack is published or reserved.</p>'''+''.join(cards)+'</body></html>'
    (output/'index.html').write_text(page)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'bundle-output/review-20');args=parser.parse_args();output=args.output.resolve()
    for name in ('originals','processed','reports','posters'):(output/name).mkdir(parents=True,exist_ok=True)
    records=[];hashes=set(exclusions())
    for archive_path in sorted((ROOT/'bundles').glob('*.zip')):
        with zipfile.ZipFile(archive_path) as archive:
            entries=[e for e in archive.infolist() if not e.is_dir() and e.filename.lower().endswith('.mp4')]
            order=[]
            for start in [0,len(entries)//4,len(entries)//2,len(entries)*3//4]:
                order.extend(range(start,min(start+15,len(entries))))
            selected=0
            for index in dict.fromkeys(order):
                if selected==4:break
                entry=entries[index];number=len(records)+1;source=output/'originals'/f'{number:02}.mp4'
                digest=hashlib.sha256()
                with archive.open(entry) as src,source.open('wb') as target:
                    while chunk:=src.read(1024*1024):digest.update(chunk);target.write(chunk)
                sha=digest.hexdigest()
                if sha in hashes:continue
                try:
                    info=probe(source);duration=float(info['format']['duration'])
                    if duration<=5 or not any(s['codec_type']=='video' for s in info['streams']):continue
                except (subprocess.CalledProcessError,ValueError,KeyError):continue
                hashes.add(sha);selected+=1
                records.append({'number':number,'bundle':archive_path.name,'member':entry.filename,
                                'source_sha256':sha,'source_duration':duration,'status':'pending'})
            if selected!=4:raise RuntimeError(f'Unable to select four clips from {archive_path.name}')
    if len(records)!=20:raise RuntimeError('Expected five ZIPs and twenty clips')
    write_review(output,records)
    print('Selected twenty unique clips: four from each ZIP',flush=True)
    def render(row):
        name=f"{row['number']:02}"
        if not process(output/'originals'/f'{name}.mp4',output/'processed'/f'{name}.mp4',output/'reports'/f'{name}.json'):
            return {'status':'excluded','duration':0,'regions':0}
        report=json.loads((output/'reports'/f'{name}.json').read_text())
        subprocess.run(['ffmpeg','-v','error','-ss','2','-i',str(output/'processed'/f'{name}.mp4'),'-frames:v','1',
                        '-vf','scale=270:480','-y',str(output/'posters'/f'{name}.jpg')],check=True,capture_output=True)
        return {'status':'rendered','duration':float(report['duration']),'regions':len(report['regions'])}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures={executor.submit(render,row):row for row in records}
        for future in as_completed(futures):
            row=futures[future]
            try:row.update(future.result())
            except Exception as error:row.update(status='failed',error=type(error).__name__)
            write_review(output,records)
            print(f"{sum(r['status']!='pending' for r in records)}/20 · {row['number']:02} {row['status']}",flush=True)
    if any(r['status']!='rendered' for r in records):raise SystemExit('Some samples failed; see manifest')
    sheet=Image.new('RGB',(5*216,4*414),'#202020');draw=ImageDraw.Draw(sheet)
    for i,row in enumerate(records):
        x=(i%5)*216;y=(i//5)*414
        with Image.open(output/'posters'/f"{row['number']:02}.jpg") as poster:
            sheet.paste(poster.resize((216,384)),(x,y+28))
        draw.text((x+6,y+6),f"{row['number']:02} · {row['duration']:.1f}s",fill='white')
    sheet.save(output/'contact-sheet.jpg')
    print(f'COMPLETE {output}/index.html',flush=True)


if __name__=='__main__':main()
