"""
╔═══════════════════════════════════════════════════════════════════════╗
║       SPRINGBOARD AUTO — WEB SERVER (Flask + SSE Live Logs)         ║
║                                                                     ║
║  Provides a beautiful web UI for the Playwright automation.         ║
║  Users enter credentials → automation runs → live logs stream.      ║
║                                                                     ║
║  Usage:  python app.py                                              ║
║  Open:   http://localhost:5000                                      ║
╚═══════════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import time
import json
import re
import queue
import threading
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response

# ── Import the automation engine ─────────────────────────────────────
from springboard_engine import SpringboardAutomation

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ── Global state for active sessions ─────────────────────────────────
sessions = {}   # session_id -> { "status", "logs_queue", "thread", "engine" }


# ═══════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Serve the main page."""
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_automation():
    """Start a new automation session."""
    data = request.json
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    course_url = data.get("course_url", "").strip()
    headless = data.get("headless", False)

    if not email or not password or not course_url:
        return jsonify({"error": "All fields are required."}), 400

    session_id = str(uuid.uuid4())[:8]
    log_queue = queue.Queue()

    def push_log(msg, level):
        source = None
        clean_msg = msg
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", msg)
        if m:
            source = m.group(1)
            clean_msg = m.group(2)

        log_queue.put({
            "msg": clean_msg,
            "level": level,
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": source,
        })

    engine = SpringboardAutomation(
        email=email,
        password=password,
        course_url=course_url,
        headless=headless,
        log_callback=push_log,
    )

    sessions[session_id] = {
        "status": "running",
        "logs_queue": log_queue,
        "engine": engine,
        "thread": None,
    }

    def run_engine():
        try:
            engine.run()
            sessions[session_id]["status"] = "completed"
            push_log("Automation finished!", "OK")
        except Exception as e:
            sessions[session_id]["status"] = "error"
            push_log(f"Fatal error: {e}", "ERR")
        finally:
            log_queue.put(None)  # Sentinel to close the SSE stream

    thread = threading.Thread(target=run_engine, daemon=True)
    sessions[session_id]["thread"] = thread
    thread.start()

    return jsonify({"session_id": session_id, "status": "started"})


@app.route("/api/logs/<session_id>")
def stream_logs(session_id):
    """SSE endpoint — streams live log messages to the frontend."""
    if session_id not in sessions:
        return jsonify({"error": "Session not found."}), 404

    def generate():
        q = sessions[session_id]["logs_queue"]
        while True:
            try:
                item = q.get(timeout=30)
                if item is None:
                    # Send final status
                    status = sessions[session_id]["status"]
                    yield f"data: {json.dumps({'type': 'done', 'status': status})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'log', **item})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/stop/<session_id>", methods=["POST"])
def stop_automation(session_id):
    """Stop a running automation session."""
    if session_id not in sessions:
        return jsonify({"error": "Session not found."}), 404

    engine = sessions[session_id].get("engine")
    if engine:
        engine.stop()
    sessions[session_id]["status"] = "stopped"
    return jsonify({"status": "stopped"})


@app.route("/api/status/<session_id>")
def get_status(session_id):
    """Get current status of a session."""
    if session_id not in sessions:
        return jsonify({"error": "Session not found."}), 404
    return jsonify({"status": sessions[session_id]["status"]})


# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print()
    print("  ===================================================")
    print(f"  |   [>] Springboard Auto — Web Server (port {port})   |")
    print("  ===================================================")
    print()
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
