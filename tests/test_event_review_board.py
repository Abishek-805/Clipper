import tempfile
import unittest
from pathlib import Path

from evaluation.anime_review import SCHEMA, save_dataset
from evaluation.build_event_review import _preserve_existing_annotations


def _dataset(fingerprint="a" * 64, start=10.0, end=20.0):
    candidate = {"candidate_id": "event_0001", "start": start, "end": end,
                 "score": .5, "selected": False, "reviewer_label": None}
    annotation = {"candidate_id": "event_0001", "source_fingerprint": fingerprint,
                  "source_interval": [start, end], "decision": "maybe", "category": "Unclassified",
                  "ratings": {}, "rejection_reasons": [], "notes": "review later",
                  "annotated_at": "2026-11-13T00:00:00Z"}
    return {"schema": SCHEMA, "schema_version": 1,
            "source": {"filename": "episode.mkv", "fingerprint": fingerprint, "duration": 30},
            "candidate_count": 1, "candidates": [candidate], "annotations": [annotation]}


class EventReviewBoardTests(unittest.TestCase):
    def test_existing_human_annotations_are_preserved_for_matching_source_and_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            existing = _dataset()
            save_dataset(output / "anime_highlight_dataset.json", existing)
            generated = _dataset()
            generated["annotations"] = []
            generated["candidates"][0]["event_summary"] = "new automated evidence"
            _preserve_existing_annotations(output, generated)
            self.assertEqual(existing["annotations"], generated["annotations"])
            self.assertEqual("new automated evidence", generated["candidates"][0]["event_summary"])

    def test_changed_source_or_interval_refuses_to_replace_user_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            path = output / "anime_highlight_dataset.json"
            save_dataset(path, _dataset())
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "different source"):
                _preserve_existing_annotations(output, _dataset("b" * 64))
            with self.assertRaisesRegex(ValueError, "candidates changed"):
                _preserve_existing_annotations(output, _dataset(start=11.0, end=20.0))
            self.assertEqual(original, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
