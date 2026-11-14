import io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from source_import import import_source
class SourceImportTests(unittest.TestCase):
 def test_known_source_reused_only_after_content_verification(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);original=root/'episode.mkv';original.write_bytes(b'known bytes')
   with patch('source_import.probe'):
    result=import_source(io.BytesIO(b'known bytes'),11,'episode.mkv',root/'imports',[original])
   self.assertTrue(result['reused']);self.assertEqual(str(original.resolve()),result['source']);self.assertEqual([],list((root/'imports').iterdir()))
   self.assertEqual(b'known bytes',original.read_bytes())
 def test_new_source_imports_atomically_and_deduplicates(self):
  with tempfile.TemporaryDirectory() as tmp,patch('source_import.probe'):
   root=Path(tmp);data=b'new video';a=import_source(io.BytesIO(data),len(data),'episode.mkv',root)
   b=import_source(io.BytesIO(data),len(data),'episode.mkv',root)
   self.assertEqual(a['source'],b['source']);self.assertEqual(data,Path(a['source']).read_bytes())
 def test_same_name_and_size_different_content_does_not_reuse_original(self):
  with tempfile.TemporaryDirectory() as tmp,patch('source_import.probe'):
   root=Path(tmp);original=root/'episode.mp4';original.write_bytes(b'old')
   result=import_source(io.BytesIO(b'new'),3,'episode.mp4',root/'imports',[original])
   self.assertFalse(result['reused']);self.assertNotEqual(str(original),result['source']);self.assertEqual(b'old',original.read_bytes())
 def test_interrupted_and_corrupt_uploads_leave_no_partial_media(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   with self.assertRaisesRegex(ValueError,'interrupted'):import_source(io.BytesIO(b'x'),5,'clip.mp4',root)
   with patch('source_import.probe',side_effect=ValueError('corrupt')),self.assertRaisesRegex(ValueError,'corrupt'):import_source(io.BytesIO(b'bad'),3,'clip.mp4',root)
   self.assertEqual([],list(root.iterdir()))
 def test_invalid_names_empty_files_and_extensions_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   for name,length in [('../video.mp4',1),('folder\\video.mkv',1),('script.exe',1),('clip.mp4',0)]:
    with self.assertRaises(ValueError):import_source(io.BytesIO(b'x'),length,name,Path(tmp))
if __name__=='__main__':unittest.main()
