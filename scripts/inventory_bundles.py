"""Read-only archive audit: SHA256, metadata, first-frame decode and exclusions."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
import csv,hashlib,json,subprocess,tempfile,zipfile
from pathlib import Path
from source_exclusions import exclusions
ROOT=Path(__file__).resolve().parents[1]
EXTENSIONS={'.mp4','.mov','.m4v','.webm','.mkv','.avi'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'bundle-output/inventory');p.add_argument('--history',type=Path);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    blocked=exclusions();history=json.loads(args.history.read_text()) if args.history else {}
    jobs=[]
    for archive in sorted((ROOT/'bundles').glob('*.zip')):
        with zipfile.ZipFile(archive) as z:
            jobs.extend((archive,entry.filename) for entry in z.infolist() if not entry.is_dir() and Path(entry.filename).suffix.lower() in EXTENSIONS)
    def inspect(job):
        archive,member=job;row={'archive':archive.name,'member':member}
        try:
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'source.mp4';digest=hashlib.sha256()
                with zipfile.ZipFile(archive) as z,z.open(member) as source,path.open('wb') as target:
                    while chunk:=source.read(1024*1024):digest.update(chunk);target.write(chunk)
                sha=digest.hexdigest();row['source_sha256']=sha
                result=subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,text=True,timeout=30)
                if result.returncode:row['status']='invalid_metadata';return row
                info=json.loads(result.stdout);video=next((s for s in info['streams'] if s['codec_type']=='video'),None)
                duration=float(info.get('format',{}).get('duration',0));row['duration']=duration
                if not video:row['status']='no_video';return row
                row.update(width=video['width'],height=video['height'])
                if duration<=5:row['status']='too_short';return row
                decode=subprocess.run(['ffmpeg','-v','error','-threads','1','-i',str(path),'-map','0:v:0','-frames:v','1','-f','null','-'],capture_output=True,timeout=30)
                if decode.returncode:row['status']='decode_failed';return row
                row['status']='duration_decode_valid'
                row['known_exclusion']=blocked.get(sha,{}).get('reason','')
                row['ledger_status']=history.get(sha,'')
                return row
        except Exception as e:row.update(status='audit_error',error=type(e).__name__);return row
    records=[]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures=[executor.submit(inspect,job) for job in jobs]
        for future in as_completed(futures):
            records.append(future.result())
            if len(records)%100==0:print(f'Audited {len(records)}/{len(jobs)}',flush=True)
    records.sort(key=lambda r:(r['archive'],r['member']))
    seen=set();summary=[]
    for archive in sorted({r['archive'] for r in records}):
        subset=[r for r in records if r['archive']==archive];counts=Counter()
        for r in subset:
            counts['video_entries']+=1
            if r['status']!='duration_decode_valid':counts[r['status']]+=1;continue
            counts['duration_decode_valid_entries']+=1
            if r['source_sha256'] in seen:r['exact_duplicate']=True;counts['exact_duplicates']+=1;continue
            seen.add(r['source_sha256']);r['exact_duplicate']=False;counts['unique_duration_decode_valid']+=1
            if r['known_exclusion']:counts['known_excluded']+=1;continue
            if r['ledger_status']:counts['already_reserved_or_published']+=1;continue
            counts['available_before_watermark_screening']+=1
        summary.append({'archive':archive,**counts})
    total=Counter()
    for item in summary:total.update({k:v for k,v in item.items() if k!='archive'})
    result={'bundles':summary,'totals':dict(total),'scope':'Metadata + >5 seconds + first-frame decode + exact-byte deduplication. Watermark screening still required before publishing. Not a full-stream corruption or perceptual-duplicate audit.'}
    (args.output/'summary.json').write_text(json.dumps(result,indent=2));(args.output/'videos.json').write_text(json.dumps(records,indent=2,ensure_ascii=False))
    fields=sorted({key for r in records for key in r})
    with (args.output/'videos.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(records)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
