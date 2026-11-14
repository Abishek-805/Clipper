import unittest
from unittest.mock import patch

from clipper import select_clips


class ZoneSelectionTests(unittest.TestCase):
    def select(self, zones, enabled=True):
        motion = [0.]*600
        motion[100:115] = [8.]*15
        motion[300:315] = [8.]*15
        return select_clips(600, motion, [0.]*600, [0, 100, 115, 300, 315, 600], [],
                            count=2, min_length=20, max_length=40, zones=zones, zone_filter_enabled=enabled)

    def test_hard_exclusions_and_legacy_switch(self):
        zones = [dict(id="all", start=0, end=600, level="HARD", label="OP", reason="owner")]
        report = self.select(zones)
        self.assertEqual(report["selected"], [])
        self.assertTrue(any("structure zone" in r["reason"] for r in report["rejections"]))
        self.assertTrue(self.select(zones, False)["selected"])

    def test_soft_only_remains_eligible(self):
        zones = [dict(id="all", start=0, end=600, level="SOFT", label="OP", reason="position")]
        self.assertTrue(self.select(zones)["selected"])
        self.assertTrue(all(c["zone_warnings"] for c in self.select(zones)["selected"]))

    def test_clean_candidates_preferred_even_after_rescore(self):
        from clipper import select_diverse_candidates
        features = dict(average_motion=1, average_audio_energy=1, speech_density=0, dialogue_completeness=0)
        clean = dict(start=100, end=130, score=.3, feature_scores=features)
        flagged = dict(start=200, end=230, score=.99, feature_scores=features, zone_warnings=["soft"])
        selected, _ = select_diverse_candidates([flagged, clean], 2, 600)
        self.assertEqual(selected, [clean])

    def test_boundary_expansion_rechecked(self):
        raw = dict(start=10, end=30)
        refined = dict(start=10, end=45, boundary_valid=True, score=.8)
        with patch("clipper.generate_candidates", return_value=dict(candidates=[raw], rejections=[])), \
             patch("clipper.rank_candidates", return_value=[refined]):
            zones = [dict(id="z", start=40, end=60, level="VERIFIED", label="song", reason="karaoke")]
            self.assertEqual(self.select(zones)["selected"], [])
