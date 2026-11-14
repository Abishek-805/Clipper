import json,socket,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import browser_preview
class BrowserImportTests(unittest.TestCase):
 def setUp(self):
  self.previous=dict(browser_preview._STATE);browser_preview._STATE.clear();browser_preview._STATE.update(state='idle',rendering=False)
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.patch=patch.object(browser_preview,'OUTPUT',self.root);self.patch.start()
  self.server=browser_preview.LocalServer(('127.0.0.1',0),browser_preview.Handler)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.base=f'http://127.0.0.1:{self.server.server_address[1]}'
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join(3);self.patch.stop();self.temp.cleanup()
  browser_preview._STATE.clear();browser_preview._STATE.update(self.previous)
 def test_browser_import_reuses_matching_source_without_native_dialog_or_processing(self):
  source=self.root/'episode.mp4';source.write_bytes(b'known video');job=self.root/'jobs'/'old';job.mkdir(parents=True)
  (job/'analysis.json').write_text(json.dumps({'source_path':str(source)}))
  with patch('source_import.probe'),patch('tkinter.Tk',side_effect=AssertionError('Native dialog must not run')),patch.object(browser_preview,'_run_job',side_effect=AssertionError('Must stay idle')):
   with urlopen(Request(self.base+'/api/import',data=b'known video',headers={'X-Filename':'episode.mp4','Content-Type':'application/octet-stream'}),timeout=3) as response:
    value=json.load(response)
  self.assertTrue(value['reused']);self.assertEqual(str(source.resolve()),value['source'])
  self.assertEqual('idle',browser_preview._STATE['state']);self.assertFalse(browser_preview._STATE['importing'])
 def test_pending_import_does_not_block_status_and_rejects_competing_jobs(self):
  sock=socket.create_connection(self.server.server_address,timeout=3)
  try:
   sock.sendall(b'POST /api/import HTTP/1.0\r\nHost: localhost\r\nContent-Length: 1000\r\nX-Filename: episode.mp4\r\n\r\nx')
   deadline=time.monotonic()+2
   while not browser_preview._STATE.get('importing') and time.monotonic()<deadline:time.sleep(.01)
   with urlopen(self.base+'/api/status',timeout=1) as response:self.assertTrue(json.load(response)['importing'])
   with self.assertRaises(HTTPError) as error:urlopen(Request(self.base+'/api/start',data=b'{}'),timeout=1)
   self.assertEqual(409,error.exception.code);error.exception.close()
   sock.shutdown(socket.SHUT_WR)
   data=b''
   while block:=sock.recv(4096):data+=block
   self.assertIn(b'400',data.splitlines()[0]);self.assertFalse(browser_preview._STATE['importing'])
   self.assertEqual([],list((self.root/'Imports').iterdir()))
  finally:sock.close()
 def test_old_native_picker_route_is_retired(self):
  with self.assertRaises(HTTPError) as error:urlopen(Request(self.base+'/api/pick-file',data=b'{}'),timeout=2)
  self.assertEqual(410,error.exception.code);error.exception.close()
if __name__=='__main__':unittest.main()
