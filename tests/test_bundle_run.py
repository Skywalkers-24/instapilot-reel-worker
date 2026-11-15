import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_bundle
from logo_templates import logo_candidates,templates
from PIL import Image


class RunTests(unittest.TestCase):
    def test_ineligible_run_does_not_render_or_upload(self):
        with patch.object(run_bundle,'call',return_value={'status':'OUTSIDE_SLOT'}),patch.object(run_bundle.subprocess,'run') as command:
            run_bundle.main();command.assert_not_called()

    def test_claim_resume_does_not_render_again(self):
        replies=[{'status':'CLAIMED'},{'status':'CREATED'},{'status':'FINISHED'},{'status':'PUBLISHED','media_id':'test'}]
        with patch.object(run_bundle,'call',side_effect=replies),patch.object(run_bundle.subprocess,'run') as command,patch.object(run_bundle.time,'sleep'):
            run_bundle.main();command.assert_not_called()

    def test_unknown_publish_is_not_retried(self):
        with patch.object(run_bundle,'call',return_value={'status':'PUBLISH_UNKNOWN'}) as call:
            with self.assertRaises(SystemExit):run_bundle.main()
            call.assert_called_once()

    def test_empty_background_is_not_a_learned_logo(self):
        self.assertEqual(len(templates()),3)
        self.assertEqual(logo_candidates(Image.new('RGB',(1080,1920),'gray')),[])


if __name__=='__main__':unittest.main()
