import geocoder
import pyttsx3
import threading
import time

class AlertSystem:
    def __init__(self, socketio):
        self.socketio = socketio
        self.engine = pyttsx3.init()
        # Ensure the engine operates without blocking the main thread significantly
        self.engine.setProperty('rate', 150)
        self.last_alert_time = 0
        self.cooldown = 5 # Prevent spamming alerts

    def get_location(self):
        try:
            g = geocoder.ip('me')
            if g.latlng:
                return {"lat": g.latlng[0], "lng": g.latlng[1], "address": g.city}
        except Exception as e:
            print(f"Error getting location: {e}")
        # Mock fallback location
        return {"lat": 28.6139, "lng": 77.2090, "address": "New Delhi (Mock)"}

    def _speak_alert(self, message):
        try:
            # Re-init engine inside thread to prevent issues on some OS
            engine = pyttsx3.init()
            engine.say(message)
            engine.runAndWait()
        except Exception as e:
            print(f"TTS Error: {e}")

    def trigger_alert(self, alert_type, message, severity="high"):
        current_time = time.time()
        if current_time - self.last_alert_time < self.cooldown:
            return # Skip if too recent
        
        self.last_alert_time = current_time
        location = self.get_location()
        
        # 1. Emit to Frontend
        alert_data = {
            "type": alert_type,
            "message": message,
            "severity": severity,
            "location": location,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.socketio.emit('new_alert', alert_data)
        
        # 2. Voice Alert
        if severity == "high" or severity == "critical":
            threading.Thread(target=self._speak_alert, args=(message,), daemon=True).start()
            
        print(f"🚨 ALERT TRIGGERED: {message} at {location}")
        
    def emergency_call(self):
        message = "Emergency! This vehicle has met with an accident and requires immediate emergency assistance."
        self.trigger_alert("CRASH_DETECTED", message, severity="critical")
