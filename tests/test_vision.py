import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vision import VisionModelManager, parse_semantic_response, read_semantic_cache, semantic_cache_key, write_semantic_cache


class VisionTests(unittest.TestCase):
    def test_response_schema_and_evidence_are_validated(self):
        valid = parse_semantic_response('{"category":"ACTION_COMBAT","score":0.8,"reason":"Movement","evidence":[10]}', [10])
        self.assertEqual("ACTION_COMBAT", valid["category"])
        with self.assertRaises(ValueError):
            parse_semantic_response('{"category":"ACTION","score":2,"evidence":[99]}', [10])

    def test_semantic_cache_is_atomic_and_fingerprint_changes_with_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "movie.mp4"
            source.write_bytes(b"video")
            first = semantic_cache_key(source, {"start": 1, "end": 25}, "hello", "en")
            second = semantic_cache_key(source, {"start": 2, "end": 25}, "hello", "en")
            self.assertNotEqual(first, second)
            cache = Path(tmp) / "semantic_cache.json"
            write_semantic_cache(cache, {"schema": 1, "entries": {first: {"score": .8}}})
            self.assertEqual(.8, read_semantic_cache(cache)["entries"][first]["score"])

    def test_manager_loads_only_when_initialize_is_called(self):
        calls = []
        with patch("vision.gpu_memory_usage", return_value=None), \
             patch("vision.VisionModelManager._model_path", return_value=Path("local")):
            manager = VisionModelManager(loader=lambda path, device: (calls.append((path, device)) or (object(), object())))
            self.assertEqual([], calls)
            self.assertEqual("cpu", manager.initialize()["device"])
            self.assertEqual(1, len(calls))
            manager.release()

    def test_cuda_inference_oom_retries_once_on_cpu(self):
        attempts = []
        def loader(_path, device):
            def infer(*_args):
                attempts.append(device)
                if device == "cuda":
                    raise RuntimeError("CUDA out of memory")
                return '{"category":"UNCERTAIN","score":0.1,"reason":"unknown","evidence":[]}'
            return None, infer
        manager = VisionModelManager(loader=loader)
        frame = {"path": Path("unused.jpg"), "timestamp": 0.0}
        with patch("vision.gpu_memory_usage", return_value={"free_mib": 3000, "total_mib": 4094}), \
             patch("vision.VisionModelManager._model_path", return_value=Path("local")):
            output = manager.analyze([frame, {**frame, "timestamp": 1.0}])
        self.assertEqual(["cuda", "cpu"], attempts)
        self.assertEqual("UNCERTAIN", output["category"])
        self.assertEqual("cpu", manager.device)


if __name__ == "__main__":
    unittest.main()
