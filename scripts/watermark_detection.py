"""Small, stationary edge watermark detection; captions are not mask candidates."""
import re
import cv2
import numpy as np


def text_candidate(row, width, height):
    text = (row.get('text') or '').strip()
    if float(row.get('conf', -1)) < 70:
        return None
    # Platform names in normal captions are not sufficient evidence.
    if not re.search(r'@[a-z0-9_.]{3,}|(?:www\.|[a-z0-9_-]+\.(?:com|in|net)/)', text, re.I):
        return None
    x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
    edge = x + w <= width * .25 or x >= width * .75 or y + h <= height * .16 or y >= height * .84
    if not edge or w*h > width*height*.015:
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
