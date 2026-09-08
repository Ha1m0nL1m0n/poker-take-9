#!/usr/bin/env python3
"""
Android Tap & Press Recorder & Replayer (Dual-Mode with Auto-Capture)
====================================================================
Simultaneously records touch events from:
  1. Physical phone touchscreen taps, long presses, and swipes (via ADB getevent)
  2. Mouse clicks, long presses, and drag/swipes inside the PC scrcpy window

AUTOMATIC SCREENSHOT CAPTURE:
  Whenever a tap or click occurs, captures the scrcpy window:
  - At the exact moment of the event (action_XXX_event.png)
  - Delayed after the event, e.g. +500ms (action_XXX_delayed_500ms.png)

Outputs accurate Android pixel coordinates, hold durations, delays, and linked screenshots.
Allows replaying the recorded sequence with exact timings on the connected device.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

def check_adb():
    try:
        subprocess.run(["adb", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        print("[ERROR] adb is not found in PATH. Please install platform-tools or add it to PATH.")
        sys.exit(1)

def get_devices():
    out = subprocess.check_output(["adb", "devices"], text=True)
    lines = [line.strip() for line in out.strip().splitlines()[1:] if line.strip()]
    devices = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices

def select_device(serial=None):
    devices = get_devices()
    if not devices:
        print("[ERROR] No devices connected. Please connect your Android device via USB with USB debugging enabled.")
        sys.exit(1)
    
    if serial:
        for s, status in devices:
            if s == serial:
                if status != "device":
                    print(f"[ERROR] Device {s} is {status}. Please authorize it on the phone screen.")
                    sys.exit(1)
                return s
        print(f"[ERROR] Device with serial '{serial}' not found. Available devices:")
        for s, status in devices:
            print(f"  - {s} ({status})")
        sys.exit(1)
    
    authorized = [s for s, status in devices if status == "device"]
    if not authorized:
        print("[ERROR] Connected device(s) are unauthorized. Please unlock phone and tap 'Allow USB debugging'.")
        for s, status in devices:
            print(f"  - {s} ({status})")
        sys.exit(1)
    
    return authorized[0]

def get_screen_size(serial):
    out = subprocess.check_output(["adb", "-s", serial, "shell", "wm", "size"], text=True)
    m = re.search(r'Override size: (\d+)x(\d+)', out)
    if not m:
        m = re.search(r'Physical size: (\d+)x(\d+)', out)
    if not m:
        raise RuntimeError(f"Failed to parse screen size from: {out}")
    return int(m.group(1)), int(m.group(2))

def get_touch_device_info(serial):
    out = subprocess.check_output(["adb", "-s", serial, "shell", "getevent", "-lp"], text=True)
    devices = out.split("add device ")
    for d in devices:
        if "ABS_MT_POSITION_X" in d and "ABS_MT_POSITION_Y" in d:
            dev_m = re.search(r'(/dev/input/event\d+)', d)
            dev_path = dev_m.group(1) if dev_m else None
            name_m = re.search(r'name:\s+"([^"]+)"', d)
            dev_name = name_m.group(1) if name_m else "unknown"
            
            x_m = re.search(r'ABS_MT_POSITION_X\s+:[^\n]*max (\d+)', d)
            y_m = re.search(r'ABS_MT_POSITION_Y\s+:[^\n]*max (\d+)', d)
            max_x = int(x_m.group(1)) if x_m else None
            max_y = int(y_m.group(1)) if y_m else None
            return dev_path, dev_name, max_x, max_y
    return None, None, None, None

def set_pointer_location(serial, enable=True):
    val = "1" if enable else "0"
    subprocess.run(["adb", "-s", serial, "shell", "settings", "put", "system", "pointer_location", val], check=True)
    subprocess.run(["adb", "-s", serial, "shell", "settings", "put", "system", "show_touches", val], check=True)
    status = "Enabled" if enable else "Disabled"
    print(f"[*] Visual Touch & Pointer Location {status} on device.")

# Windows API structures & helpers
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
                elif ("scrcpy" in t_lower or "pixel" in t_lower or "komodo" in t_lower) and "cascadia" not in cls and "console" not in cls:
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
    """Calculates exact centered viewport of the Android display within scrcpy client area."""
    if client_w <= 0 or client_h <= 0:
        return 0, 0, client_w, client_h
    screen_aspect = screen_w / screen_h
    client_aspect = client_w / client_h
    
    if client_aspect > screen_aspect:
        # Pillarbox: black bars on left and right
        rendered_w = int(round(client_h * screen_aspect))
        rendered_h = client_h
        vp_x = int(round((client_w - rendered_w) / 2.0))
        vp_y = 0
    else:
        # Letterbox: black bars on top and bottom
        rendered_w = client_w
        rendered_h = int(round(client_w / screen_aspect))
        vp_x = 0
        vp_y = int(round((client_h - rendered_h) / 2.0))
        
    return vp_x, vp_y, rendered_w, rendered_h

def map_client_to_android(rel_x, rel_y, client_w, client_h, screen_w, screen_h):
    vp_x, vp_y, rendered_w, rendered_h = get_viewport_metrics(client_w, client_h, screen_w, screen_h)
    
    x_in_render = rel_x - vp_x
    y_in_render = rel_y - vp_y
    
    if -4.0 <= x_in_render < 0:
        x_in_render = 0
    if -4.0 <= y_in_render < 0:
        y_in_render = 0
    if rendered_w <= x_in_render <= rendered_w + 4.0:
        x_in_render = rendered_w - 1
    if rendered_h <= y_in_render <= rendered_h + 4.0:
        y_in_render = rendered_h - 1
        
    if x_in_render < 0 or x_in_render >= rendered_w or y_in_render < 0 or y_in_render >= rendered_h:
        return None
        
    ax = int(round(x_in_render * screen_w / rendered_w))
    ay = int(round(y_in_render * screen_h / rendered_h))
    ax = max(0, min(screen_w - 1, ax))
    ay = max(0, min(screen_h - 1, ay))
    return ax, ay

# Capture Manager (GDI+ Window Capture Engine)
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
    def __init__(self, hwnd, serial, screen_w, screen_h, capture_dir="captures", delayed_ms=500, enabled=True):
    def __init__(self, hwnd, serial, screen_w, screen_h, capture_dir="captures", delayed_ms=500, enabled=True, capture_source="scrcpy"):
        self.hwnd = hwnd
        self.serial = serial
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.capture_dir = capture_dir
        self.delayed_ms = delayed_ms
        self.delayed_sec = delayed_ms / 1000.0
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
            if self.hwnd and self.gdiplus_ready:
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
        
        # 1. Get window rectangle and client rectangle
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
            
        # 2. Compute client offset inside window (cuts out OS title bar & window borders)
        pt = POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        off_x = pt.x - wrect.left
        off_y = pt.y - wrect.top
        
        # 3. Compute viewport (cuts out any black letterbox bars inside client area)
        vp_x, vp_y, rw, rh = get_viewport_metrics(cw, ch, self.screen_w, self.screen_h)
        
        screen_dc = user32.GetDC(0)
        full_dc = gdi32.CreateCompatibleDC(screen_dc)
        full_bmp = gdi32.CreateCompatibleBitmap(screen_dc, win_w, win_h)
        old_full = gdi32.SelectObject(full_dc, full_bmp)
        
        # Render complete hardware accelerated window
        user32.PrintWindow(hwnd, full_dc, 2)
        
        # 4. Create target bitmap sized to exact centered Android viewport
        crop_dc = gdi32.CreateCompatibleDC(screen_dc)
        crop_bmp = gdi32.CreateCompatibleBitmap(screen_dc, rw, rh)
        old_crop = gdi32.SelectObject(crop_dc, crop_bmp)
        
        # BitBlt precisely from (off_x + vp_x, off_y + vp_y)
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

    def trigger(self, action_idx):
        if not self.enabled:
            return None, None
            
        event_file = os.path.join(self.capture_dir, f"action_{action_idx:03d}_event.png")
        delayed_file = os.path.join(self.capture_dir, f"action_{action_idx:03d}_delayed_{self.delayed_ms}ms.png")
        
        # 1. Immediate capture at event moment
        t_event = threading.Thread(target=self.capture_now, args=(event_file,), daemon=True)
        t_event.start()
        
        # 2. Delayed capture after delayed_ms
        t_delayed = threading.Timer(self.delayed_sec, self.capture_now, args=(delayed_file,))
        t_delayed.daemon = True
        self.active_timers.append(t_delayed)
        t_delayed.start()
        
        return event_file, delayed_file

    def close(self):
        for t in list(self.active_timers):
            t.join(timeout=self.delayed_sec + 0.3)
        if self.gdiplus_ready:
            try:
                ctypes.windll.gdiplus.GdiplusShutdown(self.gdi_token)
            except Exception:
                pass

def get_next_action_index(capture_dir):
    """Finds highest existing action index in capture_dir so files are never overwritten."""
    if not os.path.exists(capture_dir):
        return 1
    max_idx = 0
    pattern = re.compile(r'action_(\d+)_')
    try:
        for f in os.listdir(capture_dir):
            m = pattern.search(f)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    except Exception:
        pass
    return max_idx + 1

def get_unique_output_path(base_path, overwrite=False):
    """Generates unique JSON output path if default file exists to prevent accidental overwriting."""
    if overwrite or not os.path.exists(base_path):
        return base_path
    root, ext = os.path.splitext(base_path)
    i = 1
    while os.path.exists(f"{root}_{i}{ext}"):
        i += 1
    return f"{root}_{i}{ext}"

class ActionCounter:
    def __init__(self, start=1):
        self._count = start - 1
        self._lock = threading.Lock()
        
    def next(self):
        with self._lock:
            self._count += 1
            return self._count

# Thread 1: Phone Touch Worker (ADB Getevent)
def phone_touch_worker(serial, dev_path, scale_x, scale_y, screen_w, screen_h, event_q, stop_event, capture_mgr, counter):
    cmd = ["adb", "-s", serial, "shell", "getevent", "-l", dev_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    
    slots = {}
    current_slot = 0
    
    def get_slot(idx):
        if idx not in slots:
            slots[idx] = {
                "action_id": None,
                "event_file": None,
                "delayed_file": None,
                "tracking_id": None,
                "down_time": None,
                "start_x": None,
                "start_y": None,
                "curr_x": None,
                "curr_y": None
            }
        return slots[idx]
        
    line_regex = re.compile(r'(?:/dev/input/\w+:\s+)?(\w+)\s+(\w+)\s+([0-9a-fA-F]+|\w+)')
    
    try:
        for line in proc.stdout:
            if stop_event.is_set():
                break
            m = line_regex.search(line)
            if not m:
                continue
            ev_type, ev_code, ev_val = m.group(1), m.group(2), m.group(3)
            
            if ev_code == "ABS_MT_SLOT":
                try:
                    current_slot = int(ev_val, 16)
                except ValueError:
                    current_slot = 0
                continue
                
            slot = get_slot(current_slot)
            
            if ev_code == "ABS_MT_TRACKING_ID":
                is_release = (ev_val.lower() == "ffffffff" or ev_val == "-1")
                if is_release:
                    if slot["down_time"] is not None and slot["start_x"] is not None and slot["start_y"] is not None:
                        up_time = time.time()
                        dur_ms = max(10, int(round((up_time - slot["down_time"]) * 1000)))
                        cx = slot["curr_x"] if slot["curr_x"] is not None else slot["start_x"]
                        cy = slot["curr_y"] if slot["curr_y"] is not None else slot["start_y"]
                        
                        event_q.put({
                            "action_id": slot["action_id"],
                            "event_file": slot["event_file"],
                            "delayed_file": slot["delayed_file"],
                            "source": "Phone",
                            "down_time": slot["down_time"],
                            "up_time": up_time,
                            "start_x": slot["start_x"],
                            "start_y": slot["start_y"],
                            "end_x": cx,
                            "end_y": cy,
                            "duration_ms": dur_ms
                        })
                        slot["tracking_id"] = None
                        slot["down_time"] = None
                        slot["start_x"] = None
                        slot["start_y"] = None
                        slot["curr_x"] = None
                        slot["curr_y"] = None
                else:
                    try:
                        slot["tracking_id"] = int(ev_val, 16)
                    except ValueError:
                        slot["tracking_id"] = 1
                    slot["down_time"] = time.time()
                    slot["action_id"] = counter.next()
                    slot["event_file"], slot["delayed_file"] = capture_mgr.trigger(slot["action_id"])
                    slot["start_x"] = None
                    slot["start_y"] = None
                    slot["curr_x"] = None
                    slot["curr_y"] = None
                    
            elif ev_code == "ABS_MT_POSITION_X":
                try:
                    raw_x = int(ev_val, 16)
                    sc_x = max(0, min(screen_w - 1, int(round(raw_x * scale_x))))
                    slot["curr_x"] = sc_x
                    if slot["start_x"] is None:
                        slot["start_x"] = sc_x
                except ValueError:
                    pass
            elif ev_code == "ABS_MT_POSITION_Y":
                try:
                    raw_y = int(ev_val, 16)
                    sc_y = max(0, min(screen_h - 1, int(round(raw_y * scale_y))))
                    slot["curr_y"] = sc_y
                    if slot["start_y"] is None:
                        slot["start_y"] = sc_y
                except ValueError:
                    pass
    except Exception:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except Exception:
            proc.kill()

# Thread 2: scrcpy Mouse Worker
def scrcpy_mouse_worker(scrcpy_hwnd, screen_w, screen_h, event_q, stop_event, capture_mgr, counter):
    user32 = ctypes.windll.user32
    attach_to_interactive_desktop()
    
    last_state = False
    down_time = None
    down_coords = None
    down_action_id = None
    event_file = None
    delayed_file = None
    
    while not stop_event.is_set():
        is_down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        
        hwnd_under = user32.WindowFromPoint(pt)
        root_hwnd = user32.GetAncestor(hwnd_under, 2)
        
        rect = RECT()
        user32.GetClientRect(scrcpy_hwnd, ctypes.byref(rect))
        client_origin = POINT(rect.left, rect.top)
        user32.ClientToScreen(scrcpy_hwnd, ctypes.byref(client_origin))
        
        client_w = rect.right - rect.left
        client_h = rect.bottom - rect.top
        rel_x = pt.x - client_origin.x
        rel_y = pt.y - client_origin.y
        
        is_in_scrcpy = (root_hwnd == scrcpy_hwnd)
        coords = map_client_to_android(rel_x, rel_y, client_w, client_h, screen_w, screen_h)
        
        # Transition: Mouse Down
        if is_down and not last_state:
            if is_in_scrcpy and coords:
                down_time = time.time()
                down_coords = coords
                down_action_id = counter.next()
                event_file, delayed_file = capture_mgr.trigger(down_action_id)
                last_state = True
        # Transition: Mouse Up
        elif not is_down and last_state:
            last_state = False
            if down_time and down_coords:
                up_time = time.time()
                dur_ms = max(10, int(round((up_time - down_time) * 1000)))
                end_coords = coords if coords else down_coords
                
                event_q.put({
                    "action_id": down_action_id,
                    "event_file": event_file,
                    "delayed_file": delayed_file,
                    "source": "scrcpy",
                    "down_time": down_time,
                    "up_time": up_time,
                    "start_x": down_coords[0],
                    "start_y": down_coords[1],
                    "end_x": end_coords[0],
                    "end_y": end_coords[1],
                    "duration_ms": dur_ms
                })
                down_time = None
                down_coords = None
                down_action_id = None
                
        time.sleep(0.01)

def record_touches(serial, output_file, mode="both", max_count=None, capture_dir="captures", delayed_ms=500, no_capture=False):
def record_touches(serial, output_file, mode="both", max_count=None, capture_dir="captures", delayed_ms=500, no_capture=False, capture_source="scrcpy"):
    screen_w, screen_h = get_screen_size(serial)
    dev_path, dev_name, max_x, max_y = get_touch_device_info(serial)
    
    scale_x = screen_w / (max_x + 1) if max_x else 1.0
    scale_y = screen_h / (max_y + 1) if max_y else 1.0
    
    scrcpy_wins = find_scrcpy_windows() if sys.platform == "win32" else []
    scrcpy_hwnd = scrcpy_wins[0][0] if scrcpy_wins else None
    scrcpy_title = scrcpy_wins[0][1] if scrcpy_wins else "Not found"
    
    enable_capture = not no_capture
    start_idx = get_next_action_index(capture_dir) if enable_capture else 1
    capture_mgr = CaptureManager(
        hwnd=scrcpy_hwnd,
        serial=serial,
        screen_w=screen_w,
        screen_h=screen_h,
        capture_dir=capture_dir,
        delayed_ms=delayed_ms,
        enabled=enable_capture
        enabled=enable_capture,
        capture_source=capture_source
    )
    counter = ActionCounter(start=start_idx)
    
    print("=" * 70)
    print("      ANDROID TAP & TOUCH RECORDER (DUAL-MODE + AUTO-CAPTURE)")
    print("=" * 70)
    print(f" Device Serial    : {serial}")
    print(f" Screen Size      : {screen_w} x {screen_h}")
    print(f" Phone Touch Panel: {dev_path} ({dev_name})")
    print(f" scrcpy Window    : {scrcpy_title} (HWND: {scrcpy_hwnd})")
    print(f" Capture Engine   : {'Direct ADB Screencap (1008x2244)' if capture_source == 'adb' else 'scrcpy Centered Window (Hardware Accelerated)'}")
    print(f" Active Listeners :", end=" ")
    
    active_sources = []
    if mode in ("both", "phone") and dev_path:
        active_sources.append("Physical Phone Taps")
    if mode in ("both", "scrcpy") and scrcpy_hwnd:
        active_sources.append("PC scrcpy Mouse Clicks")
        
    print(" + ".join(active_sources) if active_sources else "None")
    print(f" Screenshot Engine: {'ON (instant at event + ' + str(delayed_ms) + 'ms delayed)' if enable_capture else 'OFF'}")
    if enable_capture:
        print(f" Captures Folder  : ./{capture_dir}/ (Starting at action_{start_idx:03d})")
    print(f" Output File      : {output_file}")
    print("-" * 70)
    print(" [*] TAP on phone screen OR CLICK with mouse in scrcpy window.")
    print(f" [*] Screenshots are automatically saved at the event and +{delayed_ms}ms delayed.")
    print(" [*] Press Ctrl+C at any time to stop and save recording.")
    print("=" * 70)
    
    event_q = queue.Queue()
    stop_event = threading.Event()
    threads = []
    
    if mode in ("both", "phone") and dev_path:
        t_phone = threading.Thread(
            target=phone_touch_worker,
            args=(serial, dev_path, scale_x, scale_y, screen_w, screen_h, event_q, stop_event, capture_mgr, counter),
            daemon=True
        )
        t_phone.start()
        threads.append(t_phone)
        
    if mode in ("both", "scrcpy") and scrcpy_hwnd:
        t_mouse = threading.Thread(
            target=scrcpy_mouse_worker,
            args=(scrcpy_hwnd, screen_w, screen_h, event_q, stop_event, capture_mgr, counter),
            daemon=True
        )
        t_mouse.start()
        threads.append(t_mouse)
        
    recorded_actions = []
    last_action_end_time = time.time()
    
    try:
        while True:
            try:
                ev = event_q.get(timeout=0.1)
            except queue.Empty:
                continue
                
            idx = ev["action_id"]
            src = ev["source"]
            sx, sy = ev["start_x"], ev["start_y"]
            ex, ey = ev["end_x"], ev["end_y"]
            dur = ev["duration_ms"]
            down_t = ev["down_time"]
            up_t = ev["up_time"]
            ev_file = ev["event_file"]
            del_file = ev["delayed_file"]
            
            dist = math.hypot(ex - sx, ey - sy)
            delay_ms = max(0, int(round((down_t - last_action_end_time) * 1000)))
            
            if dist <= 35:
                if dur < 500:
                    atype = "tap"
                    cmd = f"adb shell input tap {sx} {sy}"
                    print(f" [{idx:02d}] TAP        ({src:6s}) at ({sx:4d}, {sy:4d})   | Hold: {dur:3d}ms | Delay: {delay_ms:4d}ms")
                else:
                    atype = "long_press"
                    cmd = f"adb shell input swipe {sx} {sy} {sx} {sy} {dur}"
                    print(f" [{idx:02d}] LONG_PRESS ({src:6s}) at ({sx:4d}, {sy:4d})   | Hold: {dur:3d}ms | Delay: {delay_ms:4d}ms")
                
                record_entry = {
                    "index": idx,
                    "source": src,
                    "type": atype,
                    "x": sx,
                    "y": sy,
                    "duration_ms": dur,
                    "delay_from_prev_ms": delay_ms,
                    "screenshot_event": ev_file,
                    "screenshot_delayed": del_file,
                    "adb_command": cmd
                }
            else:
                atype = "swipe"
                cmd = f"adb shell input swipe {sx} {sy} {ex} {ey} {max(100, dur)}"
                print(f" [{idx:02d}] SWIPE      ({src:6s}) ({sx:4d}, {sy:4d}) -> ({ex:4d}, {ey:4d}) | Dist: {int(dist):3d}px | Hold: {dur:3d}ms")
                record_entry = {
                    "index": idx,
                    "source": src,
                    "type": atype,
                    "start_x": sx,
                    "start_y": sy,
                    "end_x": ex,
                    "end_y": ey,
                    "distance_px": int(dist),
                    "duration_ms": dur,
                    "delay_from_prev_ms": delay_ms,
                    "screenshot_event": ev_file,
                    "screenshot_delayed": del_file,
                    "adb_command": cmd
                }
                
            if enable_capture and ev_file and del_file:
                print(f"      📸 Captured: {ev_file}  &  {del_file}")
                
            recorded_actions.append(record_entry)
            last_action_end_time = up_t
            
            if max_count and len(recorded_actions) >= max_count:
                break
                
    except KeyboardInterrupt:
        print("\n[INFO] Recording stopped by user.")
    finally:
        stop_event.set()
        capture_mgr.close()
        
    save_recording(recorded_actions, output_file, screen_w, screen_h, serial)

def save_recording(actions, output_file, screen_w, screen_h, serial):
    data = {
        "device_serial": serial,
        "screen_resolution": f"{screen_w}x{screen_h}",
        "total_actions": len(actions),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "actions": actions
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        
    print("-" * 70)
    print(f"[SUCCESS] Saved {len(actions)} actions to '{output_file}'")
    
    bat_file = os.path.splitext(output_file)[0] + "_replay.bat"
    with open(bat_file, "w", encoding="utf-8") as f:
        f.write("@echo off\n")
        f.write(f'python "%~dp0record_touches.py" --replay "%~dp0{os.path.basename(output_file)}" %*\n')
    print(f"[*] Created 1-click replay launcher: '{bat_file}'")
    print(f"[*] To replay anytime: python record_touches.py --replay {output_file}")
    print("=" * 70)

def replay_recording(serial, input_file, speed=1.0):
    if not os.path.exists(input_file):
        print(f"[ERROR] File '{input_file}' not found.")
        sys.exit(1)
        
    with open(input_file, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
        
    actions = data.get("actions", [])
    if not actions:
        print("[WARN] No actions found in recording file.")
        return
        
    print("=" * 70)
    print("                REPLAYING RECORDED TOUCHES")
    print("=" * 70)
    print(f" Device Serial : {serial}")
    print(f" Input File    : {input_file}")
    print(f" Total Actions : {len(actions)}")
    print(f" Speed Multi   : {speed}x")
    print("-" * 70)
    
    for act in actions:
        idx = act.get("index", 0)
        atype = act.get("type", "tap")
        delay_ms = act.get("delay_from_prev_ms", 500)
        
        sleep_sec = (delay_ms / 1000.0) / speed
        if sleep_sec > 0:
            time.sleep(sleep_sec)
            
        if atype == "tap":
            x, y = act["x"], act["y"]
            print(f" [{idx:02d}] Replaying TAP        at ({x}, {y})")
            subprocess.run(["adb", "-s", serial, "shell", "input", "tap", str(x), str(y)], check=True)
        elif atype == "long_press":
            x, y = act["x"], act["y"]
            dur = act.get("duration_ms", 600)
            print(f" [{idx:02d}] Replaying LONG_PRESS at ({x}, {y}) [dur: {dur}ms]")
            subprocess.run(["adb", "-s", serial, "shell", "input", "swipe", str(x), str(y), str(x), str(y), str(dur)], check=True)
        elif atype == "swipe":
            sx, sy = act["start_x"], act["start_y"]
            ex, ey = act["end_x"], act["end_y"]
            dur = act.get("duration_ms", 300)
            print(f" [{idx:02d}] Replaying SWIPE      ({sx}, {sy}) -> ({ex}, {ey}) [dur: {dur}ms]")
            subprocess.run(["adb", "-s", serial, "shell", "input", "swipe", str(sx), str(sy), str(ex), str(ey), str(dur)], check=True)
            
    print("-" * 70)
    print("[SUCCESS] Replay finished successfully!")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Record & replay Android tap/press locations with auto screenshot capture")
    parser.add_argument("-s", "--serial", help="Device serial number (optional if only 1 device connected)")
    parser.add_argument("-o", "--output", default="recorded_touches.json", help="Output JSON file (default: recorded_touches.json)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output JSON file instead of generating a unique name")
    parser.add_argument("--mode", choices=["both", "phone", "scrcpy"], default="both", help="Input source (default: both)")
    parser.add_argument("-c", "--capture-dir", default="captures", help="Directory to save screenshots (default: captures)")
    parser.add_argument("--delayed-ms", "--after-delay", dest="delayed_ms", type=int, default=500, help="Delay in ms for post-event capture (default: 500)")
    parser.add_argument("--capture-source", choices=["scrcpy", "adb"], default="scrcpy", help="Screenshot engine: 'scrcpy' (hardware window capture) or 'adb' (direct device screencap) (default: scrcpy)")
    parser.add_argument("--screencap", action="store_true", help="Shortcut for --capture-source adb (full-resolution 1008x2244 device screencap)")
    parser.add_argument("--no-capture", action="store_true", help="Disable screenshot capture")
    parser.add_argument("--replay", nargs="?", const="recorded_touches.json", help="Replay recorded touches from JSON file")
    parser.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier (default: 1.0)")
    parser.add_argument("--count", type=int, help="Stop automatically after recording N touches")
    parser.add_argument("--show-touches", choices=["on", "off"], help="Turn Android touch visualization ON or OFF")
    parser.add_argument("--pointer-location", choices=["on", "off"], help="Turn Android coordinate bar ON or OFF")
    
    args = parser.parse_args()
    
    check_adb()
    serial = select_device(args.serial)
    
    if args.show_touches:
        set_pointer_location(serial, enable=(args.show_touches == "on"))
        return
        
    if args.pointer_location:
        set_pointer_location(serial, enable=(args.pointer_location == "on"))
        return
        
    if args.replay:
        replay_recording(serial, args.replay, speed=args.speed)
        return
        
    output_file = get_unique_output_path(args.output, overwrite=args.overwrite)
    capture_source = "adb" if args.screencap else args.capture_source
    record_touches(
        serial=serial,
        output_file=output_file,
        mode=args.mode,
        max_count=args.count,
        capture_dir=args.capture_dir,
        delayed_ms=args.delayed_ms,
        no_capture=args.no_capture
        no_capture=args.no_capture,
        capture_source=capture_source
    )

if __name__ == "__main__":
    main()
