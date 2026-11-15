"""Match reviewed logo edge signatures across sources, positions and scales."""
import base64
from functools import lru_cache
import json
from pathlib import Path
import zlib
import cv2
import numpy as np


@lru_cache(maxsize=1)
def templates():
    data=json.loads((Path(__file__).resolve().parents[1]/'data/watermark_templates.json').read_text())
    result=[]
    for item in data['templates']:
        w,h=item['size'];raw=zlib.decompress(base64.b64decode(item['edge_bits_zlib']))
        edges=np.unpackbits(np.frombuffer(raw,dtype=np.uint8))[:w*h].reshape(h,w)*255
        result.append((item,edges))
    return result


def logo_candidates(image):
    gray=cv2.cvtColor(np.asarray(image.convert('RGB')),cv2.COLOR_RGB2GRAY);height,width=gray.shape
    findings=[]
    for item,reference in templates():
        x0,y0,x1,y1=item['search_roi'];x0=int(x0*width);x1=int(x1*width);y0=int(y0*height);y1=int(y1*height)
        search=cv2.Canny(gray[y0:y1,x0:x1],15,45)
        best=None
        for scale in [.8,.9,1,1.1,1.2]:
            w=round(reference.shape[1]*scale);h=round(reference.shape[0]*scale)
            if w>=search.shape[1] or h>=search.shape[0]:continue
            template=cv2.resize(reference,(w,h),interpolation=cv2.INTER_NEAREST)
            score_map=cv2.matchTemplate(search,template,cv2.TM_CCOEFF_NORMED)
            _,score,_,position=cv2.minMaxLoc(score_map)
            if score>=item['min_score'] and (best is None or score>best['score']):
                best={'kind':item['kind'],'box':[x0+position[0],y0+position[1],w,h],'score':score}
        if best:findings.append(best)
    return findings
