from __future__ import annotations
import copy
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from app.vision.onnx_shape import fixed_image_shape
from app.vision.yolo_sensor import YOLOSensor
from app.core.semantic_resolver import SemanticResolver
from app.core.object_memory import ObjectMemory
from app.core.game_state import VisualObject, HUDState
from app.core.fast_policy import FastPolicy
from app.profiles.runtime_settings import ensure_runtime_settings

ROOT = Path(__file__).resolve().parents[1]
RULES = {"monster": {"type": "monster", "relation": "hostile", "min_confidence": 0.6}}


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        (self.directory / "object_memory.json").write_text(
            '{"version":3,"objects":[]}', encoding="utf-8"
        )
        self.memory = ObjectMemory(str(self.directory / "object_memory.json"))
        self.frame = np.full((100, 100, 3), 120, dtype=np.uint8)

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_rule_recognizes_monster_without_appearance_memory(self):
        resolver = SemanticResolver(self.memory, RULES)
        obj = resolver.resolve(
            [VisualObject(0, "monster", (10, 10, 30, 40), 0.9)], self.frame
        )[0]
        self.assertEqual(
            (obj.object_type, obj.relation, obj.status),
            ("monster", "hostile", "confirmed"),
        )
        self.assertIsNone(obj.memory_id)
        action = FastPolicy().decide(
            [obj], HUDState(health=80, health_valid=True), {}, self.frame.shape
        )
        self.assertEqual(action.action_type, "ATTACK")
        self.assertEqual(action.track_id, obj.track_id)

    def test_low_confidence_and_unmapped_classes_remain_unknown(self):
        resolver = SemanticResolver(self.memory, RULES)
        for label, confidence in (("monster", 0.3), ("person", 0.95)):
            obj = resolver.resolve(
                [VisualObject(0, label, (10, 10, 30, 40), confidence)], self.frame
            )[0]
            self.assertEqual(obj.status, "unknown")
        self.assertEqual(self.memory.object_count, 0)

    def test_locked_appearance_override_wins_over_profile_class(self):
        resolver = SemanticResolver(self.memory, RULES)
        with patch.object(
            self.memory, "find_prototype_fast", return_value=("npc", 0.95, 1)
        ), patch.object(
            self.memory,
            "resolve_semantic",
            return_value=("npc", "friendly", 0.95, True),
        ), patch.object(
            self.memory,
            "get_memory",
            return_value={"locked": True, "label": "npc", "relation": "friendly"},
        ):
            obj = resolver.resolve(
                [VisualObject(0, "monster", (10, 10, 30, 40), 0.95)], self.frame
            )[0]
        self.assertEqual((obj.object_type, obj.relation), ("npc", "friendly"))

    def test_semantic_gating_off_rescans_confirmed_tracks(self):
        resolver = SemanticResolver(self.memory, RULES)
        detections = [VisualObject(0, "monster", (10, 10, 30, 40), 0.95)]
        resolver.resolve(detections, self.frame)
        # Real tracker timestamps, moved back to simulate stable observation.
        for track in resolver.tracker._tracks.values():
            track.first_seen -= 1
        objects = resolver.resolve(detections, self.frame)
        self.assertEqual(len(resolver.candidates(objects)[0]), 0)
        self.assertEqual(len(resolver.candidates(objects, only_unknown=False)[0]), 1)

    def test_fixed_onnx_uses_sibling_pt_for_requested_size(self):
        model = ROOT / "yolo/models/best.onnx"
        self.assertEqual(fixed_image_shape(model), (512, 512))
        loader = Mock()
        with patch.dict(
            "sys.modules", {"ultralytics": types.SimpleNamespace(YOLO=loader)}
        ):
            sensor = YOLOSensor(enabled=True, model_path=str(model), image_size=448)
        loader.assert_called_once_with(str(model.with_suffix(".pt")))
        self.assertEqual(sensor.image_size, 448)
        self.assertEqual(sensor.active_model_path, str(model.with_suffix(".pt")))

    def test_class_aware_dedup_retains_overlapping_item(self):
        sensor = YOLOSensor()
        objects = sensor._deduplicate(
            [
                VisualObject(0, "monster", (10, 10, 30, 40), 0.9),
                VisualObject(1, "monster", (10, 10, 30, 40), 0.8),
                VisualObject(2, "item", (10, 10, 30, 40), 0.7),
            ]
        )
        self.assertEqual([o.object_type for o in objects], ["monster", "item"])

    def test_migration_preserves_saved_tuning_and_existing_rules(self):
        directory = self.directory / "diablo4"
        directory.mkdir()
        ensure_runtime_settings(directory)
        p = directory / "vision.json"
        doc = json.loads(p.read_text())
        self.assertIn("monster", doc["class_rules"])
        doc["capture_fps"] = 45
        doc["class_rules"] = {}
        p.write_text(json.dumps(doc))
        ensure_runtime_settings(directory)
        result = json.loads(p.read_text())
        self.assertEqual(result["capture_fps"], 45)
        self.assertEqual(result["class_rules"], {})
