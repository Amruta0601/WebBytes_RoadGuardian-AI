import os
import time
import threading

from flask import Flask, render_template, Response, jsonify, request
from flask_socketio import SocketIO
from werkzeug.utils import secure_filename

from ai_modules.driver_monitor import DriverMonitor
from ai_modules.cctv_monitor import CCTVMonitor

# ── App setup ─────────────────────────────────────────────────────────────── #
app = Flask(__name__)
app.config["SECRET_KEY"] = "safeguard_ai_secret_2024"
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

ALLOWED_EXTS = {"mp4", "avi", "mov", "mkv", "webm"}

# ── AI modules ────────────────────────────────────────────────────────────── #
driver_monitor = DriverMonitor(socketio)
cctv_monitor   = CCTVMonitor(socketio)


# ── Routes ────────────────────────────────────────────────────────────────── #
@app.route("/")
def index():
    return render_template("index.html")


def _gen_driver():
    for frame in driver_monitor.generate_frames():
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"


def _gen_cctv():
    for frame in cctv_monitor.generate_frames():
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"


@app.route("/driver_video_feed")
def driver_video_feed():
    return Response(_gen_driver(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/cctv_video_feed")
def cctv_video_feed():
    return Response(_gen_cctv(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def api_status():
    return jsonify({
        "driver_status": driver_monitor.get_status(),
        "cctv_status":   cctv_monitor.get_status(),
    })


@app.route("/api/emergency_info")
def api_emergency_info():
    location = driver_monitor.alert_system.get_location()
    nearby   = driver_monitor.alert_system._get_nearby_services(location)
    return jsonify({
        "ok":              True,
        "location":        location,
        "nearby_services": nearby,
        "emergency_number": driver_monitor.alert_system.emergency_number,
        "voice_message":   (
            "Emergency voice message will be generated and read aloud "
            "automatically when a driver-only emergency is detected."
        ),
    })


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTS


@app.route("/api/upload_cctv_video", methods=["POST"])
def upload_cctv_video():
    if "video" not in request.files:
        return jsonify({"ok": False, "error": "No file part named 'video'"}), 400

    f = request.files["video"]
    if f.filename == "":
        return jsonify({"ok": False, "error": "No file selected"}), 400

    if not _allowed(f.filename):
        return jsonify({"ok": False,
                        "error": "Unsupported format. Use mp4, avi, mov, mkv, webm."}), 400

    safe    = secure_filename(f.filename)
    unique  = f"{int(time.time())}_{safe}"
    path    = os.path.join(app.config["UPLOAD_FOLDER"], unique)
    f.save(path)
    cctv_monitor.set_video_source(path)

    return jsonify({
        "ok":       True,
        "message":  f"Video '{safe}' uploaded. Accident detection started.",
        "filename": unique,
    })


# ── SocketIO events ───────────────────────────────────────────────────────── #
@socketio.on("trigger_sos")
def handle_sos(data):
    driver_monitor.alert_system.trigger_alert(
        "MANUAL_SOS",
        f"Manual SOS triggered by {data.get('user', 'Admin')}. Immediate assistance needed.",
        severity="critical",
    )
    driver_monitor.alert_system.escalate_driver_only_emergency("manual_sos")


@socketio.on("connect")
def handle_connect():
    # Send current emergency info on connect so UI is pre-populated
    location = driver_monitor.alert_system.get_location()
    nearby   = driver_monitor.alert_system._get_nearby_services(location)
    socketio.emit("emergency_escalation", {
        "incident_type":    "system_init",
        "location":         location,
        "nearby_services":  nearby,
        "emergency_number": driver_monitor.alert_system.emergency_number,
        "voice_message":    "System initialised. Monitoring active.",
        "timestamp":        time.strftime("%Y-%m-%d %H:%M:%S"),
    }, to=request.sid)


# ── Entry point ───────────────────────────────────────────────────────────── #
if __name__ == "__main__":
    print("=" * 55)
    print("  SafeGuard AI — Smart Accident Prevention System")
    print("  Running at  http://127.0.0.1:5001")
    print("=" * 55)
    socketio.run(
        app,
        host="0.0.0.0",
        port=5001,
        debug=False,
        allow_unsafe_werkzeug=True,
        use_reloader=False,
    )
