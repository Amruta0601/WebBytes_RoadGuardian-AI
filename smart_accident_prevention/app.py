import os
import threading
from flask import Flask, render_template, Response, jsonify
from flask_socketio import SocketIO
from ai_modules.driver_monitor import DriverMonitor
from ai_modules.cctv_monitor import CCTVMonitor

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize AI monitors
driver_monitor = DriverMonitor(socketio)
cctv_monitor = CCTVMonitor(socketio)

@app.route('/')
def index():
    return render_template('index.html')

def gen_driver_frames():
    for frame in driver_monitor.generate_frames():
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

def gen_cctv_frames():
    for frame in cctv_monitor.generate_frames():
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/driver_video_feed')
def driver_video_feed():
    return Response(gen_driver_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/cctv_video_feed')
def cctv_video_feed():
    return Response(gen_cctv_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/status')
def status():
    return jsonify({
        "driver_status": driver_monitor.get_status(),
        "cctv_status": cctv_monitor.get_status()
    })

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5001)
