from __future__ import annotations

import math
import time
from dataclasses import dataclass

from app.core.detection import Detection


@dataclass
class TrackedObject:
    track_id: int
    detection: Detection
    first_seen: float
    last_seen: float
    hits: int = 1
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    total_movement: float = 0.0
    stationary_since: float = 0.0
    damage_evidence: int = 0
    last_damage_time: float = 0.0
    player_distance: float = -1.0
    previous_player_distance: float = -1.0
    approach_evidence: float = 0.0
    follow_seconds: float = 0.0
    near_player_seconds: float = 0.0
    last_relation_update: float = 0.0
    relative_stationary_seconds: float = 0.0
    relative_motion_seconds: float = 0.0

    @property
    def age(self) -> float:
        return max(0.0, self.last_seen - self.first_seen)

    @property
    def speed(self) -> float:
        return math.hypot(self.velocity_x, self.velocity_y)

    @property
    def stationary_seconds(self) -> float:
        if self.stationary_since <= 0.0:
            return 0.0
        return max(0.0, self.last_seen - self.stationary_since)

    def add_damage_evidence(self, now: float) -> None:
        self.damage_evidence += 1
        self.last_damage_time = now

    def update_player_relation(
        self,
        player_x: float,
        player_y: float,
        now: float,
        camera_dx: float = 0.0,
        camera_dy: float = 0.0,
        camera_motion_reliable: bool = False,
        camera_is_moving: bool = False,
    ) -> None:
        """플레이어/카메라 이동을 제거한 상대 움직임만 적대 행동 증거로 사용한다."""
        distance = math.hypot(
            self.detection.center_x - player_x,
            self.detection.center_y - player_y,
        )
        if self.last_relation_update <= 0.0:
            self.player_distance = distance
            self.previous_player_distance = distance
            self.last_relation_update = now
            return

        dt = max(0.0, min(0.5, now - self.last_relation_update))
        self.previous_player_distance = self.player_distance
        self.player_distance = distance
        self.last_relation_update = now

        if not camera_motion_reliable:
            # 배경 공통 이동을 확정할 수 없을 때는 화면상 거리 감소를 적대 증거로 쓰지 않는다.
            self.approach_evidence = max(0.0, self.approach_evidence - 0.25)
            self.follow_seconds = max(0.0, self.follow_seconds - dt * 0.50)
            self.relative_stationary_seconds = max(
                0.0, self.relative_stationary_seconds - dt * 0.25
            )
        else:
            object_vx = self.velocity_x - camera_dx
            object_vy = self.velocity_y - camera_dy
            relative_speed = math.hypot(object_vx, object_vy)

            # 카메라 공통 이동을 뺀 뒤 거의 움직이지 않으면 월드에서 정지한 NPC 후보.
            if relative_speed <= 18.0:
                self.relative_stationary_seconds = min(
                    30.0, self.relative_stationary_seconds + dt
                )
                self.relative_motion_seconds = max(
                    0.0, self.relative_motion_seconds - dt * 0.5
                )
            else:
                self.relative_stationary_seconds = max(
                    0.0, self.relative_stationary_seconds - dt * 0.5
                )
                self.relative_motion_seconds = min(
                    30.0, self.relative_motion_seconds + dt
                )

            rel_x = self.detection.center_x - player_x
            rel_y = self.detection.center_y - player_y
            rel_len = max(1.0, math.hypot(rel_x, rel_y))
            closing_speed = -(
                object_vx * rel_x + object_vy * rel_y
            ) / rel_len

            # 핵심:
            # 1) 카메라가 움직일 때는 공통 이동과 충분히 다른 자체 움직임이어야 함.
            # 2) 플레이어 방향 성분이 강해야 함.
            # 3) 단 한 프레임이 아니라 누적되어야 follow로 인정.
            camera_speed = math.hypot(camera_dx, camera_dy)
            min_relative_speed = 32.0 if camera_is_moving else 24.0
            approaching = (
                relative_speed >= min_relative_speed
                and closing_speed >= 22.0
            )

            if approaching:
                self.approach_evidence = min(
                    8.0, self.approach_evidence + min(1.0, dt * 3.0)
                )
                self.follow_seconds = min(12.0, self.follow_seconds + dt)
            else:
                self.approach_evidence = max(
                    0.0, self.approach_evidence - max(0.18, dt * 0.8)
                )
                self.follow_seconds = max(
                    0.0, self.follow_seconds - dt * 0.75
                )

        if distance <= 320.0:
            self.near_player_seconds = min(12.0, self.near_player_seconds + dt)
        else:
            self.near_player_seconds = max(
                0.0, self.near_player_seconds - dt * 0.5
            )



