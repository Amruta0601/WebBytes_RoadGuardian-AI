import cv2
import time
import numpy as np
import threading

from .alert_system import AlertSystem


class CCTVMonitor:
    def __init__(self, socketio):
        self.alert_system = AlertSystem(socketio)
        self.status = "Monitoring"
        self.is_running = True

        # Background subtractor for motion detection (simulating vehicle tracking)
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=50, detectShadows=True
        )
        self.anomaly_counter = 0
        # Default to camera index 1 to avoid fighting the driver cam (usually 0).
        self.video_source = 1
        self.source_label = "Live Camera"
        self._source_lock = threading.Lock()
        self._tracks = {}
        self._next_track_id = 1
        self._last_track_cleanup = time.time()
        self._confirm_frames = 4  # require persistence before calling it a vehicle
        # Coarse heuristics (tune per camera angle). Used only for filtering,
        # UI always shows generic "VEHICLE".
        self._min_bbox_w = 28
        self._min_bbox_h = 22
        self._bike_max_area_ratio = 0.035   # bikes are small
        self._truck_min_area_ratio = 0.11   # trucks are large

    def get_status(self):
        return self.status

    def set_video_source(self, source_path):
        with self._source_lock:
            self.video_source = source_path
            self.source_label = "Uploaded Video Sample"

    def _get_video_source(self):
        with self._source_lock:
            return self.video_source

    def generate_frames(self):
        cap = None
        current_source = None

        while self.is_running:
            desired_source = self._get_video_source()
            if cap is None or desired_source != current_source:
                if cap is not None:
                    cap.release()
                current_source = desired_source
                if isinstance(current_source, int):
                    cap = cv2.VideoCapture(current_source, cv2.CAP_DSHOW)
                else:
                    cap = cv2.VideoCapture(current_source)
                if not cap.isOpened() and isinstance(current_source, int) and current_source != 0:
                    # fallback to camera 0 if camera 1 doesn't exist
                    cap.release()
                    current_source = 0
                    with self._source_lock:
                        self.video_source = 0
                        self.source_label = "Live Camera"
                    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
                self.anomaly_counter = 0
                self._tracks = {}
                self._next_track_id = 1

            success, frame = cap.read()
            if not success:
                # Loop uploaded videos; camera read failures will just retry.
                if not isinstance(current_source, int):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                time.sleep(0.05)
                continue

            frame = cv2.resize(frame, (640, 480))
            h_frame, w_frame = frame.shape[:2]

            # Focus on road-like region (reduces false positives from sky/buildings).
            # You can tune these if your camera angle is different.
            roi_y0 = int(h_frame * 0.35)
            roi = frame[roi_y0:, :]

            # Vehicle-like motion blobs using background subtraction + cleanup.
            blurred = cv2.GaussianBlur(roi, (5, 5), 0)
            fgmask = self.fgbg.apply(blurred, learningRate=0.003)
            # remove shadows (MOG2 shadows are ~127)
            _, fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)

            # Remove noise
            kernel = np.ones((3, 3), np.uint8)
            fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_OPEN, kernel, iterations=1)
            fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_CLOSE, kernel, iterations=2)
            fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_DILATE, kernel, iterations=2)

            # Find contours
            contours, _ = cv2.findContours(
                fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            current_status = "Normal Traffic"
            large_motion_detected = False
            vehicle_boxes = []

            # Dynamic area thresholds based on frame size.
            roi_h = fgmask.shape[0]
            roi_w = fgmask.shape[1]
            min_area = int(roi_w * roi_h * 0.006)    # stricter: ~0.6% of ROI
            max_area = int(roi_w * roi_h * 0.35)     # ignore near-full-frame blobs
            crash_area = int(roi_w * roi_h * 0.12)   # ~12% of ROI

            for contour in contours:
                area = cv2.contourArea(contour)
                if area < min_area or area > max_area:
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                # ignore extreme aspect ratios (reduces noise)
                if h == 0 or w == 0:
                    continue
                aspect = w / float(h)
                if aspect < 0.6 or aspect > 3.6:
                    continue

                # Filter by "fill" ratio: vehicles tend to occupy a meaningful portion of bbox.
                box_area = w * h
                if box_area <= 0:
                    continue
                fill = float(area) / float(box_area)
                if fill < 0.18:
                    continue

                # Filter by solidity: remove ragged/fragmented blobs (shadows, trees).
                hull = cv2.convexHull(contour)
                hull_area = cv2.contourArea(hull)
                if hull_area <= 0:
                    continue
                solidity = float(area) / float(hull_area)
                if solidity < 0.55:
                    continue

                # Convert ROI coordinates back to full-frame coordinates.
                y_full = y + roi_y0

                # Ignore very small detections (small people/animals/noise)
                if w < self._min_bbox_w or h < self._min_bbox_h:
                    continue

                # Coarse type heuristic (used for filtering stability only).
                area_ratio = float(area) / float(roi_w * roi_h)
                label = "VEHICLE"
                if area_ratio <= self._bike_max_area_ratio and aspect < 1.45:
                    label = "SMALL"
                if area_ratio >= self._truck_min_area_ratio or (
                    w > int(roi_w * 0.45) and aspect > 1.2
                ):
                    label = "LARGE"

                vehicle_boxes.append((x, y_full, w, h, area, label))
                if area > crash_area:
                    large_motion_detected = True

            # Simple centroid-based tracking so boxes are stable on uploaded videos.
            now = time.time()
            updated_tracks = {}
            for (x, y, w, h, area, label) in vehicle_boxes:
                cx = x + w // 2
                cy = y + h // 2
                best_id = None
                best_dist = 1e9
                for tid, t in self._tracks.items():
                    tx, ty = t["c"]
                    d = (tx - cx) ** 2 + (ty - cy) ** 2
                    if d < best_dist and d < (60 ** 2):
                        best_dist = d
                        best_id = tid
                if best_id is None:
                    best_id = self._next_track_id
                    self._next_track_id += 1
                prev_hits = self._tracks.get(best_id, {}).get("hits", 0)
                updated_tracks[best_id] = {
                    "c": (cx, cy),
                    "b": (x, y, w, h),
                    "t": now,
                    "hits": prev_hits + 1,
                    "label": label,
                }

            self._tracks = updated_tracks

            # Draw tracked vehicles
            confirmed = 0
            for tid, t in self._tracks.items():
                if t.get("hits", 0) < self._confirm_frames:
                    continue
                confirmed += 1
                x, y, w, h = t["b"]
                # Keep one consistent look for all vehicles.
                color = (0, 255, 0)
                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                cv2.putText(
                    frame,
                    f"VEHICLE #{tid}",
                    (x, max(15, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    2,
                )

            if large_motion_detected:
                self.anomaly_counter += 1
                if self.anomaly_counter > 10:
                    current_status = "CRASH / ANOMALY DETECTED"
                    cv2.putText(
                        frame,
                        "CCTV EMERGENCY DETECTED!",
                        (50, 50),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1,
                        (0, 0, 255),
                        3,
                    )
                    self.alert_system.trigger_alert(
                        "CCTV_CRASH",
                        "Abnormal movement detected on CCTV Camera 1.",
                        "critical",
                    )
            else:
                self.anomaly_counter = max(0, self.anomaly_counter - 1)

            self.status = current_status

            # Overlay status
            cv2.putText(
                frame,
                f"CCTV Status: {self.status}",
                (10, 450),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0) if self.status == "Normal Traffic" else (0, 0, 255),
                2,
            )
            cv2.putText(
                frame,
                f"Source: {self.source_label}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 0),
                2,
            )
            cv2.putText(
                frame,
                f"Vehicles detected: {confirmed}",
                (10, 55),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 0),
                2,
            )

            ret, buffer = cv2.imencode(".jpg", frame)
            frame_bytes = buffer.tobytes()
            yield frame_bytes

            time.sleep(0.05)

        if cap is not None:
            cap.release()

