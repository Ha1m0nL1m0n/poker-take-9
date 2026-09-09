#!/usr/bin/env python3
"""
ClubGG Heads-Up Tournament Game Automation Bot
=============================================
Autonomous decision and action execution engine for ClubGG 1-on-1 tournaments:
  - Real-time turn detection via GDI scrcpy / ADB screencap polling.
  - Complete Heads-Up state extraction (Hero cards, Board, Pot, Blinds, Action Bar).
  - GTO Push/Fold & Heuristic decision engine integration.
  - Advisory Dry-Run mode (`--dry-run`) for safe testing without sending taps.
  - Autonomous Live Play mode (`--auto`) with randomized human-like delays.
"""

import argparse
import io
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple, Any
from PIL import Image

BASE_DIR = r"c:\Users\chhu3\OneDrive\Documents\android"
sys.path.insert(0, BASE_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from table_detector import ClubGGTableDetector, PokerTableState
from poker_strategy import HeadsUpPokerStrategy, ActionPlan, ActionType


class ClubGGBot:
    def __init__(
        self,
        serial: Optional[str] = None,
        dry_run: bool = True,
        source: str = "scrcpy",
        poll_interval: float = 0.35,
        min_delay_ms: int = 850,
        max_delay_ms: int = 1500
    ):
        self.serial = serial or self._auto_detect_device()
        self.dry_run = dry_run
        self.source = source
        self.poll_interval = poll_interval
        self.min_delay_ms = min_delay_ms
        self.max_delay_ms = max_delay_ms

        self.detector = ClubGGTableDetector()
        self.strategy = HeadsUpPokerStrategy()
        
        # Scrcpy window handle (if available)
        self.scrcpy_hwnd = None
        if self.source == "scrcpy":
            self._init_scrcpy_window()

        self.total_decisions = 0
        self.action_history = []
        self._last_acted_signature = ""

    def _auto_detect_device(self) -> str:
        try:
            out = subprocess.check_output(["adb", "devices"], text=True, stderr=subprocess.DEVNULL)
            lines = [l.strip() for l in out.strip().splitlines()[1:] if l.strip()]
            devices = [l.split()[0] for l in lines if len(l.split()) >= 2 and l.split()[1] == "device"]
            if devices:
                return devices[0]
        except Exception:
            pass
        return "46261FDAS003BU"

    def _init_scrcpy_window(self):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32

            def enum_windows_callback(hwnd, extra):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value
                        if "Pixel" in title or "scrcpy" in title.lower() or "komodo" in title.lower():
                            extra.append(hwnd)
                return True

            hwnds = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)
            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), ctypes.py_object(hwnds))
            if hwnds:
                self.scrcpy_hwnd = hwnds[0]
                print(f"[*] Attached to scrcpy window (HWND: {self.scrcpy_hwnd}) for ~25ms hardware capture.")
            else:
                print("[*] No active scrcpy window found; falling back to direct ADB screencaps.")
                self.source = "adb"
        except Exception as e:
            print(f"[WARN] Error attaching to scrcpy window: {e}. Falling back to ADB.")
            self.source = "adb"

    def capture_frame(self) -> Image.Image:
        """Captures a screenshot of the table via scrcpy GDI+ or ADB screencap."""
        if self.source == "scrcpy" and self.scrcpy_hwnd:
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                gdi32 = ctypes.windll.gdi32

                rect = wintypes.RECT()
                user32.GetClientRect(self.scrcpy_hwnd, ctypes.byref(rect))
                w = rect.right - rect.left
                h = rect.bottom - rect.top

                if w > 100 and h > 100:
                    hwnd_dc = user32.GetDC(self.scrcpy_hwnd)
                    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
                    hbitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
                    gdi32.SelectObject(mem_dc, hbitmap)
                    gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, 0x00CC0020)

                    from PIL import ImageWin
                    im = Image.new("RGB", (w, h))
                    dib = ImageWin.Dib(im)
                    dib.fromhandle(hbitmap)

                    # Cleanup GDI handles
                    gdi32.DeleteObject(hbitmap)
                    gdi32.DeleteDC(mem_dc)
                    user32.ReleaseDC(self.scrcpy_hwnd, hwnd_dc)

                    # Auto-crop black letterbox borders if present
                    return self._crop_letterbox(im)
            except Exception:
                pass

        # Fallback: ADB screencap
        cmd = ["adb"]
        if self.serial:
            cmd.extend(["-s", self.serial])
        cmd.extend(["exec-out", "screencap", "-p"])
        res = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
        return Image.open(io.BytesIO(res.stdout))

    def _crop_letterbox(self, im: Image.Image) -> Image.Image:
        """Removes letterboxing black borders from scrcpy client window."""
        rgb = im.convert("RGB")
        w, h = rgb.size
        data = rgb.load()

        mid_y = h // 2
        left_x = 0
        while left_x < w and data[left_x, mid_y] == (0, 0, 0):
            left_x += 1
        right_x = w - 1
        while right_x > left_x and data[right_x, mid_y] == (0, 0, 0):
            right_x -= 1

        if left_x > 0 or right_x < w - 1:
            return im.crop((left_x, 0, right_x + 1, h))
        return im

    def tap(self, x: int, y: int):
        """Sends an input tap to the Android device via ADB."""
        cmd = ["adb"]
        if self.serial:
            cmd.extend(["-s", self.serial])
        cmd.extend(["shell", "input", "tap", str(x), str(y)])
        subprocess.run(cmd, check=True)

    def execute_plan(self, plan: ActionPlan):
        """Executes an action plan on the device with safety delays."""
        delay_sec = random.uniform(self.min_delay_ms, self.max_delay_ms) / 1000.0
        print(f"[*] Humanized thinking delay: {int(delay_sec * 1000)}ms...")
        time.sleep(delay_sec)

        tx, ty = plan.target_tap
        print(f"[ACTION] 🚀 Dispatched tap at ({tx}, {ty}) -> {plan.action.value}")
        self.tap(tx, ty)

    def run(self):
        mode_str = "DRY-RUN / ADVISORY (No taps sent)" if self.dry_run else "AUTONOMOUS PLAY (Sending ADB Taps)"
        print("=" * 70)
        print("  CLUBGG HEADS-UP TOURNAMENT GAME AUTOMATION BOT")
        print("=" * 70)
        print(f" Device Serial : {self.serial}")
        print(f" Operating Mode: {mode_str}")
        print(f" Capture Engine: {self.source.upper()}")
        print(f" Poll Interval : {int(self.poll_interval * 1000)}ms")
        print("=" * 70)
        print("[*] Bot running. Waiting for Hero's turn to act (Press Ctrl+C to stop)...\n")

        try:
            while True:
                frame = self.capture_frame()
                w, h = frame.size

                # Run fast table detection
                table_state: PokerTableState = self.detector.detect_table_state(frame)

                # Check if it's Hero's turn
                if table_state.is_hero_turn:
                    hero_cards = table_state.hero_cards
                    board_cards = table_state.community_cards
                    stage = table_state.board_stage
                    pot = table_state.total_pot
                    act_btns = table_state.action_buttons or {}

                    # Signature to prevent duplicate acting on the exact same state
                    state_sig = f"{hero_cards}_{board_cards}_{stage}_{pot}_{act_btns.get('call_amount')}_{act_btns.get('bet_raise_amount')}"
                    if state_sig == self._last_acted_signature:
                        time.sleep(self.poll_interval)
                        continue

                    # Extract stacks and blinds
                    hero_seat = next((s for s in table_state.seats if s.seat_id == 1), None)
                    opp_seat = next((s for s in table_state.seats if s.seat_id == 2), None)
                    hero_stack = hero_seat.stack if hero_seat else None
                    opp_stack = opp_seat.stack if opp_seat else None
                    position = hero_seat.position if hero_seat else "BTN/SB"

                    # Parse Big Blind
                    bb = 100.0
                    if table_state.tournament_info and table_state.tournament_info.get("bb"):
                        bb = float(table_state.tournament_info["bb"])
                    elif table_state.blinds and "/" in table_state.blinds:
                        try:
                            bb = float(table_state.blinds.split("/")[1])
                        except Exception:
                            pass

                    # Run Strategy Engine
                    plan = self.strategy.decide_action(
                        hero_cards=hero_cards,
                        community_cards=board_cards,
                        board_stage=stage,
                        total_pot=pot,
                        hero_stack=hero_stack,
                        opp_stack=opp_stack,
                        big_blind=bb,
                        position=position,
                        action_buttons=act_btns
                    )

                    self.total_decisions += 1
                    self._last_acted_signature = state_sig

                    # Display Decision Card
                    h_cards_str = " ".join(hero_cards) if hero_cards else "None"
                    b_cards_str = " ".join(board_cards) if board_cards else "(Preflop)"
                    print("\n" + "=" * 70)
                    print(f"🎯 [DECISION #{self.total_decisions:03d}] HERO'S TURN TO ACT!")
                    print("-" * 70)
                    print(f" Hero Cards   : {h_cards_str} ({plan.hand_notation})")
                    print(f" Board Cards  : {b_cards_str} [{stage.upper()}]")
                    print(f" Pot & Blinds : Pot: {pot or 0} | Blinds: {int(bb/2)}/{int(bb)} | Eff Stack: {plan.effective_bb:.1f} BB")
                    print(f" Position     : {position}")
                    actions_avail = []
                    if act_btns.get("can_fold"): actions_avail.append("Fold")
                    if act_btns.get("can_check"): actions_avail.append("Check")
                    if act_btns.get("can_call"): actions_avail.append(f"Call {act_btns.get('call_amount')}")
                    if act_btns.get("can_bet"): actions_avail.append(f"Bet {act_btns.get('bet_raise_amount')}")
                    if act_btns.get("can_raise"): actions_avail.append(f"Raise to {act_btns.get('bet_raise_amount')}")
                    print(f" Options      : [{', '.join(actions_avail)}]")
                    print("-" * 70)
                    print(f" 👉 PROPOSED ACTION : {plan.action.value}" + (f" (Amount: {plan.amount})" if plan.amount else ""))
                    print(f" 📍 TARGET TAP      : {plan.target_tap}")
                    print(f" 💡 RATIONALE       : {plan.rationale}")
                    print("=" * 70 + "\n")

                    if not self.dry_run:
                        self.execute_plan(plan)
                        # Short pause after tap to allow UI to update
                        time.sleep(1.0)

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\n[INFO] Bot stopped by user.")
        finally:
            print("=" * 70)
            print(f"Bot session complete. Total decisions made: {self.total_decisions}")
            print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="ClubGG Heads-Up Tournament Game Automation Bot")
    parser.add_argument("-s", "--serial", help="Device serial number (optional)")
    parser.add_argument("--auto", action="store_true", help="Enable autonomous play (sends ADB taps). Default is dry-run mode.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Advisory mode only: logs decisions without sending taps (default)")
    parser.add_argument("--source", choices=["scrcpy", "adb"], default="scrcpy", help="Screenshot engine: 'scrcpy' (fast ~25ms) or 'adb' (default: scrcpy)")
    parser.add_argument("--interval", type=float, default=0.35, help="Polling interval in seconds (default: 0.35s)")
    args = parser.parse_args()

    # If --auto is passed, turn off dry_run
    dry_run = not args.auto

    bot = ClubGGBot(
        serial=args.serial,
        dry_run=dry_run,
        source=args.source,
        poll_interval=args.interval
    )
    bot.run()


if __name__ == "__main__":
    main()
