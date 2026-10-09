"""YOLO tracks -> appearance memory. Only unresolved identities need Qwen."""

from __future__ import annotations
import copy
import math
import threading
import time
from app.core.detection import Detection
from app.core.game_state import VisualObject
from app.core.object_tracker import ObjectTracker
from app.profiles.profile_store import digest


class SemanticResolver:
    def __init__(self, memory, class_rules=None):
        self.memory = memory
        self.class_rules = class_rules or {}
        self.tracker = ObjectTracker(max_lost_seconds=1.5, max_match_distance=180)
        self.lock = threading.RLock()
        self.tracks = {}
        self.retry_at = {}
        self.rule_hits = {}
        self.changed = False
        self.frame = None
        self.friendly_names = set()

    def protect_named_ally(self, obj):
        if obj.name and obj.name.casefold().strip() in self.friendly_names:
            obj.object_type, obj.relation, obj.status = 'npc', 'friendly', 'confirmed'
            obj.semantic_confidence = max(.8, obj.semantic_confidence)
        return obj

    def reset(self):
        with self.lock:
            self.tracker.reset()
            self.memory.clear_track_cache()
            self.tracks = {}
            self.retry_at = {}
            self.rule_hits = {}

    def resolve(self, detections, frame):
        with self.lock:
            self.frame = frame
            self.changed = self.memory.reload_if_changed()
            parsed = []
            for o in detections:
                x1, y1, x2, y2 = map(int, o.bbox)
                parsed.append(
                    Detection(
                        0,
                        o.object_type,
                        o.confidence,
                        x1,
                        y1,
                        x2,
                        y2,
                        (x1 + x2) // 2,
                        (y1 + y2) // 2,
                        x2 - x1,
                        y2 - y1,
                    )
                )
            tracks = self.tracker.update(parsed)
            self.tracks = {t.track_id: copy.deepcopy(t) for t in tracks}
            # Keep identity/retry evidence through short occlusions, but return
            # only observed targets: cached coordinates must never be attacked.
            live = self.tracker.retained_ids
            for tid in list(self.memory._track_labels):
                if tid not in live:
                    self.memory.forget_track(tid)
            self.retry_at = {k: v for k, v in self.retry_at.items() if k in live}
            self.rule_hits = {k:v for k,v in self.rule_hits.items() if k in live}
            objects = []
            for t in tracks:
                # Recheck appearance, even for a stable tracker ID; position is not identity.
                hit = self.memory.find_prototype_fast(t, frame)
                if hit is None:
                    self.memory.forget_track(t.track_id)
                    label, relation, confidence, mid, status, name = (
                        "unknown",
                        "unknown",
                        0,
                        None,
                        "unknown",
                        "",
                    )
                else:
                    label, score, mid = hit
                    label, relation, confidence, locked = self.memory.resolve_semantic(
                        mid
                    )
                    confidence = min(confidence, score)
                    record = self.memory.get_memory(mid) or {}
                    status = (
                        "confirmed" if locked else record.get("status", "confirmed")
                    )
                    name = record.get("name", "")
                    self.memory.remember_track(t.track_id, label, confidence, mid)
                d = t.detection
                rule = self.class_rules.get(d.class_name)
                record = self.memory.get_memory(mid) if mid is not None else None
                evidence = self.rule_hits.get(t.track_id, (None, 0))
                signature = (d.class_name, rule.get("type"), rule.get("relation"), rule.get("min_confidence")) if rule else None
                count = evidence[1]+1 if signature is not None and evidence[0] == signature else 1
                if rule and d.confidence >= rule["min_confidence"]:
                    self.rule_hits[t.track_id] = (signature, count)
                else:
                    self.rule_hits.pop(t.track_id, None)
                    count = 0
                # Explicit per-game class mapping is authority; locked appearance
                # overrides still win. Generic COCO classes have no implicit mapping.
                if (
                    rule
                    and not rule.get('require_semantic', False)
                    and d.confidence >= rule["min_confidence"]
                    and (d.confidence >= .6 or count >= 2 and t.age >= .25)
                    and not (record and record.get("locked"))
                    and not (record and record.get('relation') in {'friendly', 'neutral'}
                             and record.get('status', 'confirmed') == 'confirmed')
                ):
                    label, relation, confidence, status = (
                        rule["type"],
                        rule["relation"],
                        max(.6, d.confidence),
                        "confirmed",
                    )
                    if (
                        not record
                        or record.get("label") != label
                        or record.get("relation") != relation
                    ):
                        mid, name = None, ""
                objects.append(
                    VisualObject(
                        t.track_id,
                        label,
                        (d.x1, d.y1, d.x2, d.y2),
                        d.confidence,
                        d.class_name,
                        relation,
                        confidence,
                        t.track_id,
                        mid,
                        status,
                        name,
                    )
                )
            return [self.protect_named_ally(o) for o in objects]

    def remember_friendly(self, tid, frame, name='', captured_track=None):
        """Persist positively identified allies, not missing bars/no damage."""
        with self.lock:
            track = captured_track or self.tracks.get(tid)
            if track is None:
                return None
            match = self.memory.find_prototype_fast(track, frame)
            record = self.memory.get_memory(match[2]) if match else None
            if record and record.get('locked'):
                return None
            if record:
                record.update(label='npc', relation='friendly', status='confirmed', confidence=.9)
                if name:
                    record['name'] = name
                self.memory.persist()
                return match[2]
            else:
                mid = self.memory.add(track, frame, 'npc', .9, 'friendly', status='confirmed')
                record = self.memory.get_memory(mid)
                if record and name:
                    record['name'] = name
                    self.memory.persist()
                return mid

    def candidates(self, objects, now=None, *, only_unknown=True, retry_interval=3):
        now = time.monotonic() if now is None else now
        with self.lock:
            output = []
            ordered = sorted(objects, key=lambda o: (0 if self.class_rules.get(o.detector_type, {}).get("type") == "monster" else 1, math.hypot((o.bbox[0]+o.bbox[2])/2-self.frame.shape[1]/2, (o.bbox[1]+o.bbox[3])/2-self.frame.shape[0]/2))) if self.frame is not None else objects
            for o in ordered:
                if (
                    only_unknown and o.status == "confirmed"
                ) or now < self.retry_at.get(o.track_id, 0):
                    continue
                track = self.tracks.get(o.track_id)
                if track and track.age >= 0.25:
                    output.append(copy.deepcopy(track))
                    self.retry_at[o.track_id] = now + retry_interval
                if len(output) == 3:
                    break
            return output, digest(self.memory.path), self.frame

    def accept(self, tracks, frame, data, revision):
        with self.lock:
            if digest(self.memory.path) != revision:
                self.memory.reload_if_changed()
                return []
            accepted = []
            lookup = {i: t for i, t in enumerate(tracks)}
            for sem in data.get("objects", []):
                if not isinstance(sem, dict) or type(sem.get("index")) is not int:
                    continue
                t = lookup.pop(sem["index"], None)
                label, relation, conf = (
                    sem.get("semantic"),
                    sem.get("relation"),
                    sem.get("confidence"),
                )
                name = sem.get('name', '')
                if not isinstance(name, str) or len(name) > 100:
                    name = ''
                named_ally = bool(name and name.casefold().strip() in self.friendly_names)
                if named_ally:
                    label, relation = 'npc', 'friendly'
                if (
                    t is None
                    or label not in self.memory.CONFIRMED_LABELS
                    or relation not in {"hostile", "friendly", "neutral", "unknown"}
                    or type(conf) not in (float, int)
                    or not math.isfinite(conf)
                    or not 0.8 <= conf <= 1
                ):
                    continue
                similar = self.memory.find_prototype_fast(t, frame)
                record = self.memory.get_memory(similar[2]) if similar else None
                if relation == 'friendly' and not (record and record.get('locked')):
                    mid = self.remember_friendly(t.track_id, frame, name, captured_track=t)
                    if mid is not None:
                        accepted.append((mid, 'npc', 'confirmed'))
                    continue
                if record and (
                    record.get("locked")
                    or record.get("status", "confirmed") == "confirmed"
                ):
                    continue
                agreed = bool(
                    record
                    and record["label"] == label
                    and record.get("relation") == relation
                )
                mid = self.memory.add(
                    t,
                    frame,
                    label,
                    conf,
                    relation,
                    force_new_semantic_prototype=bool(record and not agreed),
                    status="provisional",
                )
                record = self.memory.get_memory(mid)
                if not record:
                    continue
                if agreed:
                    record["observations"] = record.get("observations", 1) + 1
                    if record["observations"] >= 2:
                        record["status"] = "confirmed"
                    self.memory.persist()
                accepted.append((mid, label, record.get("status", "confirmed")))
            return accepted
