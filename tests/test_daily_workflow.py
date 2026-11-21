import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import clipper
from diagnostics import export_setup_errors
from daily_workflow import byte_range, interval, manual_clip, restore_version, export_delivery
from tests import test_rerender as fixtures

class DailyWorkflowTests(unittest.TestCase):
    def test_export_setup_accepts_cpu_and_identifies_missing_requirements(self):
        checks = [{"name": name, "status": "available"} for name in
                  ("FFmpeg", "FFprobe", "Subtitle filter", "OpenCV", "PySceneDetect")]
        report = {"checks": checks, "encoders": {"libx264": True, "h264_nvenc": False}}
        self.assertEqual(export_setup_errors(report), [])
        checks[1]["status"] = "missing"
        report["encoders"]["libx264"] = False
        errors = export_setup_errors(report)
        self.assertIn("FFprobe", errors)
        self.assertTrue(any("H.264" in error for error in errors))

    def test_manual_cached_transcript_preserves_origin_without_claiming_inference(self):
        cached = clipper.TranscriptionResult("en", [], "no_speech", "cuda", "int8_float16", "transcription cache hit")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _, _ = fixtures.SingleClipRerenderTests().make_job(root)
            with patch("daily_workflow.whisper_transcript", return_value=cached), patch.object(clipper, "render", return_value={}):
                result = manual_clip(source, root / "manual", root / "cache", 1, 0, 1,
                                     "Whisper transcription", False, threading.Event())
            self.assertFalse(result["transcription_executed"])
            self.assertEqual(result["cache"]["transcription"], "reused")
            self.assertEqual(result["transcription_device"], "cuda")

    def test_http_byte_ranges(self):
        for header, expected in [(None,(0,99,200)),('bytes=10-19',(10,19,206)),('bytes=90-',(90,99,206)),('bytes=-10',(90,99,206)),('bytes=-1000',(0,99,206))]:
            self.assertEqual(expected,byte_range(header,100))
        for header in ['bytes=100-','bytes=-0','bytes=-','bytes=-1-2','bytes=5-1','bytes=0-1,4-5','things=1-2']:
            with self.assertRaises(ValueError): byte_range(header,100)

    def test_manual_interval_bounds(self):
        for left,right in [(float('nan'),1),(-1,2),(3,2),(0,181),(0,201)]:
            with self.assertRaises(ValueError): interval(left,right,200)
        self.assertEqual((1,2),interval(1,2,200))

    def test_manual_captionless_render_and_version_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            fixture=fixtures.SingleClipRerenderTests()
            source,_,_=fixture.make_job(root)
            folder=root/"Editor's manual clip"
            with patch.object(clipper,'select_clips',side_effect=AssertionError('Selector must not run')):
                result=manual_clip(source,folder,root/'cache',1,0,1.5,'None',False,threading.Event(),overlays={'hook':'My hook','context':'Context','margin':120})
            self.assertEqual('MANUAL',result['clips'][0]['selection'])
            delivery=export_delivery(folder,'clip_001.mp4',root/'output')
            self.assertTrue((Path(delivery['folder'])/delivery['filename']).is_file())
            self.assertTrue((Path(delivery['folder'])/Path(delivery['filename']).with_suffix('.srt')).is_file())
            again=export_delivery(folder,'clip_001.mp4',root/'output')
            self.assertNotEqual(delivery['filename'],again['filename'])
            original=(folder/'clip_001.mp4').read_bytes()
            clipper.rerender_single_clip(folder,'clip_001.mp4',0,1.8,'1\n00:00:00,000 --> 00:00:01,000\nEdited','bottom-safe',render_preset='fast')
            clipper.rerender_single_clip(folder,'clip_001.mp4',.2,1.8,None,'center-lower',render_preset='fast')
            captions = (folder/'clip_001.srt').read_text(encoding='utf-8')
            self.assertIn('00:00:00,000 --> 00:00:00,800', captions)
            self.assertIn('Edited', captions)
            restored=restore_version(folder,'clip_001.mp4',1)
            self.assertEqual(original,(folder/'clip_001.mp4').read_bytes())
            self.assertEqual(1.5,json.loads((folder/'analysis.json').read_text())['clips'][0]['end'])
            self.assertEqual(3,restored['backup_version'])

    def test_missing_caption_cache_does_not_silently_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source,_,_=fixtures.SingleClipRerenderTests().make_job(root)
            with self.assertRaisesRegex(ValueError,'Matching captions'):
                manual_clip(source,root/'manual',root/'empty-cache',1,0,1,'Whisper transcription',False,threading.Event())
            self.assertFalse((root/'manual'/'clip_001.mp4').exists())

if __name__=='__main__': unittest.main()
