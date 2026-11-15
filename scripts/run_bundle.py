"""Render and publish one eligible bundle reel through the trusted backend."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from bundle_backend import call

ROOT=Path(__file__).resolve().parents[1]


def upload(video,source_hash,artifact):
    repo='Skywalkers-24/instapilot-reel-worker';tag='bundle-media-v1-'+source_hash[0]
    name=source_hash+'-'+artifact[:12]+'.mp4'
    result=subprocess.run(['gh','release','view',tag,'--repo',repo,'--json','assets'],capture_output=True,text=True)
    if result.returncode:
        subprocess.run(['gh','release','create',tag,'--repo',repo,'--target','feature/trendyapa-bundle-reels','--title',tag,'--notes','Processed bundle reel media.','--prerelease'],check=True,capture_output=True)
        assets=[]
    else:assets=json.loads(result.stdout)['assets']
    if not any(a['name']==name for a in assets):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/name;os.link(video,path)
            subprocess.run(['gh','release','upload',tag,str(path),'--repo',repo],check=True,capture_output=True,timeout=600)
    return f'https://github.com/{repo}/releases/download/{tag}/{name}'


def main():
    force = os.getenv('FORCE_PUBLISH', '').lower() in ('true', '1')
    endpoint = 'publish/next?force=true' if force else 'publish/next'
    state=call(endpoint);status=state.get('status');print('Eligibility:',status,flush=True)
    if status in ('DISABLED','OUTSIDE_SLOT','SLOT_ALREADY_CLAIMED','CADENCE_SKIP','DAILY_LIMIT'):
        return
    if status=='PUBLISHED':print('Already published for this workflow run');return
    if status=='READY_TO_RENDER':
        output=ROOT/'bundle-output';output.mkdir(exist_ok=True)
        history=output/'history.json';history.write_text(json.dumps(state['hashes']))
        subprocess.run([sys.executable,str(ROOT/'scripts/select_bundle_clip.py'),'--history',str(history),'--seed',str(state.get('selection_seed',0))],check=True)
        selection=json.loads((output/'selection.json').read_text());video=Path(selection['video'])
        with video.open('rb') as stream:artifact=hashlib.file_digest(stream,'sha256').hexdigest()
        metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(video)]))
        duration=float(metadata['format']['duration'])
        url=upload(video,selection['source_sha256'],artifact)
        reg_endpoint = 'publish/register?force=true' if force else 'publish/register'
        state=call(reg_endpoint,{'source_sha256':selection['source_sha256'],'artifact_sha256':artifact,
                   'archive':selection['archive'],'member':selection['member'],'video_url':url,'duration':duration})
        if state.get('status') in ('OUTSIDE_SLOT','SLOT_ALREADY_CLAIMED','CADENCE_SKIP','DAILY_LIMIT'):return
    if state.get('status')=='CLAIMED':state=call('publish/create',{})
    if state.get('status') not in ('CREATED','FINISHED'):
        raise SystemExit('Backend stopped this run: '+str(state.get('status')))
    for _ in range(20):
        if state.get('status')=='FINISHED':break
        time.sleep(10);state=call('publish/status');print('Processing:',state.get('processing_status',state.get('status')),flush=True)
        if state.get('processing_status') in ('ERROR','EXPIRED'):raise SystemExit('Provider processing failed; source stays reserved')
    else:raise SystemExit('Processing timeout; source stays reserved')
    result=call('publish/publish',{})
    print(json.dumps({'status':result.get('status'),'media_id':result.get('media_id')}),flush=True)
    if result.get('status')!='PUBLISHED':raise SystemExit('Publish unconfirmed; reservation retained')


if __name__=='__main__':main()
