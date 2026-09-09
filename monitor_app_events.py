#!/usr/bin/env python3
"""
Android App Sound & Vibration Real-Time Event Monitor & Recorder
================================================================
Monitors and logs Android app events in real time:
  1. Vibration / Haptic Feedback (via dumpsys vibrator_manager)
     - Timestamp, duration (ms), effect/waveform pattern, calling package, UID
  2. Sound / Audio Playback (via dumpsys media.audio_flinger)
     - Timestamp, track ID, sample rate, active playback state, calling package

AUTOMATIC SCREENSHOT CAPTURE:
  Whenever a sound starts or a vibration is fired:
  - Takes an instant pixel-perfect centered screenshot (scrcpy or ADB screencap)
  - Optionally takes a follow-up delayed screenshot (+500ms)
  - Saves all events and linked screenshots to a structured JSON file

Requires NO root access and works over USB or Wi-Fi ADB.
"""

import argparse
import ctypes
from ctypes import wintypes
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

try:
    from table_detector import ClubGGTableDetector
    TABLE_DETECTOR_AVAILABLE = True
except Exception:
    TABLE_DETECTOR_AVAILABLE = False


# ---------------------------------------------------------
# ADB & Device Management
# ---------------------------------------------------------
def check_adb():
    try:
        subprocess.run(["adb", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        print("[ERROR] adb is not found in PATH. Please install Android platform-tools.")
        sys.exit(1)

def get_devices():
    try:
        out = subprocess.check_output(["adb", "devices"], text=True, stderr=subprocess.DEVNULL)
        lines = [line.strip() for line in out.strip().splitlines()[1:] if line.strip()]
        return [line.split()[0] for line in lines if len(line.split()) >= 2 and line.split()[1] == "device"]
    except Exception:
        return []

def select_device(serial=None):
    devices = get_devices()
    if not devices:
        print("[WARN] No authorized devices currently detected. Waiting for device connection...")
        while not devices:
            time.sleep(1.0)
            devices = get_devices()
        print(f"[*] Device connected: {devices[0]}")
    if serial:
        if serial in devices:
            return serial
        print(f"[ERROR] Specified device '{serial}' not found. Available: {devices}")
        sys.exit(1)
    return devices[0]

def get_screen_size(serial):
    try:
        out = subprocess.check_output(["adb", "-s", serial, "shell", "wm", "size"], text=True)
        m = re.search(r'Override size: (\d+)x(\d+)', out)
        if not m:
            m = re.search(r'Physical size: (\d+)x(\d+)', out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 1008, 2244

# ---------------------------------------------------------
# Windows GDI+ Screen Capture Engine
# ---------------------------------------------------------
def attach_to_interactive_desktop():
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    h_winsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
    if h_winsta:
        user32.SetProcessWindowStation(h_winsta)
        h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)

def find_scrcpy_windows():
    if sys.platform != "win32":
        return []
    attach_to_interactive_desktop()
    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    
    sdl_matches = []
    other_matches = []
    def enum_cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            class_buff = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buff, 256)
            cls = class_buff.value.lower()
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                t_lower = title.lower()
                if "sdl" in cls:
                    sdl_matches.append((hwnd, title))
                elif ("scrcpy" in t_lower or "pixel" in t_lower) and "console" not in cls and "cascadia" not in cls:
                    other_matches.append((hwnd, title))
        return True
    user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
    return sdl_matches if sdl_matches else other_matches

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def get_viewport_metrics(client_w, client_h, screen_w, screen_h):
    if client_w <= 0 or client_h <= 0:
        return 0, 0, client_w, client_h
    screen_aspect = screen_w / screen_h
    client_aspect = client_w / client_h
    if client_aspect > screen_aspect:
        rendered_w = int(round(client_h * screen_aspect))
        rendered_h = client_h
        vp_x = int(round((client_w - rendered_w) / 2.0))
        vp_y = 0
    else:
        rendered_w = client_w
        rendered_h = int(round(client_w / screen_aspect))
        vp_x = 0
        vp_y = int(round((client_h - rendered_h) / 2.0))
    return vp_x, vp_y, rendered_w, rendered_h

class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32),
                ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", ctypes.c_bool),
                ("SuppressExternalCodecs", ctypes.c_bool)]

class CLSID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

