import json
import unittest
from unittest.mock import patch

from evaluation.text_scenes import audit_response, benchmark_metrics, request_scene, scene_lines
from evaluation.benchmark_text_scenes import clipper_busy


class TextSceneTests(unittest.TestCase):
    scene = dict(id="SC_0001", utterance_ids=["U_0001"])
    lines = [dict(id="U_0001", text="Klein said hello.")]

    def response(self, **changes):
        return json.dumps(dict(scene_id="SC_0001", summary="Klein said hello.", names=["Klein"], tones=["other"],
                               references=[dict(utterance_id="U_0001", quote="Klein said hello.")], **changes))

    def test_literal_checks_do_not_claim_fact_accuracy(self):
        result = audit_response(self.response(), self.scene, self.lines)
        self.assertEqual(result["errors"], [])
        metrics = benchmark_metrics([dict(status="executed", audit=result)])
        self.assertEqual(metrics["json_valid_rate"], 1)
        self.assertEqual(metrics["valid_id_rate"], 1)
        self.assertEqual(metrics["unsupported_name_rate"], 0)
        self.assertIsNone(metrics["invented_fact_rate"])

    def test_bad_ids_names_quotes_are_recorded_not_repaired(self):
        data = json.loads(self.response())
        data.update(scene_id="SC_9999", names=["Kirito"], references=[dict(utterance_id="U_9999", quote="Invented")])
        result = audit_response(json.dumps(data), self.scene, self.lines)
        self.assertEqual(len(result["errors"]), 3)
        self.assertEqual(result["supported_names"], 0)
        self.assertEqual(result["supported_quotes"], 0)

    def test_bad_json_and_field_types(self):
        for raw in ("not JSON", "[]", "{}", self.response().replace('"names": ["Klein"]', '"names": 7')):
            self.assertTrue(audit_response(raw, self.scene, self.lines)["errors"])

    def test_unclear_and_zero_denominators(self):
        data = json.loads(self.response())
        data.update(summary="Unclear", names=[], references=[])
        result = audit_response(json.dumps(data), self.scene, self.lines)
        self.assertEqual(result["errors"], [])
        self.assertIsNone(benchmark_metrics([])["json_valid_rate"])
        self.assertIsNone(benchmark_metrics([dict(status="executed", audit=result)])["unsupported_name_rate"])

    def test_scene_line_isolation(self):
        units = dict(utterances=self.lines + [dict(id="U_0002", text="Unrelated")])
        self.assertEqual(scene_lines(self.scene, units), self.lines)

    def test_network_guard_and_no_model_start(self):
        with patch("evaluation.text_scenes.build_opener", side_effect=AssertionError("No external requests")):
            with self.assertRaises(ValueError):
                request_scene("https://example.com/v1/chat/completions", "model", self.scene, self.lines)
        with self.assertRaises(ValueError):
            clipper_busy(["http://example.com/api/status"])

    def test_active_clipper_prevents_model_work(self):
        with patch("evaluation.benchmark_text_scenes.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"state":"idle","model_status":{"loaded":true}}'
            self.assertTrue(clipper_busy(["http://127.0.0.1:8770/api/status"]))
            response.read.return_value = b'{"state":"idle","model_status":null}'
            self.assertFalse(clipper_busy(["http://127.0.0.1:8770/api/status"]))
