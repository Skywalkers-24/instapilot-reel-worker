"""Content-hash exclusions survive renamed files and ZIP repackaging."""
import hashlib
import json
from pathlib import Path

PATH=Path(__file__).resolve().parents[1]/'data/source_exclusions.json'


def exclusions():
    return json.loads(PATH.read_text())['sources']


def source_digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def fixed_regions(digest,width,height):
    path=PATH.with_name('source_watermark_profiles.json')
    profile=json.loads(path.read_text())['sources'].get(digest)
    if not profile:
        return []
    rw,rh=profile['reference_size']
    regions=[]
    for item in profile['regions']:
        x,y,w,h=item['box']
        box=[round(x*width/rw),round(y*height/rh),round(w*width/rw),round(h*height/rh)]
        regions.append({'kind':item['kind'],'box':box,'origin':'reviewed_source_profile'})
    return regions
