"""Bounded text identity tracking and known graphic watermark detection."""
import re
import cv2
import numpy as np


def text_candidate(row, width, height):
    text = (row.get('text') or '').strip()
    if float(row.get('conf', -1)) < 70:
        return None
    x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
    if re.fullmatch(r'[._]*itzzgoru[._]*',text,re.I) and y>height*.75 and w*h<width*height*.015:
        return {'kind':'creator_text','text':text,'box':[x,y,w,h]}
    if text.upper()=='AF' and x<width*.15 and y<height*.15 and w*h<width*height*.003:
        return {'kind':'creator_text','text':'AF','box':[x,y,w,h]}
    # Platform names in normal captions are not sufficient evidence.
    if not re.search(r'@[a-z0-9_.]{3,}|www\.|\b[a-z0-9_-]+\.(?:com|in|net|co|io)\b', text, re.I):
        return None
    x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
    edge = x + w <= width * .25 or x >= width * .75 or y + h <= height * .16 or y >= height * .84
    explicit = bool(re.search(r"@[a-z0-9_.]{3,}|www\.", text, re.I))
    if (not edge and not explicit) or w*h > width*height*.015:
        return None
    return {'kind':'creator_text', 'text':text, 'box':[x,y,w,h]}


def sparkle_candidates(image):
    """Look for a compact four-point sparkle in the bottom-right source corner."""
    rgb = np.asarray(image.convert('RGB'))
    height, width = rgb.shape[:2]
    x0, y0 = int(width*.76), int(height*.76)
    roi = rgb[y0:, x0:]
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
    saturation = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)[:,:,1]
    contrast = gray.astype(np.float32)-cv2.GaussianBlur(gray,(0,0),12).astype(np.float32)
    mask = ((contrast>12) & (saturation<140) & (gray>105)).astype(np.uint8)*255
    shape = np.array([[50,0],[61,39],[100,50],[61,61],[50,100],[39,61],[0,50],[39,39]],np.int32).reshape(-1,1,2)
    findings=[]
    for contour in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
        x,y,w,h=cv2.boundingRect(contour)
        if not (width*.016 <= w <= width*.09 and .65 <= w/h <= 1.5):
            continue
        fill=cv2.contourArea(contour)/(w*h)
        score=cv2.matchShapes(contour,shape,cv2.CONTOURS_MATCH_I1,0)
        if .15<fill<.65 and score<.3:
            findings.append({'kind':'gemini_sparkle','box':[x+x0,y+y0,w,h], 'shape_score':round(score,4)})
    return findings


def stable_regions(findings, width, height, min_samples=3):
    groups=[]
    for finding in findings:
        x,y,w,h=finding['box'];cx,cy=x+w/2,y+h/2
        for group in groups:
            gx,gy,gw,gh=group[0]['box']
            if group[0]['kind']==finding['kind'] and abs(cx-gx-gw/2)<width*.025 and abs(cy-gy-gh/2)<height*.015:
                group.append(finding)
                break
        else:
            groups.append([finding])
    regions=[]
    for group in groups:
        if len({f['sample'] for f in group})<min_samples:
            continue
        left=max(1,min(f['box'][0] for f in group)-5)
        top=max(1,min(f['box'][1] for f in group)-5)
        right=min(width-2,max(f['box'][0]+f['box'][2] for f in group)+5)
        bottom=min(height-2,max(f['box'][1]+f['box'][3] for f in group)+5)
        if (right-left)*(bottom-top)>width*height*.02:
            continue
        regions.append({'kind':group[0]['kind'],'box':[left,top,right-left,bottom-top],
                        'samples':len({f['sample'] for f in group})})
    if sum(r['box'][2]*r['box'][3] for r in regions)>width*height*.04:
        return []
    return regions


def youtube_candidates(image):
    """Recognize small red YouTube play badges, not arbitrary scene rectangles."""
    rgb=np.asarray(image.convert('RGB'));height,width=rgb.shape[:2]
    hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    red=(((hsv[:,:,0]<12)|(hsv[:,:,0]>170))&(hsv[:,:,1]>150)&(hsv[:,:,2]>120)).astype(np.uint8)*255
    findings=[]
    for contour in cv2.findContours(red,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
        x,y,w,h=cv2.boundingRect(contour)
        if not (18<=w<=width*.12 and 1.2<w/max(h,1)<2.2 and w*h<width*height*.01):continue
        if not (y+h<height*.2 or y>height*.8 or x+w<width*.2 or x>width*.8):continue
        crop=rgb[y:y+h,x:x+w]
        white=(crop.min(axis=2)>200).astype(np.uint8)*255
        triangles=cv2.findContours(white,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
        for triangle in triangles:
            area=cv2.contourArea(triangle)
            approx=cv2.approxPolyDP(triangle,.07*cv2.arcLength(triangle,True),True)
            if len(approx)==3 and .07*w*h<area<.35*w*h:
                findings.append({'kind':'youtube_badge','box':[x,y,w,h]});break
    return findings


def timed_text_regions(findings,width,height,step,duration,min_samples=3):
    """Follow explicit text identity with time-limited regions instead of a union."""
    tracks={}
    for finding in findings:
        if finding['kind']!='creator_text':continue
        identity=re.sub(r'\s+','',finding['text']).lower()
        tracks.setdefault(identity,[]).append(finding)
    regions=[]
    for identity,track in tracks.items():
        if len({f['sample'] for f in track})<min_samples:continue
        for f in track:
            x,y,w,h=f['box']
            x1=max(1,x-10);y1=max(1,y-8)
            x2=min(width-2,x+w+10);y2=min(height-2,y+h+8)
            if (x2-x1)*(y2-y1)>width*height*.02:continue
            time=f['time']
            regions.append({'kind':'creator_text','box':[x1,y1,x2-x1,y2-y1],
                            'start':max(0,time-step/2),'end':min(duration,time+step/2),
                            'samples':len({p['sample'] for p in track})})
    # Bound simultaneous coverage, not the area covered by all time windows.
    for f in findings:
        time=f.get('time',0)
        if sum(r['box'][2]*r['box'][3] for r in regions if r['start']<=time<r['end'])>width*height*.04:
            return []
    return regions


def exclusion_reason(findings,width,height):
    """Skip recognized moving branding rather than chasing it with masks."""
    tracks={}
    for finding in findings:
        text=finding.get('text','').strip().lower()
        if finding['kind']=='creator_text':
            tracks.setdefault(text,[]).append(finding)
    for text,track in tracks.items():
        if len({f['sample'] for f in track})<3:continue
        xs=[f['box'][0]+f['box'][2]/2 for f in track]
        ys=[f['box'][1]+f['box'][3]/2 for f in track]
        if max(xs)-min(xs)>width*.06 or max(ys)-min(ys)>height*.04:
            return 'Moving or switching creator watermark'
    return None