PNG_CLSID = CLSID(0x557cf406, 0x1a04, 0x11d3, (ctypes.c_ubyte * 8)(0x9a, 0x73, 0x00, 0x00, 0xf8, 0x1e, 0xf3, 0x2e))

class CaptureManager:
    def __init__(self, hwnd, serial, screen_w, screen_h, capture_dir="captures", delayed_ms=500, enabled=True, capture_source="scrcpy"):
        self.hwnd = hwnd
        self.serial = serial
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.capture_dir = capture_dir
        self.delayed_ms = delayed_ms
        self.delayed_sec = delayed_ms / 1000.0 if delayed_ms else None
        self.enabled = enabled
        self.capture_source = capture_source
        self.gdi_lock = threading.Lock()
        self.active_timers = []
        self.gdi_token = ctypes.c_ulong()
        self.gdiplus_ready = False
        
        if self.enabled:
            os.makedirs(self.capture_dir, exist_ok=True)
            if sys.platform == "win32":
                try:
                    startup_input = GdiplusStartupInput(1, None, False, False)
                    ctypes.windll.gdiplus.GdiplusStartup(ctypes.byref(self.gdi_token), ctypes.byref(startup_input), None)
                    self.gdiplus_ready = True
                except Exception as e:
                    print(f"[WARN] Failed to initialize GDI+: {e}")

    def capture_now(self, out_path):
        if not self.enabled:
            return False
        with self.gdi_lock:
            if self.capture_source == "adb":
                return self._capture_adb_screencap(self.serial, out_path)
            elif self.hwnd and self.gdiplus_ready:
                return self._capture_hwnd_centered(self.hwnd, out_path)
            elif self.serial:
                return self._capture_adb_screencap(self.serial, out_path)
            return False

    def _capture_hwnd_centered(self, hwnd, out_path):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        gdiplus = ctypes.windll.gdiplus
        
        wrect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wrect))
        win_w = wrect.right - wrect.left
        win_h = wrect.bottom - wrect.top
        if win_w <= 0 or win_h <= 0:
            return False
            
        crect = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(crect))
        cw = crect.right - crect.left
        ch = crect.bottom - crect.top
        if cw <= 0 or ch <= 0:
            return False
            
        pt = POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        off_x = pt.x - wrect.left
        off_y = pt.y - wrect.top
        
        vp_x, vp_y, rw, rh = get_viewport_metrics(cw, ch, self.screen_w, self.screen_h)
        
        screen_dc = user32.GetDC(0)
        full_dc = gdi32.CreateCompatibleDC(screen_dc)
        full_bmp = gdi32.CreateCompatibleBitmap(screen_dc, win_w, win_h)
        old_full = gdi32.SelectObject(full_dc, full_bmp)
        
        user32.PrintWindow(hwnd, full_dc, 2)
        
        crop_dc = gdi32.CreateCompatibleDC(screen_dc)
        crop_bmp = gdi32.CreateCompatibleBitmap(screen_dc, rw, rh)
        old_crop = gdi32.SelectObject(crop_dc, crop_bmp)
        
        gdi32.BitBlt(crop_dc, 0, 0, rw, rh, full_dc, off_x + vp_x, off_y + vp_y, 0x00CC0020)
        
        bmp_ptr = ctypes.c_void_p()
        gdiplus.GdipCreateBitmapFromHBITMAP(crop_bmp, 0, ctypes.byref(bmp_ptr))
        gdiplus.GdipSaveImageToFile(bmp_ptr, ctypes.c_wchar_p(out_path), ctypes.byref(PNG_CLSID), None)
        
        gdiplus.GdipDisposeImage(bmp_ptr)
        gdi32.SelectObject(crop_dc, old_crop)
        gdi32.DeleteObject(crop_bmp)
        gdi32.DeleteDC(crop_dc)
        gdi32.SelectObject(full_dc, old_full)
        gdi32.DeleteObject(full_bmp)
        gdi32.DeleteDC(full_dc)
        user32.ReleaseDC(0, screen_dc)
        return True

    def _capture_adb_screencap(self, serial, out_path):
        try:
            with open(out_path, "wb") as f:
                subprocess.run(["adb", "-s", serial, "exec-out", "screencap", "-p"], stdout=f, check=True)
            return True
        except Exception:
            return False

    def trigger(self, event_idx, event_type="event"):
        if not self.enabled:
            return None, None
        event_file = os.path.join(self.capture_dir, f"event_{event_idx:03d}_{event_type}.png")
        delayed_file = None
        
        # 1. Immediate capture
        t_event = threading.Thread(target=self.capture_now, args=(event_file,), daemon=True)
        t_event.start()
        
        # 2. Delayed capture (if configured)
        if self.delayed_sec and self.delayed_sec > 0:
            delayed_file = os.path.join(self.capture_dir, f"event_{event_idx:03d}_{event_type}_delayed_{self.delayed_ms}ms.png")
            t_delayed = threading.Timer(self.delayed_sec, self.capture_now, args=(delayed_file,))
            t_delayed.daemon = True
            self.active_timers.append(t_delayed)
            t_delayed.start()
            
        return event_file, delayed_file

    def close(self):
        for t in list(self.active_timers):
            if self.delayed_sec:
                t.join(timeout=self.delayed_sec + 0.3)
        if self.gdiplus_ready:
            try:
                ctypes.windll.gdiplus.GdiplusShutdown(self.gdi_token)
            except Exception:
                pass

