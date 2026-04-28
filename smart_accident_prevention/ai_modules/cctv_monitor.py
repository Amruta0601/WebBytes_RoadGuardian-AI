import cv2
import time
import numpy as np
from ai_modules.alert_system import AlertSystem

class CCTVMonitor:
    def __init__(self, socketio):
        self.alert_system = AlertSystem(socketio)
        self.status = "Monitoring"
        self.is_running = True
        
        # Background subtractor for motion detection (simulating vehicle tracking)
        self.fgbg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=True)
        self.anomaly_counter = 0

    def get_status(self):
        return self.status

    def generate_frames(self):
        # In a real scenario, this would be an RTSP stream from a CCTV camera.
        # Here we use a video file or the webcam if a file isn't provided.
        cap = cv2.VideoCapture(0) # Change to a video path if needed
        
        while self.is_running:
            success, frame = cap.read()
            if not success:
                # If using a video file, loop it.
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
                
            frame = cv2.resize(frame, (640, 480))
            
            # Apply background subtraction
            fgmask = self.fgbg.apply(frame)
            
            # Remove noise
            kernel = np.ones((5,5), np.uint8)
            fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_OPEN, kernel)
            
            # Find contours
            contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            current_status = "Normal Traffic"
            large_motion_detected = False
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area > 5000: # Very large sudden area could indicate a crash / anomaly
                    large_motion_detected = True
                    x, y, w, h = cv2.boundingRect(contour)
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 2)
                    cv2.putText(frame, "ANOMALY", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
            
            if large_motion_detected:
                self.anomaly_counter += 1
                if self.anomaly_counter > 10:
                    current_status = "CRASH / ANOMALY DETECTED"
                    cv2.putText(frame, "CCTV EMERGENCY DETECTED!", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                    self.alert_system.trigger_alert("CCTV_CRASH", "Abnormal movement detected on CCTV Camera 1.", "critical")
            else:
                self.anomaly_counter = max(0, self.anomaly_counter - 1)
                
            self.status = current_status
            
            # Overlay status
            cv2.putText(frame, f"CCTV Status: {self.status}", (10, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if self.status == "Normal Traffic" else (0, 0, 255), 2)

            ret, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            yield frame_bytes
            
            time.sleep(0.05)
            
        cap.release()
