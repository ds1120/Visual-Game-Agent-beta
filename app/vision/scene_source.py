"""Compatibility telemetry for the OpenCV/Qwen scene source; no detector model."""
class SceneSource:
    def __init__(self, **settings):
        self.__dict__.update(settings)
        self.enabled = settings.get('enabled', False)
        self.model_path = self.active_model_path = 'OpenCV + Qwen-VL'
        self.interval = .1
        self.image_size = 768
        self.confidence = .8
        self.device = 'CPU/OpenCV + Qwen API'
        self.roi_x = self.roi_y = 0
        self.roi_width = self.roi_height = 1
        self.raw_count = self.filtered_count = 0
        self.classes = {}
        self.last_inference_ms = 0
        self.last_error = None
        self.raw_confidence = .8

    def detect(self, frame):
        if not self.enabled:return []
        raise RuntimeError('Scene detection belongs to NonYoloAgent; no YOLO path is available')