# ---------------------------------------------------------
# Indexing & File Helpers
# ---------------------------------------------------------
def get_next_event_index(capture_dir):
    if not os.path.exists(capture_dir):
        return 1
    max_idx = 0
    pattern = re.compile(r'event_(\d+)_')
    try:
        for f in os.listdir(capture_dir):
            m = pattern.search(f)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    except Exception:
        pass
    return max_idx + 1

def get_unique_output_path(base_path, overwrite=False):
    if overwrite or not os.path.exists(base_path):
        return base_path
    root, ext = os.path.splitext(base_path)
    i = 1
    while os.path.exists(f"{root}_{i}{ext}"):
        i += 1
    return f"{root}_{i}{ext}"

# ---------------------------------------------------------
# Regex & Parsing Helpers
# ---------------------------------------------------------
VIB_REGEX = re.compile(
    r'(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*duration:\s*(\d+)ms'
    r'.*?\|\s*([\w\.]+)\s*\(uid=(\d+)'
    r'.*?\|\s*played:\s*([^|\n]+)'
)

TRACK_REGEX = re.compile(
    r'Track-(\d+):\s+start:\s+(\S+)\s+\((active|idle)\)\s+uid:\s+(\d+)\s+([\w\.]+)'
)

def parse_audio_data(raw_audio):
    """
    Parses audio data from dumpsys media.audio_flinger:
    Returns:
      active_packages: set of package names currently active / playing audio
      bursts: list of { 'start_time': str, 'samples': list[float], 'sum_db': float, 'duration_ms': int }
      tracks: dict of track_id -> { 'start_time': str, 'state': str, 'uid': int, 'pkg': str }
    """
    active_packages = set()
    # 1. Power Clients
    for m in re.finditer(r'\(active\)\s+uid:\s+(\d+)\s+([\w\.]+)', raw_audio):
        active_packages.add(m.group(2))
    
    # 2. AudioTracks
    tracks = {}
    for m in TRACK_REGEX.finditer(raw_audio):
        trk_id, t_start, state, uid, pkg = m.groups()
        tracks[trk_id] = {
            "track_id": trk_id,
            "start_time": t_start,
            "state": state,
            "uid": int(uid) if uid.isdigit() else uid,
            "pkg": pkg
        }
        if state == "active":
            active_packages.add(pkg)

    # 3. 50ms Signal Power Bursts (Real-time sound effects)
    bursts = []
    in_50ms = False
    current_burst = None
    
    for line in raw_audio.splitlines():
        if 'Signal power history (resolution: 50.0 ms):' in line:
            in_50ms = True
            continue
        if in_50ms:
            if 'Thread throttle time' in line or 'FastMixer thread' in line or '=== YAML START' in line:
                in_50ms = False
                continue
            m_ts = re.search(r'(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}):\s*(.*)', line)
            if m_ts:
                ts, content = m_ts.groups()
                if '[' in content:
                    current_burst = {'start_time': ts, 'samples': [], 'sum_db': None}
                    payload = content.split('[', 1)[1]
                else:
                    payload = content
                if current_burst is not None:
                    num_part = payload.split(']')[0]
                    nums = re.findall(r'-?\d+\.\d+', num_part)
                    current_burst['samples'].extend([float(n) for n in nums])
                    if ']' in payload:
                        m_sum = re.search(r'sum\(([-\d\.]+)\)', payload)
                        if m_sum:
                            current_burst['sum_db'] = float(m_sum.group(1))
                        current_burst['duration_ms'] = len(current_burst['samples']) * 50
                        bursts.append(current_burst)
                        current_burst = None
                        
    return active_packages, bursts, tracks

