import io,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import clipper
import gpu_analysis
from scenedetect import ContentDetector  # Load dependency discovery before mocking decoder processes.
from diagnostics import gpu_activity

class FakeDecoder:
 def __init__(self,data):self.stdout=io.BytesIO(data);self.done=False;self.killed=False
 def wait(self,timeout=None):self.done=True;return 0
 def poll(self):return 0 if self.done else None
 def kill(self):self.killed=True;self.done=True
 def terminate(self):self.done=True

class GpuAnalysisTests(unittest.TestCase):
 def test_one_gpu_stream_produces_motion_and_timestamped_cuts(self):
  import numpy as np
  red=np.zeros((180,320,3),dtype=np.uint8);red[:,:,2]=255
  blue=np.zeros_like(red);blue[:,:,0]=255
  decoder=FakeDecoder(red.tobytes()*8+blue.tobytes()*8)
  with patch('gpu_analysis.subprocess.Popen',return_value=decoder) as launch,patch('gpu_analysis.ffmpeg_path',return_value='ffmpeg'):
   motion,cuts,status,stats=gpu_analysis.visual_signals(Path('sample.mkv'),2,24,threading.Event(),lambda _:None)
  self.assertEqual([1.0],cuts);self.assertGreater(max(motion),0);self.assertEqual(16,stats['frames_decoded']);self.assertEqual(4,stats['frames_sampled'])
  self.assertEqual('cuda-nvdec',stats['device']);self.assertIn('-hwaccel',launch.call_args.args[0]);self.assertIn('scale_cuda',str(launch.call_args.args[0]));self.assertTrue(decoder.stdout.closed)
 def test_truncated_stream_is_rejected_and_decoder_is_stopped(self):
  decoder=FakeDecoder(b'bad frame')
  with patch('gpu_analysis.subprocess.Popen',return_value=decoder),patch('gpu_analysis.ffmpeg_path',return_value='ffmpeg'):
   with self.assertRaisesRegex(RuntimeError,'incomplete'):gpu_analysis.visual_signals(Path('sample.mkv'),2,24,threading.Event(),lambda _:None)
  self.assertTrue(decoder.killed);self.assertTrue(decoder.stdout.closed)
 def test_cancel_does_not_fall_back_to_cpu(self):
  with patch('clipper._gpu_visual_signals',side_effect=InterruptedError('cancelled')),patch('clipper._motion') as cpu:
   with self.assertRaises(InterruptedError):clipper._visual_analysis(Path('x'),clipper.VideoInfo(2,320,180,24,False,0),threading.Event(),lambda _:None)
   cpu.assert_not_called()
 def test_gpu_failure_has_explicit_cpu_fallback(self):
  messages=[]
  with patch.dict('os.environ',{'CLIPPER_VISUAL_DEVICE':'auto'}),patch('clipper._gpu_visual_signals',side_effect=RuntimeError('unsupported codec')),patch('clipper._motion',return_value=[1,2]),patch('clipper._scene_cuts',return_value=([1.0],'one cut')):
   motion,cuts,_,stats=clipper._visual_analysis(Path('x'),clipper.VideoInfo(2,320,180,24,False,0),threading.Event(),messages.append)
  self.assertEqual('cpu',stats['device']);self.assertIn('unsupported codec',stats['fallback_reason']);self.assertTrue(any('CPU fallback' in text for text in messages));self.assertEqual([1.0],cuts)
 def test_telemetry_identifies_system_scope_and_unavailable_fields(self):
  with patch('diagnostics.subprocess.run',return_value=SimpleNamespace(stdout='RTX 3050, 1100, 4094, 7, 45, [N/A]\n')):
   result=gpu_activity()
  self.assertEqual('system',result['scope']);self.assertEqual(45,result['encoder_percent']);self.assertIsNone(result['decoder_percent'])
  with patch('diagnostics.subprocess.run',side_effect=FileNotFoundError('missing')):self.assertFalse(gpu_activity()['available'])
if __name__=='__main__':unittest.main()
