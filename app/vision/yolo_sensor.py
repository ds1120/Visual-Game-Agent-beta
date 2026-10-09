from __future__ import annotations

import time
import numpy as np
from pathlib import Path
from app.vision.onnx_shape import fixed_image_shape

from app.core.logger import get_logger

log = get_logger(__name__)

from app.core.game_state import VisualObject


class YOLOSensor:
    """Fast WHERE sensor with cached inference results."""

    def __init__(
        self,
        enabled: bool = False,
        model_path: str = "yolo/models/best.onnx",
        confidence: float = 0.40,
        device: str = "0",
        image_size: int = 320,
        interval: float = 0.25,
        max_detections: int = 30,
        roi_x: float = 0.05,
        roi_y: float = 0.05,
        roi_width: float = 0.90,
        roi_height: float = 0.85,
        dedup_iou: float = 0.75,
    ) -> None:
        self.enabled = bool(enabled)
        self.model_path = model_path
        self.confidence = float(confidence)
        self.raw_confidence = min(0.10, self.confidence)
        self.device = device
        self.image_size = int(image_size)
        self.interval = max(0.05, float(interval))
        self.max_detections = max(1, int(max_detections))
        self.roi_x = min(1.0, max(0.0, float(roi_x)))
        self.roi_y = min(1.0, max(0.0, float(roi_y)))
        self.roi_width = min(1.0 - self.roi_x, max(0.01, float(roi_width)))
        self.roi_height = min(1.0 - self.roi_y, max(0.01, float(roi_height)))
        self.dedup_iou = min(0.99, max(0.50, float(dedup_iou)))
        self._last_run = 0.0
        self._cache: list[VisualObject] = []
        self._model = None
        self.active_model_path = str(model_path)
        self.raw_count = 0
        self.filtered_count = 0
        self.classes = {}
        self.last_error = None
        self.last_inference_ms = 0.0
        self.inference_count = 0
        self._debug_last_log = 0.0
        self._debug_interval = 2.0

        if self.enabled:
            if not Path(self.model_path).is_file():
                raise ValueError(f"YOLO 모델 파일이 없습니다: {self.model_path}")
            from ultralytics import YOLO

            shape = fixed_image_shape(self.model_path)
            if shape and shape != (self.image_size, self.image_size):
                sibling = Path(self.model_path).with_suffix(".pt")
                if not sibling.is_file():
                    raise ValueError(
                        f"ONNX 입력은 {shape} 고정입니다. 동적 ONNX 또는 같은 모델의 .pt 파일이 필요합니다."
                    )
                self.active_model_path = str(sibling)
                log.warning(
                    f"[YOLO] ONNX input={shape} requested={self.image_size}; resizing uses sibling PT: {sibling}"
                )
            self._model = YOLO(self.active_model_path)
            log.info(
                f"[YOLO] enabled model={self.model_path} imgsz={self.image_size} "
                f"interval={self.interval:.2f}s roi=({self.roi_x:.2f},{self.roi_y:.2f},"
                f"{self.roi_width:.2f},{self.roi_height:.2f})"
            )

    @staticmethod
    def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / float(area_a + area_b - inter)

    def _deduplicate(self, objects: list[VisualObject]) -> list[VisualObject]:
        """Keep overlapping different classes (e.g. loot beneath a monster)."""
        kept: list[VisualObject] = []
        for obj in sorted(objects, key=lambda x: x.confidence, reverse=True):
            if any(
                obj.object_type == prev.object_type
                and self._iou(obj.bbox, prev.bbox) >= self.dedup_iou
                for prev in kept
            ):
                continue
            kept.append(obj)

        # Downstream semantic indices must be compact/stable for this inference batch.
        for i, obj in enumerate(kept):
            obj.object_id = i
        return kept

    def detect(self, frame: np.ndarray) -> list[VisualObject]:
        if not self.enabled or self._model is None:
            return []

        now = time.monotonic()
        if now - self._last_run < self.interval:
            return list(self._cache)
        self._last_run = now

        frame_h, frame_w = frame.shape[:2]
        offset_x = int(round(frame_w * self.roi_x))
        offset_y = int(round(frame_h * self.roi_y))
        roi_w = int(round(frame_w * self.roi_width))
        roi_h = int(round(frame_h * self.roi_height))
        x2_roi = min(frame_w, offset_x + roi_w)
        y2_roi = min(frame_h, offset_y + roi_h)
        crop = frame[offset_y:y2_roi, offset_x:x2_roi]
        if crop.size == 0:
            log.warning("[YOLO] ROI crop is empty; skipping inference")
            return list(self._cache)

        infer_started = time.perf_counter()
        results = self._model.predict(
            source=crop,
            conf=self.raw_confidence,
            imgsz=self.image_size,
            device=None if self.device == "auto" else self.device,
            max_det=self.max_detections,
            verbose=False,
        )
        # Raw model output diagnostics (before VisualObject filtering/conversion).
        now_debug = time.monotonic()
        if now_debug - self._debug_last_log >= self._debug_interval:
            self._debug_last_log = now_debug
            raw_boxes = (
                results[0].boxes
                if results and getattr(results[0], "boxes", None) is not None
                else []
            )
            names = getattr(results[0], "names", {}) if results else {}
            log.debug(
                f"[YOLO RAW] frame={frame_w}x{frame_h} "
                f"crop={crop.shape[1]}x{crop.shape[0]} offset=({offset_x},{offset_y}) "
                f"imgsz={self.image_size} raw_threshold={self.raw_confidence:.2f} "
                f"final_threshold={self.confidence:.2f} raw={len(raw_boxes)} classes={names}"
            )
            for raw_i, box in enumerate(raw_boxes[:10]):
                cls_id = int(box.cls[0].item())
                cls_name = (
                    str(names.get(cls_id, cls_id))
                    if isinstance(names, dict)
                    else str(cls_id)
                )
                conf = float(box.conf[0].item())
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                sx1, sy1 = x1 + offset_x, y1 + offset_y
                sx2, sy2 = x2 + offset_x, y2 + offset_y
                status = "PASS" if conf >= self.confidence else "DROP_CONF"
                log.debug(
                    f"[YOLO RAW] #{raw_i} class={cls_name}({cls_id}) conf={conf:.3f} "
                    f"crop_bbox=({x1},{y1},{x2},{y2}) "
                    f"screen_bbox=({sx1},{sy1},{sx2},{sy2}) {status}"
                )
        objects: list[VisualObject] = []
        if results:
            result = results[0]
            names = result.names
            self.classes = (
                dict(names) if isinstance(names, dict) else dict(enumerate(names))
            )
            self.raw_count = len(result.boxes) if result.boxes is not None else 0
            for i, box in enumerate(result.boxes if result.boxes is not None else []):
                xyxy = box.xyxy[0].detach().cpu().tolist()
                xyxy[0] += offset_x
                xyxy[2] += offset_x
                xyxy[1] += offset_y
                xyxy[3] += offset_y
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                if conf < self.confidence:
                    continue
                label = (
                    str(names.get(cls_id, cls_id))
                    if isinstance(names, dict)
                    else str(names[cls_id])
                )
                objects.append(
                    VisualObject(
                        object_id=i,
                        object_type=label,
                        bbox=tuple(int(round(v)) for v in xyxy),
                        confidence=conf,
                    )
                )
        self.last_inference_ms = (time.perf_counter() - infer_started) * 1000.0
        if (
            now_debug - getattr(self, "_debug_last_filter_log", 0.0)
            >= self._debug_interval
        ):
            self._debug_last_filter_log = now_debug
            all_boxes = (
                results[0].boxes
                if results and getattr(results[0], "boxes", None) is not None
                else []
            )
            pass_count = sum(
                1 for b in all_boxes if float(b.conf[0].item()) >= self.confidence
            )
            log.debug(
                f"[YOLO FILTER] raw={len(all_boxes)} pass={pass_count} "
                f"drop_conf={len(all_boxes) - pass_count} final_threshold={self.confidence:.2f}"
            )
        before_dedup = len(objects)
        self.filtered_count = before_dedup
        objects = self._deduplicate(objects)
        removed_duplicates = before_dedup - len(objects)
        if removed_duplicates:
            log.debug(
                f"[YOLO DEDUP] before={before_dedup} after={len(objects)} "
                f"removed={removed_duplicates} iou={self.dedup_iou:.2f}"
            )

        self.inference_count += 1
        self.last_error = None
        self._cache = objects
        log.debug(
            f"[YOLO] objects={len(objects)} inference={self.last_inference_ms:.1f}ms "
            f"imgsz={self.image_size}"
        )
        return list(objects)
