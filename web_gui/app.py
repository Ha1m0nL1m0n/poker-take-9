#!/usr/bin/env python3
"""
ClubGG Table Detection & HUD Visualizer - Flask Web Application
==============================================================
Provides real-time interactive visualization of:
  - Table configuration (8-max / 6-max, blinds, game type)
  - Community cards rendered with custom pixel card pack assets
  - 8 Player seats with HUD metrics: stack, VPIP, username, in-hand, position, action
  - Dealer button localization and betting felt chips
  - Dual-view mode: Virtual Poker Table + Source Screencap with detection bounding boxes
  - Data integrity and card uniqueness validator
  - Live ADB capture and historical capture browsing
"""

import glob
import os
import re
import subprocess
import sys
import time
from typing import Dict, Any, Optional

from flask import Flask, render_template, jsonify, request, send_from_directory

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from table_detector import ClubGGTableDetector
from card_detector import CardDetector

app = Flask(__name__, template_folder="templates", static_folder="static")

# Shared detector instances
detector = ClubGGTableDetector()
card_detector = CardDetector()

# Cache latest detection state
latest_state: Dict[str, Any] = {}
latest_image_path: Optional[str] = None

CAPTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "captures"))


def get_default_capture() -> Optional[str]:
    """Finds the most recent screenshot in captures/."""
    if not os.path.exists(CAPTURES_DIR):
        return None
    files = glob.glob(os.path.join(CAPTURES_DIR, "*.png"))
    if not files:
        return None
    # Sort by modification time descending
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


@app.route("/")
def index():
    """Renders the main poker table HUD dashboard."""
    return render_template("index.html")


@app.route("/api/table_state")
def get_table_state():
    """Returns the current table state JSON."""
    global latest_state, latest_image_path
    if not latest_state:
        # Load default capture on startup
        default_cap = get_default_capture()
        if default_cap and os.path.exists(default_cap):
            latest_image_path = os.path.basename(default_cap)
            state = detector.detect_table_state(default_cap)
            latest_state = state.to_dict()

    return jsonify({
        "success": True,
        "image_name": latest_image_path,
        "image_url": f"/captures/{latest_image_path}" if latest_image_path else None,
        "state": latest_state
    })


@app.route("/api/captures_list")
def get_captures_list():
    """Returns a list of all screenshots available in captures/."""
    if not os.path.exists(CAPTURES_DIR):
        return jsonify({"captures": []})
    
    files = glob.glob(os.path.join(CAPTURES_DIR, "*.png"))
    files.sort(key=os.path.getmtime, reverse=True)
    
    captures = []
    for f in files[:100]:  # Limit to 100 most recent
        fname = os.path.basename(f)
        captures.append({
            "filename": fname,
            "size_kb": round(os.path.getsize(f) / 1024, 1),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(f)))
        })

    return jsonify({"captures": captures})


@app.route("/api/analyze_capture", methods=["POST"])
def analyze_capture():
    """Analyzes a specific capture file from captures/."""
    global latest_state, latest_image_path
    data = request.get_json() or {}
    filename = data.get("filename")

    if not filename:
        return jsonify({"success": False, "error": "No filename provided"}), 400

    safe_path = os.path.abspath(os.path.join(CAPTURES_DIR, filename))
    if not safe_path.startswith(CAPTURES_DIR) or not os.path.exists(safe_path):
        return jsonify({"success": False, "error": "File not found"}), 404

    t0 = time.time()
    state = detector.detect_table_state(safe_path)
    elapsed = round(time.time() - t0, 3)

    latest_state = state.to_dict()
    latest_state["analysis_time_sec"] = elapsed
    latest_image_path = filename

    return jsonify({
        "success": True,
        "image_name": latest_image_path,
        "image_url": f"/captures/{latest_image_path}",
        "state": latest_state
    })


@app.route("/api/capture_live", methods=["POST"])
def capture_live():
    """Captures a live hardware screencap via ADB and analyzes it immediately."""
    global latest_state, latest_image_path
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    
    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    live_filename = f"live_{timestamp_str}.png"
    target_path = os.path.join(CAPTURES_DIR, live_filename)

    try:
        # Run ADB hardware screencap
        cmd = ["adb", "exec-out", "screencap", "-p"]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if proc.returncode != 0 or len(proc.stdout) < 1000:
            return jsonify({"success": False, "error": f"ADB capture failed: {proc.stderr.decode()}"}), 500

        with open(target_path, "wb") as f:
            f.write(proc.stdout)

        t0 = time.time()
        state = detector.detect_table_state(target_path)
        elapsed = round(time.time() - t0, 3)

        latest_state = state.to_dict()
        latest_state["analysis_time_sec"] = elapsed
        latest_image_path = live_filename

        return jsonify({
            "success": True,
            "image_name": latest_image_path,
            "image_url": f"/captures/{latest_image_path}",
            "state": latest_state
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/captures/<path:filename>")
def serve_capture(filename):
    """Serves raw screenshot image files."""
    return send_from_directory(CAPTURES_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"[*] Starting ClubGG Poker Table HUD Visualizer on http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
