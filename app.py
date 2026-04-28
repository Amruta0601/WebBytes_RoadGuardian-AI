import os
import threading
import time
from flask import Flask, render_template, Response, jsonify, request
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename
from ai_modules.driver_monitor import DriverMonitor
from ai_modules.cctv_monitor import CCTVMonitor

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads")
socketio = SocketIO(app, cors_allowed_origins="*")

ALLOWED_VIDEO_EXTENSIONS = {"mp4", "avi", "mov", "mkv", "webm"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

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


def allowed_video_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


@app.route("/api/upload_cctv_video", methods=["POST"])
def upload_cctv_video():
    if "video" not in request.files:
        return jsonify({"ok": False, "error": "No file part named 'video'"}), 400

    file = request.files["video"]
    if file.filename == "":
        return jsonify({"ok": False, "error": "No file selected"}), 400

    if not allowed_video_file(file.filename):
        return jsonify(
            {
                "ok": False,
                "error": "Unsupported format. Use mp4, avi, mov, mkv, or webm.",
            }
        ), 400

    safe_name = secure_filename(file.filename)
    unique_name = f"{int(time.time())}_{safe_name}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file.save(save_path)
    cctv_monitor.set_video_source(save_path)

    return jsonify(
        {
            "ok": True,
            "message": "Traffic sample uploaded. CCTV stream switched to uploaded video.",
            "filename": unique_name,
        }
    )


@socketio.on("trigger_sos")
def handle_trigger_sos(data):
    # Trigger a real alert so all connected clients receive it.
    driver_monitor.alert_system.trigger_alert(
        "MANUAL_SOS",
        f"Manual emergency trigger activated by {data.get('user', 'user')}.",
        severity="critical",
    )

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5001, allow_unsafe_werkzeug=True)
