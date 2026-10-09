from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app.core.object_tracker import TrackedObject
from app.profiles.profile_store import atomic_json, digest, profile_lock, validate_document


class ObjectMemory:
    """
    Track ID와 독립적인 장기 Semantic Object Memory.

    하나의 의미 종류(memory_id)에 여러 appearance fingerprint를 저장한다.
    같은 Track이 이동/회전/공격 애니메이션으로 모습이 바뀌면,
    안전한 범위 안에서 새 appearance를 같은 memory_id에 누적한다.
    """

    VERSION = 3
    CONFIRMED_LABELS = {"monster", "npc", "item", "player", "obstacle"}
    MAX_OBJECTS_PER_LABEL = 24
    MAX_APPEARANCES_PER_OBJECT = 12
    MERGE_SIMILARITY = 0.78
    # 몬스터는 전투 중 Track ID가 자주 바뀌므로 조금 더 적극적으로 같은 외형을 병합한다.
    MONSTER_MERGE_SIMILARITY = 0.74

    def __init__(self, path: str, similarity_threshold: float = 0.82) -> None:
        self.path = Path(path)
        self.similarity_threshold = float(similarity_threshold)
        self._objects: list[dict[str, Any]] = []
        self._track_labels: dict[int, tuple[str, float]] = {}
        self._track_memory_ids: dict[int, int] = {}
        # 미확정 Track의 장기-memory 후보. 같은 memory가 연속으로 관찰될 때만 확정한다.
        self._track_match_candidates: dict[int, tuple[int, str, float, int]] = {}
        self._next_memory_id = 1
        self._disk_digest = ""
        self._load()

    @property
    def object_count(self) -> int:
        """현재 로드된 장기 Object Memory 객체 수."""
        return len(self._objects)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            validate_document("object_memory.json", data)
            self._disk_digest = digest(self.path)
        except Exception as exc:
            print(f"[OBJECT-MEMORY] load failed: {exc}")
            return

        # v1/v2는 단일 bbox/appearance 기반이므로 새 다중 appearance 구조에 섞지 않는다.
        if not isinstance(data, dict) or data.get("version") != self.VERSION:
            print("[OBJECT-MEMORY] old memory format ignored; starting version 3")
            return

        objects = data.get("objects", [])
        if isinstance(objects, list):
            self._objects = [x for x in objects if isinstance(x, dict)]
        self._compact_loaded_memory()
        ids = [int(x.get("memory_id", 0)) for x in self._objects]
        self._next_memory_id = max(ids, default=0) + 1

    def _compact_loaded_memory(self) -> None:
        """기존 JSON의 과도한 appearance/object 누적을 안전하게 제한한다."""
        compacted: list[dict[str, Any]] = []
        per_label: dict[str, int] = {}
        # 최근에 만들어진 memory를 우선 보존한다.
        for obj in reversed(self._objects):
            label = str(obj.get("label", "unknown"))
            if label not in self.CONFIRMED_LABELS:
                continue
            if per_label.get(label, 0) >= self.MAX_OBJECTS_PER_LABEL:
                continue
            appearances = obj.get("appearances", [])
            if isinstance(appearances, list):
                obj["appearances"] = appearances[-self.MAX_APPEARANCES_PER_OBJECT :]
            compacted.append(obj)
            per_label[label] = per_label.get(label, 0) + 1
        self._objects = list(reversed(compacted))

    def _best_same_label_object(
        self,
        label: str,
        fingerprint: list[float],
    ) -> tuple[Optional[dict[str, Any]], float]:
        best_obj: Optional[dict[str, Any]] = None
        best_score = -1.0
        for obj in self._objects:
            if str(obj.get("label", "")) != label:
                continue
            for appearance in obj.get("appearances", []):
                if not isinstance(appearance, dict):
                    continue
                vector = appearance.get("fingerprint")
                if not isinstance(vector, list):
                    continue
                score = self._similarity(fingerprint, vector)
                if score > best_score:
                    best_score = score
                    best_obj = obj
        return best_obj, best_score

    def _prune_label(self, label: str) -> None:
        same = [x for x in self._objects if str(x.get("label", "")) == label]
        overflow = len(same) - self.MAX_OBJECTS_PER_LABEL
        if overflow <= 0:
            return
        removable = [x for x in same if not bool(x.get("locked", False))]
        remove_ids = {int(x.get("memory_id", -1)) for x in removable[:overflow]}
        self._objects = [x for x in self._objects if int(x.get("memory_id", -1)) not in remove_ids]

    def reload_if_changed(self) -> bool:
        with profile_lock(self.path.parent):
            if digest(self.path) == self._disk_digest:
                return False
            self._load()
            self.clear_track_cache()
            return True

    def _save(self) -> None:
        with profile_lock(self.path.parent):
            # Chat/editor changes win over stale in-memory learning.
            if digest(self.path) != self._disk_digest:
                self._load()
                self.clear_track_cache()
                return
            payload = {"version": self.VERSION, "objects": self._objects}
            validate_document("object_memory.json", payload)
            atomic_json(self.path, payload)
            self._disk_digest = digest(self.path)

    @staticmethod
    def _crop(track: TrackedObject, frame: np.ndarray) -> Optional[np.ndarray]:
        if frame is None or frame.size == 0:
            return None
        h, w = frame.shape[:2]
        d = track.detection
        x1 = max(0, min(w - 1, int(d.x1)))
        y1 = max(0, min(h - 1, int(d.y1)))
        x2 = max(x1 + 1, min(w, int(d.x2)))
        y2 = max(y1 + 1, min(h, int(d.y2)))
        crop = frame[y1:y2, x1:x2]
        return crop if crop.size else None

    @classmethod
    def _fingerprint(cls, track: TrackedObject, frame: np.ndarray) -> Optional[list[float]]:
        crop = cls._crop(track, frame)
        if crop is None:
            return None

        # OpenCV 분류가 아니라 YOLO crop의 저비용 외형 descriptor.
        # 위치/절대 크기를 제외하고 색/밝기/공간 분포를 사용해 거리 변화에 덜 민감하게 한다.
        crop = crop.astype(np.float32) / 255.0
        h, w = crop.shape[:2]
        features: list[float] = []

        # 4x4 spatial color means = 48 dimensions.
        for gy in range(4):
            y1, y2 = h * gy // 4, h * (gy + 1) // 4
            for gx in range(4):
                x1, x2 = w * gx // 4, w * (gx + 1) // 4
                cell = crop[y1:y2, x1:x2]
                if cell.size:
                    features.extend(cell.reshape(-1, cell.shape[-1]).mean(axis=0)[:3].tolist())
                else:
                    features.extend([0.0, 0.0, 0.0])

        # 전체 평균/표준편차 + aspect ratio. 절대 bbox 크기는 저장하지 않는다.
        pixels = crop.reshape(-1, crop.shape[-1])
        features.extend(pixels.mean(axis=0)[:3].tolist())
        features.extend(pixels.std(axis=0)[:3].tolist())
        aspect = min(4.0, max(0.25, float(w) / max(1.0, float(h)))) / 4.0
        features.append(aspect)

        v = np.asarray(features, dtype=np.float32)
        norm = float(np.linalg.norm(v))
        if norm <= 1e-8:
            return None
        return (v / norm).round(6).tolist()

    @staticmethod
    def _similarity(a: list[float], b: list[float]) -> float:
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        if va.shape != vb.shape or va.size == 0:
            return 0.0
        return float(np.clip(np.dot(va, vb), -1.0, 1.0))

    def get_track_label(self, track_id: int) -> Optional[tuple[str, float]]:
        return self._track_labels.get(track_id)

    def get_relation(self, memory_id: Optional[int]) -> str:
        """memory_id에 저장된 관계(hostile/friendly/neutral/unknown)를 반환한다."""
        if memory_id is None:
            return "unknown"
        for obj in self._objects:
            if int(obj.get("memory_id", -1)) == int(memory_id):
                return str(obj.get("relation", "unknown"))
        return "unknown"

    def get_memory(self, memory_id: Optional[int]) -> Optional[dict[str, Any]]:
        """memory_id의 JSON 레코드를 반환한다. JSON semantic이 최종 판정이다."""
        if memory_id is None:
            return None
        for obj in self._objects:
            if int(obj.get("memory_id", -1)) == int(memory_id):
                return obj
        return None

    def is_locked(self, memory_id: Optional[int]) -> bool:
        obj = self.get_memory(memory_id)
        return bool(obj and obj.get("locked", False))

    def resolve_semantic(
        self,
        memory_id: Optional[int],
        fallback_label: str = "unknown",
        fallback_relation: str = "unknown",
        fallback_confidence: float = 0.0,
    ) -> tuple[str, str, float, bool]:
        """JSON memory가 있으면 그 semantic을 최종 판정으로 반환한다."""
        obj = self.get_memory(memory_id)
        if obj is None:
            return str(fallback_label), str(fallback_relation), float(fallback_confidence), False
        return (
            str(obj.get("label", fallback_label)),
            str(obj.get("relation", fallback_relation)),
            float(obj.get("confidence", fallback_confidence)),
            bool(obj.get("locked", False)),
        )

    def persist(self) -> None:
        """현재 Object Memory를 디스크에 저장한다."""
        self._save()

    def has_memory_id(self, memory_id: Optional[int]) -> bool:
        if memory_id is None:
            return False
        return any(int(obj.get("memory_id", -1)) == int(memory_id) for obj in self._objects)

    def memory_ids_for_label(self, label: str) -> set[int]:
        return {
            int(obj.get("memory_id", -1))
            for obj in self._objects
            if str(obj.get("label", "")) == label
        }

    def all_memory_ids(self) -> set[int]:
        return {
            int(obj.get("memory_id", -1))
            for obj in self._objects
            if int(obj.get("memory_id", -1)) >= 0
        }

    def clear_track_cache(self) -> None:
        """
        Tracker reset 시 Track ID에만 종속된 단기 캐시를 비운다.

        object_memory.json의 장기 appearance 기억은 삭제하지 않는다.
        플레이어 사망/부활처럼 tracker가 reset되면 기존 track_id가
        재사용될 수 있으므로 label과 memory_id 연결을 함께 초기화한다.
        """
        self._track_labels.clear()
        self._track_memory_ids.clear()
        self._track_match_candidates.clear()

    def forget_track(self, track_id: int) -> None:
        """Track의 단기 label/memory 연결만 해제하고 장기 기억은 유지한다."""
        self._track_labels.pop(track_id, None)
        self._track_memory_ids.pop(track_id, None)
        self._track_match_candidates.pop(track_id, None)

    def remember_track(
        self,
        track_id: int,
        label: str,
        confidence: float,
        memory_id: Optional[int] = None,
    ) -> None:
        self._track_labels[track_id] = (label, float(confidence))
        if memory_id is not None:
            self._track_memory_ids[track_id] = int(memory_id)

    def find_prototype_fast(
        self,
        track: TrackedObject,
        frame: np.ndarray,
    ) -> Optional[tuple[str, float, int]]:
        """Known visual prototype fast path before Qwen.

        Strict normal matching runs first. If that misses, use a conservative
        second-stage appearance match. Ambiguous top candidates are left for Qwen.
        """
        strict = self.find_similar(track, frame)
        if strict is not None:
            return strict

        fp = self._fingerprint(track, frame)
        if fp is None:
            return None

        candidates: list[tuple[float, dict[str, Any]]] = []
        for obj in self._objects:
            if str(obj.get("label", "unknown")) not in self.CONFIRMED_LABELS:
                continue
            best = -1.0
            for appearance in obj.get("appearances", []):
                vector = appearance.get("fingerprint")
                if isinstance(vector, list):
                    best = max(best, self._similarity(fp, vector))
            if best >= 0.0:
                candidates.append((best, obj))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best_obj = candidates[0]
        second_score = candidates[1][0] if len(candidates) > 1 else -1.0

        # LOCK is semantic authority, not a reason to over-match identity.
        threshold = 0.86 if bool(best_obj.get("locked", False)) else 0.78
        if best_score < threshold:
            return None

        # Similar prototypes are genuinely ambiguous; let Qwen decide instead.
        if second_score >= threshold and (best_score - second_score) < 0.035:
            return None

        memory_id = int(best_obj["memory_id"])
        label = str(best_obj["label"])
        confidence = min(float(best_obj.get("confidence", 0.9)), best_score)
        return label, confidence, memory_id

    def find_similar(
        self,
        track: TrackedObject,
        frame: np.ndarray,
        label_hint: Optional[str] = None,
    ) -> Optional[tuple[str, float, int]]:
        fp = self._fingerprint(track, frame)
        if fp is None:
            return None

        hint = str(label_hint or "").strip().lower()
        best_obj: Optional[dict[str, Any]] = None
        best_score = -1.0
        identity_scores = {}
        for obj in self._objects:
            label = str(obj.get("label", "unknown"))
            if label not in self.CONFIRMED_LABELS:
                continue

            # YOLO class is only a coarse observation. Identity/type matching
            # is appearance-based so a corrected JSON label survives YOLO flips.

            for appearance in obj.get("appearances", []):
                vector = appearance.get("fingerprint")
                if not isinstance(vector, list):
                    continue
                score = self._similarity(fp, vector)
                identity_scores[int(obj["memory_id"])] = max(
                    identity_scores.get(int(obj["memory_id"]), -1.0), score
                )
                if score > best_score:
                    best_score = score
                    best_obj = obj

        # Identity and semantic label are separate concerns.
        # A user may change JSON label/relation while the physical object remains
        # the same. Do not require the old semantic class to create the identity.
        threshold = self.similarity_threshold
        if best_obj is not None and bool(best_obj.get("locked", False)):
            # LOCK protects semantic truth, not identity. Require a strong visual
            # match so a corrected NPC does not absorb a similar-looking monster.
            threshold = max(threshold, 0.86)
        elif hint == "monster":
            threshold = min(threshold, 0.78)

        if best_obj is None or best_score < threshold:
            return None

        scores = sorted(identity_scores.values(), reverse=True)
        if len(scores) > 1 and scores[1] >= threshold and scores[0] - scores[1] < 0.035:
            return None

        label = str(best_obj["label"])
        confidence = min(float(best_obj.get("confidence", 0.9)), max(0.0, best_score))
        memory_id = int(best_obj["memory_id"])
        return label, confidence, memory_id

    def confirm_similar_match(
        self,
        track: TrackedObject,
        match: tuple[str, float, int],
        required_consecutive: int = 3,
    ) -> Optional[tuple[str, float, int]]:
        """같은 memory_id가 여러 프레임 연속 매칭될 때만 Track을 확정한다."""
        label, confidence, memory_id = match
        previous = self._track_match_candidates.get(track.track_id)
        if previous is not None and previous[0] == memory_id and previous[1] == label:
            count = previous[3] + 1
            best_confidence = max(previous[2], float(confidence))
        else:
            count = 1
            best_confidence = float(confidence)

        self._track_match_candidates[track.track_id] = (
            int(memory_id),
            label,
            best_confidence,
            count,
        )
        if count < max(1, int(required_consecutive)):
            return None

        self._track_match_candidates.pop(track.track_id, None)
        self.remember_track(track.track_id, label, best_confidence, memory_id)
        return label, best_confidence, memory_id

    def clear_match_candidate(self, track_id: int) -> None:
        self._track_match_candidates.pop(track_id, None)

    def add(
        self,
        track: TrackedObject,
        frame: np.ndarray,
        label: str,
        confidence: float,
        relation: str = "unknown",
        force_new_semantic_prototype: bool = False,
        status: str = "confirmed",
    ) -> Optional[int]:
        if label not in self.CONFIRMED_LABELS:
            return None
        fp = self._fingerprint(track, frame)
        if fp is None:
            return None

        # 행동 증거로 새 Track이 확정되어도 같은 label의 유사 memory가 있으면
        # 새 memory_id를 만들지 않고 기존 semantic object로 병합한다.
        existing, score = (None, -1.0)
        if not force_new_semantic_prototype:
            existing, score = self._best_same_label_object(label, fp)
        merge_threshold = 0.84
        if existing is not None and score >= merge_threshold:
            memory_id = int(existing["memory_id"])
            # Manual JSON correction wins. Do not let Qwen/automatic learning
            # rewrite label/relation/confidence/appearances of a locked record.
            if bool(existing.get("locked", False)):
                self.remember_track(
                    track.track_id,
                    str(existing.get("label", label)),
                    float(existing.get("confidence", confidence)),
                    memory_id,
                )
                return memory_id
            appearances = existing.setdefault("appearances", [])
            changed = False

            # 거의 같은 모습은 Track만 기존 memory_id에 연결하고 JSON은 건드리지 않는다.
            # 전투 중 Track ID churn 때문에 매 프레임/매 Track 저장되는 현상을 막는다.
            if score < 0.92 and len(appearances) < self.MAX_APPEARANCES_PER_OBJECT:
                appearances.append({"fingerprint": fp})
                changed = True

            old_confidence = float(existing.get("confidence", 0.0))
            if float(confidence) > old_confidence + 0.01:
                existing["confidence"] = float(confidence)
                changed = True

            relation = str(relation or "unknown")
            old_relation = str(existing.get("relation", "unknown"))
            if relation != "unknown" and relation != old_relation:
                existing["relation"] = relation
                changed = True

            self.remember_track(track.track_id, label, confidence, memory_id)
            if changed:
                self._save()
            return memory_id

        # Qwen semantic can disagree with a manually corrected JSON label.
        # Before allocating a new ID, try identity-only matching across every label.
        # Existing JSON identity wins; Qwen must not fork the same physical object
        # into a new memory_id just because its semantic label changed.
        best_any: Optional[dict[str, Any]] = None
        best_any_score = -1.0
        for candidate in ([] if force_new_semantic_prototype else self._objects):
            for appearance in candidate.get("appearances", []):
                if not isinstance(appearance, dict):
                    continue
                vector = appearance.get("fingerprint")
                if not isinstance(vector, list):
                    continue
                score = self._similarity(fp, vector)
                if score > best_any_score:
                    best_any_score = score
                    best_any = candidate

        if best_any is not None:
            identity_threshold = 0.88 if bool(best_any.get("locked", False)) else 0.84
            if best_any_score >= identity_threshold:
                memory_id = int(best_any["memory_id"])
                stored_label = str(best_any.get("label", label))
                stored_conf = float(best_any.get("confidence", confidence))
                self.remember_track(track.track_id, stored_label, stored_conf, memory_id)

                # JSON is authoritative. Never change semantic label/relation here.
                # For unlocked records only, a sufficiently different but still
                # matching appearance may enrich the identity fingerprint set.
                if not bool(best_any.get("locked", False)):
                    appearances = best_any.setdefault("appearances", [])
                    if best_any_score < 0.92 and len(appearances) < self.MAX_APPEARANCES_PER_OBJECT:
                        appearances.append({"fingerprint": fp})
                        self._save()
                return memory_id

        memory_id = self._next_memory_id
        self._next_memory_id += 1
        obj = {
            "memory_id": memory_id,
            "memory_kind": "semantic_type",
            "status": status,
            "observations": 1,
            "label": label,
            "confidence": float(confidence),
            "relation": str(relation or "unknown"),
            "locked": False,
            "appearances": [{"fingerprint": fp}],
        }
        self._objects.append(obj)
        self._prune_label(label)
        self.remember_track(track.track_id, label, confidence, memory_id)
        self._save()
        return memory_id

    def learn_track_appearance(self, track: TrackedObject, frame: np.ndarray) -> bool:
        """
        이미 확정된 Track의 새로운 자세/방향을 같은 memory_id에 추가한다.

        너무 비슷함(>=0.97): 중복이므로 저장하지 않음.
        너무 다름(<0.70): tracker 오연결/가림 가능성이 있어 오염 방지 차원에서 저장하지 않음.
        중간 범위만 새로운 appearance로 학습한다.
        """
        memory_id = self._track_memory_ids.get(track.track_id)
        label_info = self._track_labels.get(track.track_id)
        if memory_id is None or label_info is None:
            return False

        fp = self._fingerprint(track, frame)
        if fp is None:
            return False

        obj = next((x for x in self._objects if int(x.get("memory_id", -1)) == memory_id), None)
        if obj is None:
            return False
        if bool(obj.get("locked", False)):
            return False

        appearances = obj.setdefault("appearances", [])
        scores = [
            self._similarity(fp, a.get("fingerprint", []))
            for a in appearances
            if isinstance(a, dict)
        ]
        best = max(scores, default=0.0)

        if best >= 0.97 or best < 0.70:
            return False

        # JSON 비대화 방지: 객체당 대표 외형 최대 12개.
        if len(appearances) >= self.MAX_APPEARANCES_PER_OBJECT:
            return False

        appearances.append({"fingerprint": fp})
        self._save()
        return True
