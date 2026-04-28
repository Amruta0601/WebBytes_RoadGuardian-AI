import cv2
import mediapipe as mp
import numpy as np
import threading
import time

from .alert_system import AlertSystem

mp_face_mesh = mp.solutions.face_mesh
mp_pose = mp.solutions.pose


class DriverMonitor:
    def __init__(self, socketio):
        self.alert_system = AlertSystem(socketio)
        self.status = "Monitoring"
        self.is_running = True
        self._frame_lock = threading.Lock()
        self._latest_jpeg = None
        self._worker = None
        self._worker_started = False
        self._cam_idx = None

        # Thresholds
        self.EAR_THRESH = 0.22
        self.EAR_CONSEC_FRAMES = 15

        self.MAR_THRESH = 0.5
        self.MAR_CONSEC_FRAMES = 15

        self.blink_counter = 0
        self.yawn_counter = 0
        self.no_movement_counter = 0
        self.posture_counter = 0
        self.position_counter = 0

    def _dist(self, a, b):
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        return float(np.linalg.norm(a - b))

    def calculate_ear(self, eye):
        # Calculate eye aspect ratio
        A = self._dist(eye[1], eye[5])
        B = self._dist(eye[2], eye[4])
        C = self._dist(eye[0], eye[3])
        if C == 0:
            return 0.0
        ear = (A + B) / (2.0 * C)
        return ear

    def calculate_mar(self, mouth):
        # Calculate mouth aspect ratio
        A = self._dist(mouth[2], mouth[10])  # 51, 59
        B = self._dist(mouth[4], mouth[8])  # 53, 57
        C = self._dist(mouth[0], mouth[6])  # 49, 55
        if C == 0:
            return 0.0
        mar = (A + B) / (2.0 * C)
        return mar

    def get_status(self):
        return self.status

    def _open_camera(self):
        # Windows: DirectShow backend is typically the most reliable.
        backends = [cv2.CAP_DSHOW, cv2.CAP_ANY]
        indices = [0, 1, 2]
        for backend in backends:
            for idx in indices:
                cap = cv2.VideoCapture(idx, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                # quick probe
                ok, _ = cap.read()
                if ok:
                    return cap, idx
                cap.release()
        return None, None

    def generate_frames(self):
        if not self._worker_started:
            self._worker_started = True
            self._worker = threading.Thread(target=self._run_loop, daemon=True)
            self._worker.start()

        while self.is_running:
            with self._frame_lock:
                frame = self._latest_jpeg
            if frame:
                yield frame
            time.sleep(0.05)

    def _run_loop(self):
        cap, cam_idx = self._open_camera()
        self._cam_idx = cam_idx
        if cap is None:
            self.status = "Camera busy / not available"
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                "Camera busy or not available. Close other apps using camera.",
                (14, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (0, 0, 255),
                2,
            )
            ret, buffer = cv2.imencode(".jpg", frame)
            with self._frame_lock:
                self._latest_jpeg = buffer.tobytes()
            return

        with mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as face_mesh, mp_pose.Pose(
            min_detection_confidence=0.5, min_tracking_confidence=0.5
        ) as pose:
            while self.is_running:
                success, frame = cap.read()
                if not success:
                    time.sleep(0.05)
                    continue

                frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                results_mesh = face_mesh.process(rgb_frame)
                results_pose = pose.process(rgb_frame)

                h, w, _ = frame.shape
                current_status = "Safe"
                eye_state = "Alert"
                chest_state = "Normal"
                position_state = "Centered"

                if results_mesh.multi_face_landmarks:
                    self.no_movement_counter = 0
                    for face_landmarks in results_mesh.multi_face_landmarks:
                        landmarks = []
                        for lm in face_landmarks.landmark:
                            landmarks.append([int(lm.x * w), int(lm.y * h)])

                        left_eye = [
                            landmarks[362],
                            landmarks[385],
                            landmarks[387],
                            landmarks[263],
                            landmarks[373],
                            landmarks[380],
                        ]
                        right_eye = [
                            landmarks[33],
                            landmarks[160],
                            landmarks[158],
                            landmarks[133],
                            landmarks[153],
                            landmarks[144],
                        ]

                        ear_left = self.calculate_ear(left_eye)
                        ear_right = self.calculate_ear(right_eye)
                        ear = (ear_left + ear_right) / 2.0

                        if ear < self.EAR_THRESH:
                            self.blink_counter += 1
                            if self.blink_counter >= self.EAR_CONSEC_FRAMES:
                                current_status = "Drowsy"
                                eye_state = "Drowsy"
                                cv2.putText(
                                    frame,
                                    "DROWSINESS DETECTED!",
                                    (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.7,
                                    (0, 0, 255),
                                    2,
                                )
                                self.alert_system.trigger_alert(
                                    "DROWSINESS",
                                    "Warning: Driver drowsiness detected.",
                                    "high",
                                )
                        else:
                            self.blink_counter = 0
                            eye_state = "Alert"

                        for pt in left_eye + right_eye:
                            cv2.circle(frame, tuple(pt), 1, (0, 255, 0), -1)

                else:
                    self.no_movement_counter += 1
                    if self.no_movement_counter > 50:
                        current_status = "No Driver"
                        self.alert_system.trigger_alert(
                            "NO_DRIVER", "Warning: Driver not detected.", "medium"
                        )

                if results_pose.pose_landmarks:
                    landmarks = results_pose.pose_landmarks.landmark
                    nose_y = landmarks[mp_pose.PoseLandmark.NOSE.value].y
                    nose_x = landmarks[mp_pose.PoseLandmark.NOSE.value].x
                    left_shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
                    right_shoulder = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
                    shoulder_y = (
                        left_shoulder.y
                        + right_shoulder.y
                    ) / 2
                    shoulder_x = (left_shoulder.x + right_shoulder.x) / 2
                    shoulder_tilt = abs(left_shoulder.y - right_shoulder.y)
                    horizontal_offset = abs(nose_x - shoulder_x)

                    # Chest posture: shoulder tilt generally increases when driver slouches/leans.
                    if shoulder_tilt > 0.12:
                        self.posture_counter += 1
                        chest_state = "Tilted"
                        if self.posture_counter > 12:
                            current_status = "Poor Chest Posture"
                            self.alert_system.trigger_alert(
                                "POSTURE_WARNING",
                                "Driver chest posture appears tilted. Please sit upright.",
                                "medium",
                            )
                    else:
                        self.posture_counter = max(0, self.posture_counter - 1)
                        chest_state = "Normal"

                    # Driving position: head excessively off-center can indicate unsafe posture.
                    if horizontal_offset > 0.17:
                        self.position_counter += 1
                        position_state = "Off-Center"
                        if self.position_counter > 12:
                            current_status = "Unsafe Driving Position"
                            self.alert_system.trigger_alert(
                                "DRIVING_POSITION",
                                "Driver position is off-center. Please align with seat and wheel.",
                                "medium",
                            )
                    else:
                        self.position_counter = max(0, self.position_counter - 1)
                        position_state = "Centered"

                    if (shoulder_y - nose_y) < 0.1:
                        current_status = "Collapse/Emergency"
                        chest_state = "Collapse Risk"
                        cv2.putText(
                            frame,
                            "EMERGENCY: COLLAPSE DETECTED!",
                            (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (0, 0, 255),
                            2,
                        )
                        self.alert_system.emergency_call()

                self.status = current_status
                cv2.putText(
                    frame,
                    f"Status: {self.status} | Cam: {cam_idx}",
                    (10, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0) if self.status == "Safe" else (0, 0, 255),
                    2,
                )
                cv2.putText(
                    frame,
                    f"Eyes: {eye_state} | Chest: {chest_state} | Position: {position_state}",
                    (10, h - 45),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (255, 255, 0),
                    2,
                )

                ret, buffer = cv2.imencode(".jpg", frame)
                with self._frame_lock:
                    self._latest_jpeg = buffer.tobytes()
                time.sleep(0.03)

        cap.release()

