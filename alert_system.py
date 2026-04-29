"""
Driver Monitor Module
=====================
Uses MediaPipe FaceMesh + Pose to detect:
  - Drowsiness  : eyes closed > 4 seconds  → ALARM
  - Chest pain  : hands near chest > 5 seconds → ALARM
  - Head slump  : nose drops below shoulder level → ALARM
  - Passenger logic:
      * Passengers present → alarm only (no full escalation)
      * Driver alone       → full emergency workflow (location + call)
"""

import cv2
import numpy as np
import time
import threading

from .alert_system import AlertSystem

try:
    import mediapipe as mp  # type: ignore
    _HAS_MEDIAPIPE = True
    mp_face_mesh = mp.solutions.face_mesh
    mp_pose = mp.solutions.pose
    mp_drawing = mp.solutions.drawing_utils
except Exception as exc:
    print(f"[DriverMonitor] MediaPipe not available: {exc}")
    _HAS_MEDIAPIPE = False
    mp = mp_face_mesh = mp_pose = mp_drawing = None


# ── MediaPipe Face Mesh indices for eye landmarks ─────────────────────────── #
LEFT_EYE_IDX  = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_IDX = [33,  160, 158, 133, 153, 144]


class DriverMonitor:
    # ── Tuneable thresholds ───────────────────────────────────────────────── #
    EAR_THRESH          = 0.22   # eye-aspect-ratio below this → eyes closed
    DROWSY_SECONDS      = 4.0   # eyes must be closed this long to alarm
    CHEST_PAIN_SECONDS  = 5.0   # hands near chest this long to alarm
    HEAD_SLUMP_THRESH   = 0.18  # nose.y - shoulder_avg.y; increased to make it easier to trigger
    NO_FACE_FRAMES      = 60    # consecutive frames with no face → "no driver"
    CHEST_DIST_THRESH   = 0.35  # increased from 0.13 to make it much easier to detect hands on chest

    def __init__(self, socketio):
        self.alert_system  = AlertSystem(socketio)
        self.status        = "Monitoring"
        self.is_running    = True

        # Timers
        self._eye_closed_start  = None
        self._chest_hold_start  = None
        self._no_face_counter   = 0

        # Alarm audio state
        self._alarm_active  = False
        self._alarm_thread  = None

    # ── Helpers ───────────────────────────────────────────────────────────── #
    @staticmethod
    def _dist(a, b) -> float:
        a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
        return float(np.linalg.norm(a - b))

    def _ear(self, pts) -> float:
        """Eye Aspect Ratio"""
        A = self._dist(pts[1], pts[5])
        B = self._dist(pts[2], pts[4])
        C = self._dist(pts[0], pts[3])
        return (A + B) / (2.0 * C) if C > 0 else 0.0

    def _hand_near_chest(self, landmarks) -> bool:
        ls = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
        rs = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
        lw = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
        rw = landmarks[mp_pose.PoseLandmark.RIGHT_WRIST.value]
        cx = (ls.x + rs.x) / 2.0
        cy = (ls.y + rs.y) / 2.0
        chest = [cx, cy]
        ld = self._dist([lw.x, lw.y], chest)
        rd = self._dist([rw.x, rw.y], chest)
        return min(ld, rd) < self.CHEST_DIST_THRESH

    def _play_alarm_beep(self):
        """Continuous beep using OpenCV / numpy while alarm is active."""
        try:
            import numpy as np
            import struct, wave, io
            # We'll just use the TTS alert; beep via pyttsx3 is handled in alert_system
        except Exception:
            pass

    def get_status(self) -> str:
        return self.status

    # ── Overlay helpers ───────────────────────────────────────────────────── #
    @staticmethod
    def _put_text(frame, text, y, color=(0, 0, 255)):
        cv2.putText(frame, text, (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

    # ── Main generator ────────────────────────────────────────────────────── #
    def generate_frames(self):
        cap = cv2.VideoCapture(0)

        # Allow camera to warm up
        time.sleep(0.5)

        if not cap.isOpened():
            # Yield a placeholder frame
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "Camera not accessible", (80, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            ret, buf = cv2.imencode(".jpg", placeholder)
            while self.is_running:
                yield buf.tobytes()
                time.sleep(0.5)
            return

        if not _HAS_MEDIAPIPE:
            self.status = "Install mediapipe to enable AI detection"
            while self.is_running:
                ok, frame = cap.read()
                if not ok:
                    break
                self._put_text(frame, "MediaPipe not installed - basic feed only", 40, (0, 165, 255))
                ret, buf = cv2.imencode(".jpg", frame)
                yield buf.tobytes()
                time.sleep(0.04)
            cap.release()
            return

        # ── Full AI detection loop ─────────────────────────────────────── #
        with mp_face_mesh.FaceMesh(
            max_num_faces=5,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as face_mesh, mp_pose.Pose(
            min_detection_confidence=0.45,
            min_tracking_confidence=0.45,
        ) as pose_model:

            while self.is_running:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                frame  = cv2.flip(frame, 1)
                h, w   = frame.shape[:2]
                rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                mesh_result = face_mesh.process(rgb)
                pose_result = pose_model.process(rgb)

                # ── Face / passenger detection ─────────────────────────── #
                num_faces      = 0
                emergency_type = None          # drowsy | chest_pain | collapse
                now            = time.time()

                if mesh_result.multi_face_landmarks:
                    num_faces = len(mesh_result.multi_face_landmarks)
                    self._no_face_counter = 0

                    # Driver = first detected face
                    driver_lm = mesh_result.multi_face_landmarks[0].landmark
                    pts = [[int(lm.x * w), int(lm.y * h)] for lm in driver_lm]

                    left_eye  = [pts[i] for i in LEFT_EYE_IDX]
                    right_eye = [pts[i] for i in RIGHT_EYE_IDX]
                    ear       = (self._ear(left_eye) + self._ear(right_eye)) / 2.0

                    # Draw eye landmarks
                    for pt in left_eye + right_eye:
                        cv2.circle(frame, tuple(pt), 2, (0, 255, 0), -1)

                    # ── Drowsiness check ──────────────────────────────── #
                    if ear < self.EAR_THRESH:
                        if self._eye_closed_start is None:
                            self._eye_closed_start = now
                        closed_for = now - self._eye_closed_start
                        pct = min(closed_for / self.DROWSY_SECONDS, 1.0)
                        bar_w = int(200 * pct)
                        cv2.rectangle(frame, (10, h - 55), (10 + bar_w, h - 40),
                                      (0, 0, 255), -1)
                        cv2.rectangle(frame, (10, h - 55), (210, h - 40),
                                      (100, 100, 100), 1)
                        self._put_text(frame,
                                       f"Eyes Closed: {closed_for:.1f}s / {self.DROWSY_SECONDS}s",
                                       h - 60, (0, 165, 255))
                        if closed_for >= self.DROWSY_SECONDS:
                            emergency_type = "driver_drowsy"
                            self._put_text(frame, "!! DROWSINESS ALERT !!", 35, (0, 0, 255))
                    else:
                        self._eye_closed_start = None

                    # Draw all detected faces with index labels
                    for idx, face_lm in enumerate(mesh_result.multi_face_landmarks):
                        nose_x = int(face_lm.landmark[1].x * w)
                        nose_y = int(face_lm.landmark[1].y * h)
                        label  = "Driver" if idx == 0 else f"Passenger {idx}"
                        cv2.putText(frame, label, (nose_x - 30, nose_y - 15),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                    (0, 255, 255) if idx == 0 else (255, 165, 0), 2)

                else:
                    self._no_face_counter += 1
                    self._eye_closed_start = None
                    if self._no_face_counter > self.NO_FACE_FRAMES:
                        self.alert_system.trigger_alert(
                            "NO_DRIVER", "Warning: No driver detected in frame.", "medium"
                        )
                        self._put_text(frame, "WARNING: No Driver Detected", 35, (0, 165, 255))

                # ── Pose / chest-pain / collapse check ────────────────── #
                if pose_result.pose_landmarks:
                    lm = pose_result.pose_landmarks.landmark

                    # Chest pain
                    if self._hand_near_chest(lm):
                        if self._chest_hold_start is None:
                            self._chest_hold_start = now
                        held = now - self._chest_hold_start
                        pct  = min(held / self.CHEST_PAIN_SECONDS, 1.0)
                        bar_w = int(200 * pct)
                        cv2.rectangle(frame, (10, h - 90), (10 + bar_w, h - 75),
                                      (255, 0, 128), -1)
                        cv2.rectangle(frame, (10, h - 90), (210, h - 75),
                                      (100, 100, 100), 1)
                        self._put_text(frame,
                                       f"Hand on Chest: {held:.1f}s / {self.CHEST_PAIN_SECONDS}s",
                                       h - 95, (255, 100, 100))
                        if held >= self.CHEST_PAIN_SECONDS:
                            emergency_type = "chest_pain"
                            self._put_text(frame, "!! CHEST PAIN ALERT !!", 65, (0, 0, 255))
                    else:
                        self._chest_hold_start = None

                    # Head / collapse
                    nose_y_n   = lm[mp_pose.PoseLandmark.NOSE.value].y
                    ls_y       = lm[mp_pose.PoseLandmark.LEFT_SHOULDER.value].y
                    rs_y       = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].y
                    shoulder_y = (ls_y + rs_y) / 2.0
                    if (shoulder_y - nose_y_n) < self.HEAD_SLUMP_THRESH:
                        emergency_type = "collapse"
                        self._put_text(frame, "!! COLLAPSE DETECTED !!", 95, (0, 0, 255))
                else:
                    self._chest_hold_start = None

                # ── Passenger count overlay ───────────────────────────── #
                passengers = max(0, num_faces - 1)
                self._put_text(frame,
                               f"People in vehicle: {num_faces}  (Passengers: {passengers})",
                               130, (255, 255, 0))

                # ── Emergency dispatch ────────────────────────────────── #
                if emergency_type is not None:
                    if emergency_type == "driver_drowsy":
                        # Drowsiness is just a short local alarm, no emergency escalation
                        self.alert_system.trigger_alert(
                            "DROWSINESS_ALERT",
                            "Drowsiness detected. Please pay attention to the road.",
                            "high"
                        )
                    else:
                        if passengers > 0:
                            # Passengers present → alarm only
                            self.alert_system.trigger_alert(
                                "PASSENGER_ALARM",
                                f"Emergency detected ({emergency_type.replace('_',' ')}). "
                                "Passengers present - alarm activated.",
                                "high",
                            )
                        else:
                            # Driver alone → full escalation
                            self.alert_system.trigger_alert(
                                "DRIVER_ONLY_EMERGENCY",
                                f"Driver-only emergency: {emergency_type.replace('_',' ')}. "
                                "Activating full emergency workflow.",
                                "critical",
                            )
                            self.alert_system.escalate_driver_only_emergency(emergency_type)

                # ── Status bar ───────────────────────────────────────── #
                if emergency_type is not None:
                    status_label = f"EMERGENCY: {emergency_type.upper()}"
                    status_color = (0, 0, 255)
                elif num_faces == 0:
                    status_label = "No Driver Detected"
                    status_color = (0, 165, 255)
                else:
                    status_label = "Safe - Monitoring"
                    status_color = (0, 255, 100)

                self.status = status_label
                cv2.putText(frame, f"Status: {status_label}",
                            (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX,
                            0.65, status_color, 2, cv2.LINE_AA)

                ret, buf = cv2.imencode(".jpg", frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                yield buf.tobytes()
                time.sleep(0.033)   # ~30 fps

        cap.release()
    def _ear(self, pts) -> float:
        """Eye Aspect Ratio"""
        A = self._dist(pts[1], pts[5])
        B = self._dist(pts[2], pts[4])
        C = self._dist(pts[0], pts[3])
        return (A + B) / (2.0 * C) if C > 0 else 0.0

    def _hand_near_chest(self, landmarks) -> bool:
        ls = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
        rs = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
        lw = landmarks[mp_pose.PoseLandmark.LEFT_WRIST.value]
        rw = landmarks[mp_pose.PoseLandmark.RIGHT_WRIST.value]
        cx = (ls.x + rs.x) / 2.0
        cy = (ls.y + rs.y) / 2.0
        chest = [cx, cy]
        ld = self._dist([lw.x, lw.y], chest)
        rd = self._dist([rw.x, rw.y], chest)
        return min(ld, rd) < self.CHEST_DIST_THRESH

    def _play_alarm_beep(self):
        """Continuous beep using OpenCV / numpy while alarm is active."""
        try:
            import numpy as np
            import struct, wave, io
            # We'll just use the TTS alert; beep via pyttsx3 is handled in alert_system
        except Exception:
            pass

    def get_status(self) -> str:
        return self.status

    # ── Overlay helpers ───────────────────────────────────────────────────── #
    @staticmethod
    def _put_text(frame, text, y, color=(0, 0, 255)):
        cv2.putText(frame, text, (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

    # ── Main generator ────────────────────────────────────────────────────── #
    def generate_frames(self):
        cap = cv2.VideoCapture(0)

        # Allow camera to warm up
        time.sleep(0.5)

        if not cap.isOpened():
            # Yield a placeholder frame
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "Camera not accessible", (80, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            ret, buf = cv2.imencode(".jpg", placeholder)
            while self.is_running:
                yield buf.tobytes()
                time.sleep(0.5)
            return

        if not _HAS_MEDIAPIPE:
            self.status = "Install mediapipe to enable AI detection"
            while self.is_running:
                ok, frame = cap.read()
                if not ok:
                    break
                self._put_text(frame, "MediaPipe not installed - basic feed only", 40, (0, 165, 255))
                ret, buf = cv2.imencode(".jpg", frame)
                yield buf.tobytes()
                time.sleep(0.04)
            cap.release()
            return

        # ── Full AI detection loop ─────────────────────────────────────── #
        with mp_face_mesh.FaceMesh(
            max_num_faces=5,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as face_mesh, mp_pose.Pose(
            min_detection_confidence=0.45,
            min_tracking_confidence=0.45,
        ) as pose_model:

            while self.is_running:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                frame  = cv2.flip(frame, 1)
                h, w   = frame.shape[:2]
                rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                mesh_result = face_mesh.process(rgb)
                pose_result = pose_model.process(rgb)

                # ── Face / passenger detection ─────────────────────────── #
                num_faces      = 0
                emergency_type = None          # drowsy | chest_pain | collapse
                now            = time.time()

                if mesh_result.multi_face_landmarks:
                    num_faces = len(mesh_result.multi_face_landmarks)
                    self._no_face_counter = 0

                    # Driver = first detected face
                    driver_lm = mesh_result.multi_face_landmarks[0].landmark
                    pts = [[int(lm.x * w), int(lm.y * h)] for lm in driver_lm]

                    left_eye  = [pts[i] for i in LEFT_EYE_IDX]
                    right_eye = [pts[i] for i in RIGHT_EYE_IDX]
                    ear       = (self._ear(left_eye) + self._ear(right_eye)) / 2.0

                    # Draw eye landmarks
                    for pt in left_eye + right_eye:
                        cv2.circle(frame, tuple(pt), 2, (0, 255, 0), -1)

                    # ── Drowsiness check ──────────────────────────────── #
                    if ear < self.EAR_THRESH:
                        if self._eye_closed_start is None:
                            self._eye_closed_start = now
                        closed_for = now - self._eye_closed_start
                        pct = min(closed_for / self.DROWSY_SECONDS, 1.0)
                        bar_w = int(200 * pct)
                        cv2.rectangle(frame, (10, h - 55), (10 + bar_w, h - 40),
                                      (0, 0, 255), -1)
                        cv2.rectangle(frame, (10, h - 55), (210, h - 40),
                                      (100, 100, 100), 1)
                        self._put_text(frame,
                                       f"Eyes Closed: {closed_for:.1f}s / {self.DROWSY_SECONDS}s",
                                       h - 60, (0, 165, 255))
                        if closed_for >= self.DROWSY_SECONDS:
                            emergency_type = "driver_drowsy"
                            self._put_text(frame, "!! DROWSINESS ALERT !!", 35, (0, 0, 255))
                    else:
                        self._eye_closed_start = None

                    # Draw all detected faces with index labels
                    for idx, face_lm in enumerate(mesh_result.multi_face_landmarks):
                        nose_x = int(face_lm.landmark[1].x * w)
                        nose_y = int(face_lm.landmark[1].y * h)
                        label  = "Driver" if idx == 0 else f"Passenger {idx}"
                        cv2.putText(frame, label, (nose_x - 30, nose_y - 15),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                    (0, 255, 255) if idx == 0 else (255, 165, 0), 2)

                else:
                    self._no_face_counter += 1
                    self._eye_closed_start = None
                    if self._no_face_counter > self.NO_FACE_FRAMES:
                        self.alert_system.trigger_alert(
                            "NO_DRIVER", "Warning: No driver detected in frame.", "medium"
                        )
                        self._put_text(frame, "WARNING: No Driver Detected", 35, (0, 165, 255))

                # ── Pose / chest-pain / collapse check ────────────────── #
                if pose_result.pose_landmarks:
                    lm = pose_result.pose_landmarks.landmark

                    # Chest pain
                    if self._hand_near_chest(lm):
                        if self._chest_hold_start is None:
                            self._chest_hold_start = now
                        held = now - self._chest_hold_start
                        pct  = min(held / self.CHEST_PAIN_SECONDS, 1.0)
                        bar_w = int(200 * pct)
                        cv2.rectangle(frame, (10, h - 90), (10 + bar_w, h - 75),
                                      (255, 0, 128), -1)
                        cv2.rectangle(frame, (10, h - 90), (210, h - 75),
                                      (100, 100, 100), 1)
                        self._put_text(frame,
                                       f"Hand on Chest: {held:.1f}s / {self.CHEST_PAIN_SECONDS}s",
                                       h - 95, (255, 100, 100))
                        if held >= self.CHEST_PAIN_SECONDS:
                            emergency_type = "chest_pain"
                            self._put_text(frame, "!! CHEST PAIN ALERT !!", 65, (0, 0, 255))
                    else:
                        self._chest_hold_start = None

                    # Head / collapse
                    nose_y_n   = lm[mp_pose.PoseLandmark.NOSE.value].y
                    ls_y       = lm[mp_pose.PoseLandmark.LEFT_SHOULDER.value].y
                    rs_y       = lm[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].y
                    shoulder_y = (ls_y + rs_y) / 2.0
                    if (shoulder_y - nose_y_n) < self.HEAD_SLUMP_THRESH:
                        emergency_type = "collapse"
                        self._put_text(frame, "!! COLLAPSE DETECTED !!", 95, (0, 0, 255))
                else:
                    self._chest_hold_start = None

                # ── Passenger count overlay ───────────────────────────── #
                passengers = max(0, num_faces - 1)
                self._put_text(frame,
                               f"People in vehicle: {num_faces}  (Passengers: {passengers})",
                               130, (255, 255, 0))

                # ── Emergency dispatch ────────────────────────────────── #
                if emergency_type is not None:
                    if emergency_type == "driver_drowsy":
                        # Drowsiness is just a short local alarm, no emergency escalation
                        self.alert_system.trigger_alert(
                            "DROWSINESS_ALERT",
                            "Drowsiness detected. Please pay attention to the road.",
                            "high"
                        )
                    else:
                        if passengers > 0:
                            # Passengers present → alarm only
                            self.alert_system.trigger_alert(
                                "PASSENGER_ALARM",
                                f"Emergency detected ({emergency_type.replace('_',' ')}). "
                                "Passengers present - alarm activated.",
                                "high",
                            )
                        else:
                            # Driver alone → full escalation
                            self.alert_system.trigger_alert(
                                "DRIVER_ONLY_EMERGENCY",
                                f"Driver-only emergency: {emergency_type.replace('_',' ')}. "
                                "Activating full emergency workflow.",
                                "critical",
                            )
                            self.alert_system.escalate_driver_only_emergency(emergency_type)

                # ── Status bar ───────────────────────────────────────── #
                if emergency_type is not None:
                    status_label = f"EMERGENCY: {emergency_type.upper()}"
                    status_color = (0, 0, 255)
                elif num_faces == 0:
                    status_label = "No Driver Detected"
                    status_color = (0, 165, 255)
                else:
                    status_label = "Safe - Monitoring"
                    status_color = (0, 255, 100)

                self.status = status_label
                cv2.putText(frame, f"Status: {status_label}",
                            (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX,
                            0.65, status_color, 2, cv2.LINE_AA)

                ret, buf = cv2.imencode(".jpg", frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, 80])
                yield buf.tobytes()
                time.sleep(0.033)   # ~30 fps

        cap.release()
