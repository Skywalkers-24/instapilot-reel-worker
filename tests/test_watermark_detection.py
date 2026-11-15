import sys
from pathlib import Path
import unittest
from PIL import Image, ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from watermark_detection import text_candidate, sparkle_candidates, stable_regions, timed_text_regions, youtube_candidates
from brand_overlay import create_brand_overlay


def row(text, x=20, y=20, w=150, h=25, confidence=95):
    return {'text':text,'left':str(x),'top':str(y),'width':str(w),'height':str(h),'conf':str(confidence)}


class DetectionTests(unittest.TestCase):
    def test_brand_is_small_and_has_transparent_rounded_corners(self):
        brand=create_brand_overlay()
        self.assertEqual(brand.mode,'RGBA')
        self.assertEqual(brand.height,52)
        self.assertLess(brand.width,300)
        self.assertEqual(brand.getpixel((0,0))[3],0)
        self.assertGreater(brand.getchannel('A').getextrema()[1],200)
        for red,green,blue,alpha in brand.getdata():
            if alpha:
                self.assertEqual(red,green)
                self.assertEqual(green,blue)

    def test_caption_and_platform_mentions_are_preserved(self):
        for text in ['My happiness forever','Follow your dreams','youtube','instagram','TVS']:
            self.assertIsNone(text_candidate(row(text),1080,1920))
        self.assertIsNotNone(text_candidate(row('@someone',x=400,y=950),1080,1920))

    def test_handle_requires_edge_confidence_and_small_area(self):
        self.assertIsNotNone(text_candidate(row('@someone'),1080,1920))
        self.assertIsNone(text_candidate(row('@someone',confidence=40),1080,1920))
        self.assertIsNone(text_candidate(row('@someone',w=900,h=100),1080,1920))

    def test_single_frame_detection_is_not_masked(self):
        findings=[{'kind':'creator_text','box':[20,20,100,25],'sample':0}]*12
        self.assertEqual(stable_regions(findings,1080,1920),[])

    def test_moving_marks_do_not_create_large_permanent_mask(self):
        findings=[{'kind':'creator_text','box':[10+i*100,20,100,25],'sample':i} for i in range(8)]
        self.assertEqual(stable_regions(findings,1080,1920),[])

    def test_moving_identity_gets_separate_timed_regions(self):
        findings=[{'kind':'creator_text','text':'@someone','box':[50+i*180,200,120,30],
                   'sample':i,'time':i+.5} for i in range(4)]
        regions=timed_text_regions(findings,1080,1920,1,4)
        self.assertEqual(len(regions),4)
        self.assertTrue(all(r['box'][2]<160 for r in regions))
        self.assertEqual(regions[0]['end'],1)
        self.assertEqual(regions[-1]['start'],3)

    def test_unrelated_handles_do_not_form_one_track(self):
        findings=[{'kind':'creator_text','text':f'@person{i}','box':[50,20,120,30],
                   'sample':i,'time':i+.5} for i in range(4)]
        self.assertEqual(timed_text_regions(findings,1080,1920,1,4),[])

    def test_youtube_badge_requires_white_play_triangle(self):
        image=Image.new('RGB',(1080,1920),'gray')
        draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((20,20,100,70),radius=10,fill=(240,0,0))
        self.assertFalse(youtube_candidates(image))
        draw.polygon([(48,30),(48,60),(75,45)],fill='white')
        self.assertEqual(len(youtube_candidates(image)),1)

    def test_stationary_mark_gets_one_small_region(self):
        findings=[{'kind':'gemini_sparkle','box':[860+i%2,1700,70,70],'sample':i} for i in range(8)]
        regions=stable_regions(findings,1080,1920)
        self.assertEqual(len(regions),1)
        self.assertLess(regions[0]['box'][2]*regions[0]['box'][3],1080*1920*.01)

    def test_sparkle_is_detected_but_rectangle_is_not(self):
        image=Image.new('RGB',(1080,1920),(100,70,40))
        draw=ImageDraw.Draw(image)
        points=[(900,1700),(909,1726),(935,1735),(909,1744),(900,1770),(891,1744),(865,1735),(891,1726)]
        draw.polygon(points,fill=(190,175,150))
        self.assertTrue(sparkle_candidates(image))
        rectangle=Image.new('RGB',(1080,1920),(100,70,40))
        ImageDraw.Draw(rectangle).rectangle((865,1700,935,1770),fill='white')
        self.assertFalse(sparkle_candidates(rectangle))


if __name__=='__main__':
    unittest.main()
