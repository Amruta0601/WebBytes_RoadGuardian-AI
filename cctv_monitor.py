"""
CCTV Monitor Module
====================
Detects accidents/anomalies in uploaded videos using:
 - Background subtraction (MOG2)
 - Large contour detection (sudden crashes = large abrupt motion)
 - Sustained anomaly counter (avoids false positives)

Default state: shows a "waiting for video" screen until a video is uploaded.
"""

import cv2
import time
import numpy as np
import threading

from .alert_system import AlertSystem


class CCTVMonitor:
    ANOMALY_AREA_THRESHOLD = 8000   # min pixel area to flag as anomaly
    ANOMALY_TRIGGER_COUNT  = 12     # sustained frames before alerting
    ANOMALY_DECAY          = 1      # how fast counter drops per clean frame

    def __init__(self, socketio):
        self.alert_system  = AlertSystem(socketio)
        self.status        = "Waiting for video upload"
        self.is_running    = True

        self._video_source = None        # None = no video yet
        self._source_label = "No video loaded"
        self._source_lock  = threading.Lock()
        self._source_changed = threading.Event()

        self._fgbg         = cv2.createBackgroundSubtractorMOG2(
            history=400, varThreshold=40, detectShadows=True
        )
        self._anomaly_count = 0

    # ── Public API ─────────────────────────────────────────────────────── #
    def get_status(self) -> str:
        return self.status

    def set_video_source(self, path: str):
        with self._source_lock:
            self._video_source   = path
            self._source_label   = f"Uploaded: {path.split('/')[-1]}"
            self._anomaly_count  = 0
            # Reset background model for new video
            self._fgbg = cv2.createBackgroundSubtractorMOG2(
                history=400, varThreshold=40, detectShadows=True
            )
        self._source_changed.set()

    def _get_source(self):
        with self._source_lock:
            return self._video_source, self._source_label

    # ── Waiting frame ──────────────────────────────────────────────────── #
    @staticmethod
    def _waiting_frame() -> bytes:
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Gradient background
        for i in range(480):
            val = int(20 + i * 0.06)
            frame[i, :] = [val, val // 2, val // 3]
        cv2.putText(frame, "CCTV Monitor", (180, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (100, 200, 255), 3)
        cv2.putText(frame, "Upload a traffic/accident video", (80, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
        cv2.putText(frame, "to begin accident detection", (120, 275),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
        _, buf = cv2.imencode(".jpg", frame)
        return buf.tobytes()

    # ── Main generator ─────────────────────────────────────────────────── #
    def generate_frames(self):
        cap          = None
        current_src  = None
        waiting_buf  = self._waiting_frame()

        while self.is_running:
            source, label = self._get_source()

            # No video yet → keep showing waiting frame
            if source is None:
                yield waiting_buf
                time.sleep(0.1)
                continue

            # Source changed → re-open capture
            if source != current_src:
                if cap is not None:
                    cap.release()
                cap         = cv2.VideoCapture(source)
                current_src = source
                self._anomaly_count = 0
                self.status = "Analyzing video…"
                if not cap.isOpened():
                    self.status = "Error: cannot open video file"
                    yield waiting_buf
                    time.sleep(0.5)
                    current_src = None
                    continue

            ok, frame = cap.read()
            if not ok:
                # End of video → loop
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._anomaly_count = 0
                time.sleep(0.05)
                continue

            frame = cv2.resize(frame, (640, 480))

            # ── Background subtraction ─────────────────────────────────── #
            kernel = np.ones((5, 5), np.uint8)
            fgmask = self._fgbg.apply(frame)
            fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_OPEN, kernel)
            fgmask = cv2.dilate(fgmask, kernel, iterations=2)

            contours, _ = cv2.findContours(
                fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            large_motion = False
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area > self.ANOMALY_AREA_THRESHOLD:
                    large_motion = True
                    x, y, bw, bh = cv2.boundingRect(cnt)
                    cv2.rectangle(frame, (x, y), (x + bw, y + bh), (0, 0, 255), 2)
                    cv2.putText(frame, f"ANOMALY ({int(area)}px)", (x, max(y - 10, 15)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

            # ── Sustained anomaly logic ───────────────────────────────── #
            if large_motion:
                self._anomaly_count += 1
            else:
                self._anomaly_count = max(0, self._anomaly_count - self.ANOMALY_DECAY)

            if self._anomaly_count >= self.ANOMALY_TRIGGER_COUNT:
                self.status = "🚨 CRASH / ACCIDENT DETECTED"
                cv2.putText(frame, "!! ACCIDENT DETECTED !!",
                            (60, 60), cv2.FONT_HERSHEY_SIMPLEX,
                            1.1, (0, 0, 255), 3, cv2.LINE_AA)
                self.alert_system.trigger_alert(
                    "CCTV_CRASH",
                    "Road accident detected on CCTV feed. Emergency services notified.",
                    "critical",
                )
            else:
                self.status = "Normal Traffic"

            # ── Overlays ─────────────────────────────────────────────── #
            bar_color = (0, 0, 255) if self._anomaly_count >= self.ANOMALY_TRIGGER_COUNT else (0, 200, 100)
            status_color = (0, 0, 255) if "CRASH" in self.status else (0, 255, 100)

            cv2.putText(frame, f"CCTV Status: {self.status}",
                        (10, 450), cv2.FONT_HERSHEY_SIMPLEX,
                        0.65, status_color, 2, cv2.LINE_AA)
            cv2.putText(frame, label,
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (255, 220, 0), 2)
            # Anomaly meter
            meter = min(self._anomaly_count / self.ANOMALY_TRIGGER_COUNT, 1.0)
            cv2.rectangle(frame, (10, 35), (10 + int(200 * meter), 48), bar_color, -1)
            cv2.rectangle(frame, (10, 35), (210, 48), (80, 80, 80), 1)
            cv2.putText(frame, f"Anomaly meter: {int(meter*100)}%",
                        (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            yield buf.tobytes()
            time.sleep(0.033)

        if cap is not None:
            cap.release()
