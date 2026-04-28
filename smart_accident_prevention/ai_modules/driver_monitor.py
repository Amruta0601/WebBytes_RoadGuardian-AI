import cv2
import mediapipe as mp
import numpy as np
import time
from ai_modules.alert_system import AlertSystem
from scipy.spatial import distance as dist

mp_face_mesh = mp.solutions.face_mesh
mp_pose = mp.solutions.pose

class DriverMonitor:
    def __init__(self, socketio):
        self.alert_system = AlertSystem(socketio)
        self.status = "Monitoring"
        self.is_running = True
        
        # Thresholds
        self.EAR_THRESH = 0.22
        self.EAR_CONSEC_FRAMES = 15
        
        self.MAR_THRESH = 0.5
        self.MAR_CONSEC_FRAMES = 15

        self.blink_counter = 0
        self.yawn_counter = 0
        self.no_movement_counter = 0

    def calculate_ear(self, eye):
        # Calculate eye aspect ratio
        A = dist.euclidean(eye[1], eye[5])
        B = dist.euclidean(eye[2], eye[4])
        C = dist.euclidean(eye[0], eye[3])
        if C == 0:
            return 0.0
        ear = (A + B) / (2.0 * C)
        return ear

    def calculate_mar(self, mouth):
        # Calculate mouth aspect ratio
        A = dist.euclidean(mouth[2], mouth[10]) # 51, 59
        B = dist.euclidean(mouth[4], mouth[8])  # 53, 57
        C = dist.euclidean(mouth[0], mouth[6])  # 49, 55
        if C == 0:
            return 0.0
        mar = (A + B) / (2.0 * C)
        return mar

    def get_status(self):
        return self.status

    def generate_frames(self):
        cap = cv2.VideoCapture(0) # Use default camera
        
        with mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5) as face_mesh, \
            mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5) as pose:
            
            while self.is_running:
                success, frame = cap.read()
                if not success:
                    break
                
                # Flip frame horizontally for selfie-view
                frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Process MediaPipe
                results_mesh = face_mesh.process(rgb_frame)
                results_pose = pose.process(rgb_frame)
                
                h, w, _ = frame.shape
                
                current_status = "Safe"
                
                if results_mesh.multi_face_landmarks:
                    self.no_movement_counter = 0
                    for face_landmarks in results_mesh.multi_face_landmarks:
                        landmarks = []
                        for lm in face_landmarks.landmark:
                            landmarks.append([int(lm.x * w), int(lm.y * h)])
                            
                        # Extract Eye Landmarks (approximate indices for MediaPipe Face Mesh)
                        left_eye = [landmarks[362], landmarks[385], landmarks[387], landmarks[263], landmarks[373], landmarks[380]]
                        right_eye = [landmarks[33], landmarks[160], landmarks[158], landmarks[133], landmarks[153], landmarks[144]]
                        
                        ear_left = self.calculate_ear(left_eye)
                        ear_right = self.calculate_ear(right_eye)
                        ear = (ear_left + ear_right) / 2.0
                        
                        # Extract Mouth Landmarks
                        mouth = [landmarks[78], landmarks[81], landmarks[13], landmarks[311], landmarks[308], landmarks[402], landmarks[14], landmarks[178]]
                        # Simpler MAR for prototype
                        mar = 0.0
                        if len(landmarks) > 17:
                            mar = self.calculate_mar(mouth)
                            
                        # Logic for Drowsiness
                        if ear < self.EAR_THRESH:
                            self.blink_counter += 1
                            if self.blink_counter >= self.EAR_CONSEC_FRAMES:
                                current_status = "Drowsy"
                                cv2.putText(frame, "DROWSINESS DETECTED!", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                                self.alert_system.trigger_alert("DROWSINESS", "Warning: Driver drowsiness detected.", "high")
                        else:
                            self.blink_counter = 0
                            
                        # Draw eyes
                        for pt in left_eye + right_eye:
                            cv2.circle(frame, tuple(pt), 1, (0, 255, 0), -1)
                            
                else:
                    self.no_movement_counter += 1
                    if self.no_movement_counter > 50:
                        current_status = "No Driver"
                        self.alert_system.trigger_alert("NO_DRIVER", "Warning: Driver not detected.", "medium")

                # Pose detection (Chest pain / collapse)
                if results_pose.pose_landmarks:
                    landmarks = results_pose.pose_landmarks.landmark
                    # Calculate shoulder angle or sudden drop
                    nose_y = landmarks[mp_pose.PoseLandmark.NOSE.value].y
                    shoulder_y = (landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value].y + landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value].y) / 2
                    
                    if (shoulder_y - nose_y) < 0.1: # Head dropped close to shoulders
                        current_status = "Collapse/Emergency"
                        cv2.putText(frame, "EMERGENCY: COLLAPSE DETECTED!", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                        self.alert_system.emergency_call()
                
                self.status = current_status
                
                # Display Status
                cv2.putText(frame, f"Status: {self.status}", (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if self.status == "Safe" else (0, 0, 255), 2)

                ret, buffer = cv2.imencode('.jpg', frame)
                frame_bytes = buffer.tobytes()
                yield frame_bytes
                
                time.sleep(0.05) # Control framerate
                
        cap.release()