def extract_foreground_package(raw_window_dump):
    if not raw_window_dump:
        return None
    for line in raw_window_dump.splitlines():
        if "mCurrentFocus" in line and "/" in line:
            m = re.search(r'Window\{\S+\s+\S+\s+([\w\.]+)/', line)
            if m:
                return m.group(1)
    return None

# ---------------------------------------------------------
# Main Monitoring Loop
# ---------------------------------------------------------
def monitor_and_record(serial, target_package="com.nsus.clubgg", output_file="app_events.json",
                       capture_dir="captures", delayed_ms=500, no_capture=False,
                       capture_source="scrcpy", poll_interval=0.15, audio_debounce_sec=0.35,
                       detect_table=False):
    
    screen_w, screen_h = get_screen_size(serial)
    scrcpy_wins = find_scrcpy_windows() if sys.platform == "win32" else []
    scrcpy_hwnd = scrcpy_wins[0][0] if scrcpy_wins else None
    scrcpy_title = scrcpy_wins[0][1] if scrcpy_wins else "Not found"
    
    enable_capture = not no_capture
    start_idx = get_next_event_index(capture_dir) if enable_capture else 1
    event_idx = start_idx
    
    table_detector = ClubGGTableDetector() if (detect_table and TABLE_DETECTOR_AVAILABLE) else None
    
    capture_mgr = CaptureManager(
        hwnd=scrcpy_hwnd,
        serial=serial,
        screen_w=screen_w,
        screen_h=screen_h,
        capture_dir=capture_dir,
        delayed_ms=delayed_ms,
        enabled=enable_capture,
        capture_source=capture_source
    )
    
    engine_desc = f"{'Direct ADB Screencap (1008x2244)' if capture_source == 'adb' else 'scrcpy Centered Window'}"
    if delayed_ms:
        engine_desc += f" [instant + {delayed_ms}ms delayed]"
        
    print("=" * 75)
    print("       ANDROID APP SOUND & VIBRATION EVENT RECORDER")
    print("=" * 75)
    print(f" Device Serial    : {serial}")
    print(f" Target App       : {target_package if target_package else 'ALL APPS'}")
    print(f" Screen Size      : {screen_w} x {screen_h}")
    print(f" scrcpy Window    : {scrcpy_title} (HWND: {scrcpy_hwnd})")
    print(f" Screenshot Engine: {engine_desc if enable_capture else 'OFF'}")
    print(f" Poker Table HUD  : {'ENABLED (Real-time seat/pot/stack detection)' if table_detector else 'OFF'}")
    if enable_capture:
        print(f" Captures Dir     : ./{capture_dir}/ (Starting at event_{start_idx:03d})")
    print(f" Output JSON File : {output_file}")
    print(f" Polling Interval : {int(poll_interval * 1000)}ms")
    print(f" Audio Debounce   : {int(audio_debounce_sec * 1000)}ms")
    print("-" * 75)
    print(" [*] Initializing baseline device state...")

    recorded_events = []
    seen_vibrations = set()
    seen_audio_bursts = set()
    active_audio_tracks = {} # track_id -> info dict
    last_audio_burst_time = 0.0

    def analyze_table_async(img_path, ev_record):
        if not table_detector or not img_path:
            return
        # Wait up to 1.5s for capture file (especially delayed frames) to be completely written
        for _ in range(35):
            if os.path.exists(img_path) and os.path.getsize(img_path) > 1000:
                break
            time.sleep(0.04)
        try:
            t_state = table_detector.detect_table_state(img_path)
            state_dict = t_state.to_dict()
            ev_record["table_state"] = state_dict
            save_json_file()
            pot_str = f"{t_state.total_pot:.2f}" if t_state.total_pot is not None else "N/A"
            print(f"              ♠️ TABLE : {t_state.occupied_seats}/{t_state.total_seats} players | Pot: {pot_str} | Board: {t_state.board_stage.upper()} | In-Hand: {t_state.active_players_in_hand}")

            # Notify Web GUI if running
            try:
                import urllib.request
                payload = json.dumps({
                    "table_state": state_dict,
                    "image_name": os.path.basename(img_path)
                }).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:5000/api/event_update",
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=0.25)
            except Exception:
                pass
        except Exception:
            pass


    # ---------------------------------------------------------
    # Pre-populate Baseline: IGNORE past events on startup!
    # ---------------------------------------------------------
    try:
        init_raw = subprocess.check_output(
            ["adb", "-s", serial, "shell", "dumpsys vibrator_manager; echo ==SPLIT==; dumpsys media.audio_flinger; echo ==SPLIT==; dumpsys window | grep mCurrentFocus"],
            text=True, errors="replace"
        )
        parts = init_raw.split("==SPLIT==")
        init_vib = parts[0] if len(parts) > 0 else ""
        init_audio = parts[1] if len(parts) > 1 else ""

        # Populate past vibrations
        for m in VIB_REGEX.finditer(init_vib):
            t_stamp, eff, status, dur_ms, pkg, uid, played = m.groups()
            seen_vibrations.add((t_stamp, pkg, dur_ms, played.strip()))

        # Populate past audio bursts & tracks
        init_pkgs, init_bursts, init_tracks = parse_audio_data(init_audio)
        for b in init_bursts:
            seen_audio_bursts.add(b["start_time"])
        for tid, tinfo in init_tracks.items():
            if tinfo["state"] == "active":
                active_audio_tracks[tid] = {
                    "index": 0,
                    "start_time": time.time(),
                    "device_timestamp": tinfo["start_time"],
                    "package": tinfo["pkg"],
                    "uid": tinfo["uid"]
                }

        print(f" [*] Baseline established successfully:")
        print(f"     - Ignored {len(seen_vibrations)} pre-existing vibrations in history")
        print(f"     - Ignored {len(seen_audio_bursts)} pre-existing audio bursts in history")
        print(f"     - Tracked {len(active_audio_tracks)} currently open audio tracks")
        print(" [*] Ready! ZERO false triggers at startup. Waiting for real-time events...")
        print(" [*] Press Ctrl+C at any time to stop.")
        print("=" * 75 + "\n")
    except Exception as e:
        print(f"[WARN] Could not initialize baseline completely: {e}")

    def save_json_file():
        data = {
            "device_serial": serial,
            "target_package": target_package,
            "screen_resolution": f"{screen_w}x{screen_h}",
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "capture_engine": engine_desc if enable_capture else "OFF",
            "delayed_ms": delayed_ms,
            "total_events": len(recorded_events),
            "events": recorded_events
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    try:
        while True:
            # 1. Fetch dumpsys vibrator_manager, audio_flinger, and focused window in ONE fast round-trip
            try:
                raw_combined = subprocess.check_output(
                    ["adb", "-s", serial, "shell", "dumpsys vibrator_manager; echo ==SPLIT==; dumpsys media.audio_flinger; echo ==SPLIT==; dumpsys window | grep mCurrentFocus"],
                    text=True, errors="replace"
                )
                parts = raw_combined.split("==SPLIT==")
                raw_vib = parts[0] if len(parts) > 0 else ""
                raw_audio = parts[1] if len(parts) > 1 else ""
                raw_focus = parts[2] if len(parts) > 2 else ""
            except subprocess.SubprocessError:
                time.sleep(1.0)
                continue

            now_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            foreground_pkg = extract_foreground_package(raw_focus)
            active_pkgs, bursts, current_tracks = parse_audio_data(raw_audio)

            # -------------------------------------------------------------
            # A. Process Vibrations
            # -------------------------------------------------------------
            for m in VIB_REGEX.finditer(raw_vib):
                t_stamp, eff, status, dur_ms, pkg, uid, played = m.groups()
                played = played.strip()
                key = (t_stamp, pkg, dur_ms, played)
                
                if key not in seen_vibrations:
                    seen_vibrations.add(key)
                    if not target_package or target_package in pkg:
                        idx = event_idx
                        event_idx += 1
                        
                        ev_file, del_file = capture_mgr.trigger(idx, "vibration")
                        
                        print(f" [{now_str}] 📳 [EVENT {idx:03d}] VIBRATION TRIGGERED!")
                        print(f"              App      : {pkg} (UID {uid})")
                        print(f"              Duration : {dur_ms} ms")
                        print(f"              Effect   : {played}")
                        if ev_file:
                            print(f"              📸 Shot  : {ev_file} {'& ' + del_file if del_file else ''}")
                        print("-" * 75)
                        
                        event_record = {
                            "index": idx,
                            "type": "vibration",
                            "state": "start",
                            "timestamp": t_stamp,
                            "app": pkg,
                            "uid": int(uid) if uid.isdigit() else uid,
                            "duration_ms": int(dur_ms),
                            "effect": played,
                            "status": status,
                            "screenshot_event": ev_file,
                            "screenshot_delayed": del_file
                        }
                        recorded_events.append(event_record)
                        save_json_file()
                        if table_detector:
                            target_img = del_file if del_file else ev_file
                            if target_img:
                                threading.Thread(target=analyze_table_async, args=(target_img, event_record), daemon=True).start()

            # -------------------------------------------------------------
            # B. Process Audio Signal Power Bursts (Game Sound Effects)
            # -------------------------------------------------------------
            for b in bursts:
                b_ts = b["start_time"]
                if b_ts not in seen_audio_bursts:
                    seen_audio_bursts.add(b_ts)
                    
                    # Verify attribution to target package
                    is_target = False
                    attributed_pkg = None
                    
                    if target_package:
                        if any(target_package in p for p in active_pkgs):
                            is_target = True
                            attributed_pkg = target_package
                        elif foreground_pkg and target_package in foreground_pkg:
                            is_target = True
                            attributed_pkg = target_package
                    else:
                        is_target = True
                        attributed_pkg = list(active_pkgs)[0] if active_pkgs else (foreground_pkg or "unknown")

                    if is_target:
                        now_time = time.time()
                        # Apply debounce to prevent multi-trigger on continuous sounds
                        if (now_time - last_audio_burst_time) >= audio_debounce_sec:
                            last_audio_burst_time = now_time
                            idx = event_idx
                            event_idx += 1
                            
                            ev_file, del_file = capture_mgr.trigger(idx, "audio")
                            
                            dur_str = f"{b['duration_ms']} ms" if b['duration_ms'] else "burst"
                            sum_str = f"{b['sum_db']} dBFS" if b['sum_db'] is not None else "N/A"
                            
                            print(f" [{now_str}] 🔊 [EVENT {idx:03d}] AUDIO SOUND BURST DETECTED!")
                            print(f"              App      : {attributed_pkg}")
                            print(f"              Duration : {dur_str} ({len(b['samples'])} samples @ 50ms)")
                            print(f"              Peak/Sum : {sum_str}")
                            if ev_file:
                                print(f"              📸 Shot  : {ev_file} {'& ' + del_file if del_file else ''}")
                            print("-" * 75)
                            
                            event_record = {
                                "index": idx,
                                "type": "audio",
                                "subtype": "burst",
                                "timestamp": b_ts,
                                "app": attributed_pkg,
                                "duration_ms": b["duration_ms"],
                                "sample_count": len(b["samples"]),
                                "peak_db": b["sum_db"],
                                "screenshot_event": ev_file,
                                "screenshot_delayed": del_file
                            }
                            recorded_events.append(event_record)
                            save_json_file()
                            if table_detector:
                                target_img = del_file if del_file else ev_file
                                if target_img:
                                    threading.Thread(target=analyze_table_async, args=(target_img, event_record), daemon=True).start()

            # -------------------------------------------------------------
            # C. Process Standard AudioTrack State Changes (Non-Unity Apps)
            # -------------------------------------------------------------
            for trk_id, trk in current_tracks.items():
                if trk["state"] == "active":
                    if trk_id not in active_audio_tracks:
                        if not target_package or target_package in trk["pkg"]:
                            idx = event_idx
                            event_idx += 1
                            
                            ev_file, del_file = capture_mgr.trigger(idx, "audio_track")
                            
                            active_audio_tracks[trk_id] = {
                                "index": idx,
                                "start_time": time.time(),
                                "device_timestamp": trk["start_time"],
                                "package": trk["pkg"],
                                "uid": trk["uid"],
                                "screenshot_event": ev_file,
                                "screenshot_delayed": del_file
                            }
                            
                            print(f" [{now_str}] 🎵 [EVENT {idx:03d}] AUDIO TRACK OPENED!")
                            print(f"              App      : {trk['pkg']} (UID {trk['uid']})")
                            print(f"              Track ID : Track-{trk_id}")
                            if ev_file:
                                print(f"              📸 Shot  : {ev_file} {'& ' + del_file if del_file else ''}")
                            print("-" * 75)
                            
                            event_record = {
                                "index": idx,
                                "type": "audio",
                                "subtype": "track_start",
                                "timestamp": trk["start_time"],
                                "app": trk["pkg"],
                                "uid": trk["uid"],
                                "track_id": trk_id,
                                "screenshot_event": ev_file,
                                "screenshot_delayed": del_file
                            }
                            recorded_events.append(event_record)
                            save_json_file()
                            if table_detector:
                                target_img = del_file if del_file else ev_file
                                if target_img:
                                    threading.Thread(target=analyze_table_async, args=(target_img, event_record), daemon=True).start()


            # Detect audio track stop
            stopped_tracks = [tid for tid in active_audio_tracks if tid not in current_tracks or current_tracks[tid]["state"] != "active"]
            for tid in stopped_tracks:
                info = active_audio_tracks.pop(tid)
                if info.get("index", 0) > 0: # Only report if it was an active logged event
                    dur_ms = max(10, int(round((time.time() - info["start_time"]) * 1000)))
                    print(f" [{now_str}] 🔇 [TRACK-{tid}] AUDIO STOPPED | Played: {dur_ms}ms | App: {info['package']}")
                    print("-" * 75)
                    
                    stop_record = {
                        "index": info["index"],
                        "type": "audio",
                        "subtype": "track_stop",
                        "timestamp": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
                        "app": info["package"],
                        "uid": info["uid"],
                        "track_id": tid,
                        "duration_ms": dur_ms
                    }
                    recorded_events.append(stop_record)
                    save_json_file()

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n[INFO] Monitoring stopped by user.")
    finally:
        capture_mgr.close()
        save_json_file()
        print("=" * 75)
        print(f"[SUCCESS] Recorded {len(recorded_events)} events saved to '{output_file}'")
        print("=" * 75)

def main():
    parser = argparse.ArgumentParser(description="Record Android App Sound & Vibration Events with Auto Screenshot Capture")
    parser.add_argument("-s", "--serial", help="Device serial number (optional if only 1 device connected)")
    parser.add_argument("-p", "--package", default="com.nsus.clubgg", help="Target package name to monitor (default: com.nsus.clubgg, pass empty '' for all apps)")
    parser.add_argument("-o", "--output", default="app_events.json", help="Output JSON file path (default: app_events.json)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output JSON file instead of generating unique filename")
    parser.add_argument("-c", "--capture-dir", default="captures", help="Directory to save screenshots (default: captures)")
    parser.add_argument("--delayed-ms", type=int, default=500, help="Delay in ms for follow-up post-event capture (default: 500, 0 to disable)")
    parser.add_argument("--no-capture", action="store_true", help="Disable screenshot capture")
    parser.add_argument("--capture-source", choices=["scrcpy", "adb"], default="scrcpy", help="Screenshot engine: 'scrcpy' (fast ~5ms) or 'adb' (full-res 1008x2244) (default: scrcpy)")
    parser.add_argument("--screencap", action="store_true", help="Shortcut for --capture-source adb (full-resolution device screencap)")
    parser.add_argument("--interval", type=float, default=0.15, help="Polling interval in seconds (default: 0.15s / 150ms)")
    parser.add_argument("--audio-debounce", type=float, default=0.35, help="Debounce time in seconds for rapid audio bursts (default: 0.35s)")
    parser.add_argument("--detect-table", action="store_true", help="Automatically analyze table state (players, stacks, VPIP, pot, cards) on each event")
    
    args = parser.parse_args()
    
    check_adb()
    serial = select_device(args.serial)
    
    output_file = get_unique_output_path(args.output, overwrite=args.overwrite)
    capture_source = "adb" if args.screencap else args.capture_source
    
    monitor_and_record(
        serial=serial,
        target_package=args.package,
        output_file=output_file,
        capture_dir=args.capture_dir,
        delayed_ms=args.delayed_ms,
        no_capture=args.no_capture,
        capture_source=capture_source,
        poll_interval=args.interval,
        audio_debounce_sec=args.audio_debounce,
        detect_table=args.detect_table
    )

if __name__ == "__main__":
    main()