class ObjectTracker:
    """가벼운 중심점/크기 기반 객체 추적기. OpenCV를 사용하지 않는다."""

    def __init__(self, max_lost_seconds: float = 1.5, max_match_distance: float = 180.0) -> None:
        self.max_lost_seconds = max(0.2, float(max_lost_seconds))
        self.max_match_distance = max(20.0, float(max_match_distance))
        self._next_id = 1
        self._tracks: dict[int, TrackedObject] = {}

    @property
    def active_track_count(self) -> int:
        return len(self._tracks)

    @property
    def retained_ids(self) -> set[int]:
        return set(self._tracks)

    def reset(self) -> None:
        """현재 화면의 추적 상태를 비운다. 다음 ID 번호는 계속 증가시킨다."""
        self._tracks.clear()

    def update(self, detections: list[Detection], now: float | None = None) -> list[TrackedObject]:
        now = time.monotonic() if now is None else now
        self._expire(now)
        unmatched_tracks = set(self._tracks)
        output: list[TrackedObject] = []

        for detection in detections:
            track_id = self._best_match(detection, unmatched_tracks, now)
            if track_id is None:
                track = TrackedObject(
                    track_id=self._next_id,
                    detection=detection,
                    first_seen=now,
                    last_seen=now,
                    stationary_since=now,
                )
                self._tracks[track.track_id] = track
                self._next_id += 1
            else:
                track = self._tracks[track_id]
                dt = max(0.001, now - track.last_seen)
                dx = detection.center_x - track.detection.center_x
                dy = detection.center_y - track.detection.center_y
                movement = math.hypot(dx, dy)
                track.velocity_x = dx / dt
                track.velocity_y = dy / dt
                track.total_movement += movement
                # 화면 흔들림/YOLO bbox jitter 정도는 정지로 취급한다.
                if movement <= 8.0:
                    if track.stationary_since <= 0.0:
                        track.stationary_since = track.last_seen
                else:
                    track.stationary_since = now
                track.detection = detection
                track.last_seen = now
                track.hits += 1
                unmatched_tracks.discard(track_id)
            output.append(track)

        return output

    def _best_match(self, detection: Detection, candidates: set[int], now: float | None = None) -> int | None:
        best_id: int | None = None
        best_score = float("inf")
        diag = math.hypot(max(1.0, detection.width), max(1.0, detection.height))
        allowed = max(self.max_match_distance, diag * 1.25)

        for track_id in candidates:
            track = self._tracks[track_id]
            old = track.detection
            if old.class_name != detection.class_name:
                continue
            # Predict briefly from recent velocity; clamp displacement so a
            # bad match cannot propel a track across the screen.
            dt = min(.25, max(0, (time.monotonic() if now is None else now) - track.last_seen))
            px = old.center_x + max(-allowed / 2, min(allowed / 2, track.velocity_x * dt))
            py = old.center_y + max(-allowed / 2, min(allowed / 2, track.velocity_y * dt))
            distance = math.hypot(detection.center_x - px, detection.center_y - py)
            size_ratio = max(
                detection.width / max(1.0, old.width), old.width / max(1.0, detection.width),
                detection.height / max(1.0, old.height), old.height / max(1.0, detection.height),
            )
            if distance > allowed or size_ratio > 2.5:
                continue
            score = distance + (size_ratio - 1.0) * 40.0
            if score < best_score:
                best_score = score
                best_id = track_id
        return best_id

    def _expire(self, now: float) -> None:
        expired = [track_id for track_id, track in self._tracks.items() if now - track.last_seen > self.max_lost_seconds]
        for track_id in expired:
            del self._tracks[track_id]
