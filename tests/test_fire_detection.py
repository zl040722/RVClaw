import sys
import tempfile
import unittest
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "services/rvclaw_api"),
    str(ROOT / "services/rvclaw_report"),
    str(ROOT / "robot/ros2_ws/src/rvclaw_algo"),
]

from rvclaw_api import server
from rvclaw_api.fire_evidence import FireEvidenceStore


class FireEvidenceTest(unittest.TestCase):
    def test_store_lists_and_reads_annotated_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FireEvidenceStore(Path(directory))
            record = store.save(b"annotated-jpeg", {
                "camera_id": "astra",
                "source": "camera",
                "fire_count": 1,
                "inference_ms": 24.5,
            })

            self.assertEqual(store.list_records(), [record])
            self.assertEqual(store.read_image(record["evidence_id"]), b"annotated-jpeg")
            self.assertIsNone(store.read_image("not-an-evidence-id"))

    def test_delete_removes_only_the_selected_record_and_image(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FireEvidenceStore(Path(directory))
            first = store.save(b"first-image", {"camera_id": "astra", "source": "camera", "fire_count": 1})
            second = store.save(b"second-image", {"camera_id": "astra", "source": "camera", "fire_count": 2})

            self.assertTrue(store.delete(first["evidence_id"]))
            self.assertIsNone(store.read_image(first["evidence_id"]))
            self.assertEqual(store.list_records(), [second])
            self.assertEqual(store.read_image(second["evidence_id"]), b"second-image")
            self.assertFalse(store.delete(first["evidence_id"]))
            self.assertFalse(store.delete("invalid-id"))

    def test_camera_confirms_three_checks_and_saves_once_per_fire_episode(self):
        camera = server.CameraStream(
            "astra", "Astra", "/dev/null", 640, 480, 10,
            command=["unused"], inference={"task": "fire_detection"},
        )
        saved = []

        class MetricsStream:
            def __init__(self, values):
                self.lines = iter(
                    (f'RVCLAW_METRICS {{"detections":{value},"inference_ms":20.0}}\n').encode()
                    for value in values
                )

            def readline(self):
                return next(self.lines, b"")

        def observe(values):
            camera._drain_stderr(MetricsStream(values))

        def publish():
            camera._publish_complete_frames(bytearray(b"\xff\xd8frame\xff\xd9"))

        with patch.object(server, "FIRE_EVIDENCE_STORE") as store:
            store.save.side_effect = lambda image, metadata: saved.append((image, metadata))
            observe([1, 1])
            self.assertFalse(camera.status()["inference"]["fire_alert"])
            observe([1])
            self.assertTrue(camera.status()["inference"]["fire_alert"])
            publish()
            observe([1, 1, 1])
            publish()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0][0], b"\xff\xd8frame\xff\xd9")
            self.assertEqual(saved[0][1]["fire_count"], 1)

            observe([0, 0, 0])
            self.assertFalse(camera.status()["inference"]["fire_alert"])
            observe([1, 1, 1])
            publish()
            self.assertEqual(len(saved), 2)

    def test_uploaded_fire_image_returns_annotated_result_without_saving_evidence(self):
        ok, encoded = cv2.imencode(".jpg", np.zeros((32, 48, 3), dtype=np.uint8))
        self.assertTrue(ok)
        input_data = base64.b64encode(encoded.tobytes()).decode("ascii")
        output_data = encoded.tobytes()
        metrics = b'RVCLAW_METRICS {"detections":2,"inference_ms":31.5}\n'
        handler = SimpleNamespace(
            _read_json_body=lambda **_: {"image": input_data},
            _write_json=lambda value, status=200: setattr(handler, "response", (status, value)),
        )

        def run(command, **kwargs):
            image_path = Path(command[command.index("--input-video") + 1])
            self.assertTrue(image_path.is_file())
            return SimpleNamespace(returncode=0, stdout=output_data, stderr=metrics)

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(server, "YOLOV8_BINARY", Path(directory) / "detector"), \
                patch.object(server, "FIRE_CONFIG", Path(directory) / "fire.yaml"), \
                patch.object(server, "FIRE_MODEL", Path(directory) / "fire.onnx"), \
                patch.object(server, "FIRE_LABELS", Path(directory) / "fire.txt"), \
                patch.object(server, "VISION_ROOT", Path(directory)), \
                patch.object(server.CAMERA_CONTROLLER, "status", return_value={
                    "active_mode": "astra_fire", "cameras": [{"active": True, "online": False}],
                }), \
                patch.object(server.subprocess, "run", side_effect=run):
            for name in ("detector", "fire.yaml", "fire.onnx", "fire.txt"):
                (Path(directory) / name).write_text("ready", encoding="utf-8")
            server.RVClawRequestHandler._detect_uploaded_fire_image(handler)

        status, result = handler.response
        self.assertEqual(status, 200)
        self.assertEqual(result["source"], "upload")
        self.assertEqual(result["fire_count"], 2)
        self.assertTrue(result["fire_alert"])
        self.assertEqual(result["inference_ms"], 31.5)
        self.assertEqual(base64.b64decode(result["annotated_image"].split(",", 1)[1]), output_data)

    def test_uploaded_fire_image_rejects_invalid_image_without_running_detector(self):
        handler = SimpleNamespace(
            _read_json_body=lambda **_: {"image": base64.b64encode(b"not an image").decode("ascii")},
            _write_json=lambda value, status=200: setattr(handler, "response", (status, value)),
        )
        with patch.object(server.subprocess, "run") as run:
            server.RVClawRequestHandler._detect_uploaded_fire_image(handler)
        self.assertEqual(handler.response[0], 400)
        self.assertIn("无法读取", handler.response[1]["error"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
