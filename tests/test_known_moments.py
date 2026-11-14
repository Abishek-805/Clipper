import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import clipper
from known_moments import load_decisions, load_review, preview, propose, save_decision


SOURCE = "a" * 64


def sample():
    template = dict(source_hash=SOURCE, pending_owner_descriptions=[dict(label="Training", owner_description="hero and friend training conversation")])
    cues = [dict(start=10., end=20., text="Activate the sword skill and move."),
            dict(start=23., end=29., text="Use the initial motion to attack.")]
    return propose(template, cues, 100, SOURCE)


class KnownMomentTests(unittest.TestCase):
    def test_text_matches_are_proposed_and_wrong_sources_ignored(self):
        proposals = sample()
        self.assertTrue(proposals)
        self.assertLessEqual(len(proposals), 3)
        self.assertTrue(all(p["status"] == "PROPOSED" and p["quotes"] for p in proposals))
        self.assertEqual(proposals, sample())
        self.assertEqual(propose(dict(source_hash="b"*64), [], 100, SOURCE), [])

    def test_confirm_reject_edit_and_reload(self):
        proposals = sample()
        identity = proposals[0]["id"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "known.json"
            save_decision(path, SOURCE, proposals, identity, "confirm", 10.123, 28.456, 100)
            self.assertEqual(load_decisions(path, SOURCE)[identity]["start"], 10.123)
            self.assertEqual(len(json.loads(path.read_text())["moments"]), 1)
            self.assertEqual(load_decisions(path, "b"*64), {})
            save_decision(path, SOURCE, proposals, identity, "reject", 10.123, 28.456, 100)
            self.assertEqual(json.loads(path.read_text())["moments"], [])
            result = save_decision(path, SOURCE, proposals, identity, "edit", 11, 28, 100)
            self.assertEqual(result[identity]["status"], "PROPOSED")
            self.assertEqual(result[identity]["rough_start"], proposals[0]["start"])

    def test_invalid_actions_bounds_and_source_cannot_save(self):
        proposals = sample()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "known.json"
            for action, start, end, source in (("automatic", 10, 20, SOURCE), ("confirm", 20, 10, SOURCE),
                                             ("confirm", float("nan"), 20, SOURCE), ("confirm", 10, 20, "b"*64)):
                with self.assertRaises(ValueError):
                    save_decision(path, source, proposals, proposals[0]["id"], action, start, end, 100)
            self.assertFalse(path.exists())

    def test_missing_cached_words_never_start_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"template.json").write_text(json.dumps(dict(source_hash=SOURCE, pending_owner_descriptions=[])))
            info = clipper.VideoInfo(100, 640, 360, 24, True, 0, [dict(index=1, type="audio")])
            structure = dict(source_hash=SOURCE, duration=100, active=[], tracks=[])
            result = clipper.TranscriptionResult(None, [], "unavailable", "none", "none")
            with patch.object(clipper, "probe", return_value=info), patch("known_moments.prepare_structure", return_value=structure), \
                 patch.object(clipper, "transcribe", return_value=result) as transcribe:
                with self.assertRaisesRegex(ValueError, "never starts transcription"):
                    load_review(root/"source.mkv", root, root, 1, root)
                self.assertFalse(transcribe.call_args.kwargs["allow_inference"])

    def test_preview_is_valid_cached_and_cancel_safe(self):
        from tests.test_rerender import SingleClipRerenderTests
        from evaluation.build_review_board import validate_preview
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _, _ = SingleClipRerenderTests().make_job(root)
            info = clipper.probe(source)
            folder = root/"review"
            proposal = dict(source_hash=SOURCE)
            output = preview(source, folder, proposal, 0, 1.5, info, 1, threading.Event())
            self.assertTrue(validate_preview(output, 1, None)["audio_available"])
            original = output.read_bytes()
            modified = output.stat().st_mtime_ns
            self.assertEqual(output, preview(source, folder, proposal, 0, 1.5, info, 1, threading.Event()))
            self.assertEqual(output.stat().st_mtime_ns, modified)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(InterruptedError):
                preview(source, folder, proposal, .1, 1.5, info, 1, cancel)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(folder.glob("*.mp4")), [output])

    def test_saved_reference_survives_changed_text_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            templates = root/"templates"
            templates.mkdir()
            (templates/"template.json").write_text(json.dumps(dict(source_hash=SOURCE, pending_owner_descriptions=[])))
            path = root/"Review"/SOURCE/"known-moments.json"
            p = sample()
            save_decision(path, SOURCE, p, p[0]["id"], "confirm", 11, 28, 100)
            info = clipper.VideoInfo(100, 640, 360, 24, True, 0, [dict(index=1, type="audio")])
            structure = dict(source_hash=SOURCE, duration=100, active=[], tracks=[])
            transcript = clipper.TranscriptionResult("en", [clipper.Word(1, 2, "Different text.")], "transcribed", "cache", "none")
            with patch.object(clipper, "probe", return_value=info), patch("known_moments.prepare_structure", return_value=structure), \
                 patch.object(clipper, "transcribe", return_value=transcript):
                data = load_review(root/"source.mkv", root, root, 1, templates)
            self.assertEqual(data["confirmed_count"], 1)
            self.assertEqual(data["proposals"][0]["id"], p[0]["id"])

    def test_explicit_owner_template_is_imported_without_losing_other_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            templates = root/"templates"
            templates.mkdir()
            p = sample()
            saved = dict(id=p[0]["id"], source_hash=SOURCE, label="Training", start=11, end=28,
                         rough_start=10, rough_end=29, status="CONFIRMED")
            (templates/"template.json").write_text(json.dumps(dict(source_hash=SOURCE, moments=[saved], pending_owner_descriptions=[])))
            info = clipper.VideoInfo(100, 640, 360, 24, True, 0, [dict(index=1, type="audio")])
            structure = dict(source_hash=SOURCE, duration=100, active=[], tracks=[])
            transcript = clipper.TranscriptionResult("en", [clipper.Word(1, 2, "Different text.")], "transcribed", "cache", "none")
            with patch.object(clipper, "probe", return_value=info), patch("known_moments.prepare_structure", return_value=structure), \
                 patch.object(clipper, "transcribe", return_value=transcript):
                data = load_review(root/"source.mkv", root, root, 1, templates)
            self.assertEqual(data["confirmed_count"], 1)
            self.assertEqual(load_decisions(root/"Review"/SOURCE/"known-moments.json", SOURCE)[saved["id"]], saved)

    def test_http_decisions_source_guard_ranges_and_reservation(self):
        import browser_preview as browser
        previous = dict(browser._STATE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/"source.mkv"
            source.write_bytes(b"source")
            proposals = sample()
            folder = root/"Review"/SOURCE
            data = dict(source_hash=SOURCE, duration=100, proposals=proposals, decisions={}, folder=str(folder), confirmed_count=0)
            server = browser.LocalServer(("127.0.0.1", 0), browser.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            def post(action, source_hash=SOURCE):
                payload = dict(source=str(source), audio_index=1, action=action, source_hash=source_hash,
                               proposal_id=proposals[0]["id"], start=10, end=25)
                return urlopen(Request(base+"/api/known-moments", data=json.dumps(payload).encode()))
            try:
                browser._STATE.clear()
                browser._STATE.update(state="idle", rendering=False)
                with patch.object(browser, "OUTPUT", root), patch.object(browser, "_probe_source", return_value=(source, clipper.VideoInfo(100, 640, 360, 24, True, 0))), \
                     patch.object(browser, "load_known_review", return_value=data):
                    with self.assertRaises(HTTPError) as error:
                        post("confirm", "b"*64)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
                    self.assertFalse((folder/"known-moments.json").exists())
                    with post("confirm") as response:
                        self.assertEqual(json.load(response)["confirmed_count"], 1)
                    request = Request(base+f"/review-files/{SOURCE}/known-moments.json", headers={"Range": "bytes=0-9"})
                    with urlopen(request) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(len(response.read()), 10)
                    browser._STATE["reviewing"] = True
                    with self.assertRaises(HTTPError) as error:
                        post("load")
                    self.assertEqual(error.exception.code, 409)
                    error.exception.close()
                    self.assertTrue(browser._STATE["reviewing"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)
                browser._STATE.clear()
                browser._STATE.update(previous)
