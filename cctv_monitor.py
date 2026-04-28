import cv2
import time
import numpy as np
import threading
from collections import deque

from .alert_system import AlertSystem

try:
    from ultralytics import YOLO  # type: ignore

    _HAS_YOLO = True
except Exception:
    YOLO = None
    _HAS_YOLO = False


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
        self._max_misses = 6  # keep tracks alive briefly when occluded
        self._pair_overlap = {}  # (id1,id2) -> overlap frame count
        self._collision_cooldown_until = 0.0
        self._pair_score = {}  # (id1,id2) -> cumulative collision score
        # Motion-based collision thresholds (tune-friendly defaults)
        self._moving_speed = 28.0  # px/sec considered "moving"
        self._impact_drop_ratio = 0.55  # speed falls below this fraction after contact
        self._impact_low_speed = 12.0  # px/sec considered "stopped/slow"
        self._min_iou_for_contact = 0.16

        # NOTE: User requested the simpler "detect everything" behavior.
        # YOLO remains optional, but is disabled by default for now.
        self._yolo = None
        self._use_yolo = False

    def _ensure_yolo_loaded(self):
        if self._yolo is not None:
            return True
        if not _HAS_YOLO:
            return False
        try:
            self._yolo = YOLO("yolov8n.pt")
            return True
        except Exception:
            self._yolo = None
            return False

    def get_status(self):
        return self.status

    def get_detection_mode(self):
        return "specific" if self._use_yolo else "loose"

    def is_yolo_available(self):
        return self._ensure_yolo_loaded()

    def set_detection_mode(self, mode):
        mode_normalized = (mode or "").strip().lower()
        if mode_normalized == "specific":
            if self._ensure_yolo_loaded():
                self._use_yolo = True
                return True, "CCTV detection mode set to Specific (YOLO vehicle-only)."
            self._use_yolo = False
            return False, "YOLO is not available. Install ultralytics to enable Specific mode."
        self._use_yolo = False
        return True, "CCTV detection mode set to Loose (motion objects)."

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

        def iou(a, b):
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            x1 = max(ax, bx)
            y1 = max(ay, by)
            x2 = min(ax + aw, bx + bw)
            y2 = min(ay + ah, by + bh)
            inter_w = max(0, x2 - x1)
            inter_h = max(0, y2 - y1)
            inter = inter_w * inter_h
            if inter <= 0:
                return 0.0
            union = (aw * ah) + (bw * bh) - inter
            return float(inter) / float(max(union, 1))

        def track_update(detections, now):
            """
            detections: list of (x,y,w,h,score)
            """
            used_track_ids = set()
            for tid in list(self._tracks.keys()):
                self._tracks[tid]["miss"] = self._tracks[tid].get("miss", 0) + 1

            for (x, y, w, h, score) in detections:
                cx = x + w // 2
                cy = y + h // 2
                best_id = None
                best = -1.0

                # Prefer IoU match, fall back to center distance.
                for tid, t in self._tracks.items():
                    if tid in used_track_ids:
                        continue
                    ov = iou((x, y, w, h), t["b"])
                    if ov > best and ov > 0.12:
                        best = ov
                        best_id = tid

                if best_id is None:
                    best_dist = 1e18
                    for tid, t in self._tracks.items():
                        if tid in used_track_ids:
                            continue
                        tx, ty = t["c"]
                        d = (tx - cx) ** 2 + (ty - cy) ** 2
                        if d < best_dist and d < (90 ** 2):
                            best_dist = d
                            best_id = tid

                if best_id is None:
                    best_id = self._next_track_id
                    self._next_track_id += 1
                    self._tracks[best_id] = {
                        "c": (cx, cy),
                        "b": (x, y, w, h),
                        "t": now,
                        "hits": 1,
                        "miss": 0,
                        "hist": deque(maxlen=10),
                        "speed": 0.0,
                        "prev_speed": 0.0,
                        "score": float(score),
                    }
                    self._tracks[best_id]["hist"].append((cx, cy, now))
                    used_track_ids.add(best_id)
                    continue

                t = self._tracks[best_id]
                hist = t.get("hist")
                if hist is None:
                    hist = deque(maxlen=10)
                    t["hist"] = hist
                if len(hist) >= 1:
                    px, py, pt = hist[-1]
                    dt = max(now - pt, 1e-3)
                    inst_speed = float(np.hypot(cx - px, cy - py)) / dt
                else:
                    inst_speed = 0.0

                prev_speed = float(t.get("speed", 0.0))
                hist.append((cx, cy, now))
                self._tracks[best_id] = {
                    **t,
                    "c": (cx, cy),
                    "b": (x, y, w, h),
                    "t": now,
                    "hits": int(t.get("hits", 0)) + 1,
                    "miss": 0,
                    "hist": hist,
                    "prev_speed": prev_speed,
                    "speed": inst_speed,
                    "score": float(score),
                }
                used_track_ids.add(best_id)

            for tid in list(self._tracks.keys()):
                if self._tracks[tid].get("miss", 0) > self._max_misses:
                    self._tracks.pop(tid, None)

        def detect_with_yolo(frame_bgr):
            # COCO classes: bicycle=1, car=2, motorcycle=3, bus=5, truck=7
            results = self._yolo.predict(
                frame_bgr,
                imgsz=640,
                conf=0.35,
                iou=0.45,
                classes=[1, 2, 3, 5, 7],
                verbose=False,
            )
            dets = []
            r0 = results[0]
            if r0.boxes is None:
                return dets
            boxes = r0.boxes
            for b in boxes:
                xyxy = b.xyxy[0].tolist()
                x1, y1, x2, y2 = [int(v) for v in xyxy]
                w = max(0, x2 - x1)
                h = max(0, y2 - y1)
                if w <= 0 or h <= 0:
                    continue
                # reject tiny detections
                if w < 28 or h < 22:
                    continue
                score = float(b.conf[0].item()) if hasattr(b.conf[0], "item") else float(b.conf[0])
                dets.append((x1, y1, w, h, score))
            return dets

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
                self._pair_overlap = {}

            success, frame = cap.read()
            if not success:
                # Loop uploaded videos; camera read failures will just retry.
                if not isinstance(current_source, int):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                time.sleep(0.05)
                continue

            frame = cv2.resize(frame, (640, 480))
            h_frame, w_frame = frame.shape[:2]

            now = time.time()

            # Simple "detect everything" mode (motion blobs over full frame).
            # This will show boxes for most moving objects (including non-vehicles),
            # matching the earlier behavior the user requested.
            used_yolo = self._use_yolo and (self._yolo is not None)
            if used_yolo:
                dets = detect_with_yolo(frame)
                track_update(dets, now)
                current_status = "Normal Traffic"
                large_motion_detected = False
            else:
                blurred = cv2.GaussianBlur(frame, (5, 5), 0)
                fgmask = self.fgbg.apply(blurred, learningRate=0.003)
                _, fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)

                kernel = np.ones((5, 5), np.uint8)
                fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_OPEN, kernel, iterations=1)
                fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_DILATE, kernel, iterations=2)

                contours, _ = cv2.findContours(
                    fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )

                current_status = "Normal Traffic"
                large_motion_detected = False

                frame_area = float(w_frame * h_frame)
                # More specific than before: ignore very small motions (leaves/noise)
                min_area = int(frame_area * 0.004)   # ~0.4% of frame
                crash_area = int(frame_area * 0.10)  # ~10% of frame

                detections = []
                small_blob_count = 0
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area < min_area:
                        if area > (min_area * 0.25):
                            small_blob_count += 1
                        continue
                    x, y, w, h = cv2.boundingRect(contour)
                    if w < 20 or h < 20:
                        continue
                    detections.append((x, y, w, h, 0.5))
                    if area > crash_area:
                        large_motion_detected = True

                track_update(detections, now)

            # Draw tracked vehicles (same regardless of detector).
            confirmed = 0
            confirmed_ids = []
            for tid, t in self._tracks.items():
                if t.get("hits", 0) < self._confirm_frames:
                    continue
                confirmed += 1
                confirmed_ids.append(tid)
                x, y, w, h = t["b"]
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

            # Collision detection (two confirmed vehicles overlap persistently).
            # This is much less noisy than single-blob "big motion" triggers.
            collision_detected = False
            collision_pair = None
            collision_box = None
            if now >= self._collision_cooldown_until and len(confirmed_ids) >= 2:
                ids = sorted(confirmed_ids)
                for i in range(len(ids)):
                    for j in range(i + 1, len(ids)):
                        a = self._tracks[ids[i]]["b"]
                        b = self._tracks[ids[j]]["b"]
                        ov = iou(a, b)
                        key = (ids[i], ids[j])
                        if ov > self._min_iou_for_contact:
                            self._pair_overlap[key] = self._pair_overlap.get(key, 0) + 1
                        else:
                            self._pair_overlap[key] = max(0, self._pair_overlap.get(key, 0) - 1)

                        # Accident definition:
                        # - two (or more) moving objects make contact (overlap)
                        # - at least one object was moving before contact
                        # - impact signal: speed drops sharply / one or both slow to near stop
                        if self._pair_overlap.get(key, 0) >= 3:
                            t1 = self._tracks[ids[i]]
                            t2 = self._tracks[ids[j]]
                            s1 = float(t1.get("speed", 0.0))
                            s2 = float(t2.get("speed", 0.0))
                            ps1 = float(t1.get("prev_speed", s1))
                            ps2 = float(t2.get("prev_speed", s2))
                            was_moving = (ps1 >= self._moving_speed) or (ps2 >= self._moving_speed)
                            # impact: sharp drop OR slow-down to low speed after contact
                            impact = False
                            if ps1 >= self._moving_speed and (s1 <= max(self._impact_low_speed, ps1 * self._impact_drop_ratio)):
                                impact = True
                            if ps2 >= self._moving_speed and (s2 <= max(self._impact_low_speed, ps2 * self._impact_drop_ratio)):
                                impact = True
                            if (ps1 >= self._moving_speed or ps2 >= self._moving_speed) and (s1 <= self._impact_low_speed and s2 <= self._impact_low_speed):
                                impact = True

                            # Optional debris burst signal (helps crash clips with breakup).
                            debris_burst = False
                            if "small_blob_count" in locals():
                                debris_burst = small_blob_count >= 18 and ov > 0.12

                            # Score-based decision for clearer collisions.
                            score = self._pair_score.get(key, 0.0)
                            score += min(3.0, ov * 10.0)
                            if was_moving:
                                score += 1.0
                            if impact:
                                score += 2.6
                            if debris_burst:
                                score += 2.0
                            # slight decay
                            score *= 0.92
                            self._pair_score[key] = score

                            if was_moving and impact and score >= 6.0:
                                collision_detected = True
                                collision_pair = key
                                # union box for visualization
                                ax, ay, aw, ah = a
                                bx, by, bw, bh = b
                                x1 = min(ax, bx)
                                y1 = min(ay, by)
                                x2 = max(ax + aw, bx + bw)
                                y2 = max(ay + ah, by + bh)
                                collision_box = (x1, y1, x2 - x1, y2 - y1)
                                break
                    if collision_detected:
                        break

            if collision_box is not None:
                x, y, w, h = collision_box
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 0, 255), 3)
                cv2.putText(
                    frame,
                    "COLLISION ZONE",
                    (x, max(20, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 0, 255),
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

            if collision_detected:
                current_status = "COLLISION DETECTED"
                cv2.putText(
                    frame,
                    "COLLISION DETECTED!",
                    (50, 90),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 0, 255),
                    3,
                )
                self.alert_system.trigger_alert(
                    "CCTV_COLLISION",
                    "Accident detected: moving objects collided.",
                    "critical",
                )
                self._collision_cooldown_until = now + 8.0

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

