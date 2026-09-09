#!/usr/bin/env python3
"""
ClubGG Game History Automated Crawler
======================================
Crawls ClubGG game history bottom-up (oldest to newest):
1. Starts at the bottom of the Hand History list.
2. Identifies each of the visible session boxes (up to 7 per screen).
3. For each unvisited session (bottom-to-top: Slot 7 -> Slot 1):
   - Reads date, hand count, blinds, game type, and profit/loss.
   - Taps into the session.
   - Verifies the total hand count inside the session replayer.
   - Cycles through every hand (N down to 1) via the left arrow button.
   - Saves crisp full-res screenshots into structured session directories.
   - Exits back to the list screen.
4. Smoothly scrolls up to reveal subsequent newer sessions.
5. Deduplicates sessions and terminates upon reaching the top of the history list.
"""

import io
import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple, Any
from PIL import Image

BASE_DIR = r"c:\Users\chhu3\OneDrive\Documents\android"
sys.path.insert(0, BASE_DIR)
from table_detector import ClubGGTableDetector

# Output paths
HISTORY_DIR = os.path.join(BASE_DIR, "game_history")
CATALOG_PATH = os.path.join(HISTORY_DIR, "crawled_sessions.json")

# Physical pixel geometry (Pixel 9 Pro XL, 1008x2244)
SLOT_HEIGHT = 261
START_Y = 400
TAP_X = 500
SLOT_CENTERS = [START_Y + i * SLOT_HEIGHT + SLOT_HEIGHT // 2 for i in range(7)]  # [530, 791, 1052, 1313, 1574, 1835, 2096]

# Session Replayer Controls
REPLAYER_LEFT_ARROW = (60, 2166)   # Previous hand
REPLAYER_RIGHT_ARROW = (946, 2165) # Next hand
REPLAYER_BACK_BTN = (45, 210)      # Return to list screen


class ClubGGHistoryCrawler:
    def __init__(self):
        self.detector = ClubGGTableDetector()
        os.makedirs(HISTORY_DIR, exist_ok=True)
        self.crawled_sessions = self._load_catalog()

    def _load_catalog(self) -> Dict[str, Any]:
        if os.path.exists(CATALOG_PATH):
            try:
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_catalog(self):
        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.crawled_sessions, f, indent=2, ensure_ascii=False)

    def capture_screen(self) -> Image.Image:
        res = subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=subprocess.PIPE, check=True)
        return Image.open(io.BytesIO(res.stdout))

    def tap(self, x: int, y: int, delay: float = 0.3):
        subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)], check=True)
        time.sleep(delay)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350, delay: float = 1.0):
        subprocess.run(["adb", "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)], check=True)
        time.sleep(delay)

    def parse_slots_on_screen(self, screen_img: Image.Image) -> List[Dict[str, Any]]:
        w, h = screen_img.size
        words = self.detector.run_ocr(screen_img)
        slots = []

        for i in range(7):
            y_min = (START_Y + i * SLOT_HEIGHT) / float(h)
            y_max = (START_Y + (i + 1) * SLOT_HEIGHT) / float(h)
            box_words = [t for t in words if y_min <= (t[1] + t[3] / 2.0) < y_max]
            full_text = " ".join(t[4] for t in sorted(box_words, key=lambda x: (x[1], x[0])))

            # Hand Count
            m_hands = re.search(r"(\d+)\s*Hand", full_text, re.IGNORECASE)
            hand_count = int(m_hands.group(1)) if m_hands else None

            # Date & Time
            m_date = re.search(r"(202\d[-/]\d{2}[-/]\d{2})", full_text)
            date_str = m_date.group(1) if m_date else None
            m_time = re.search(r"(\d{2}:\d{2}(?::\d{2})?)", full_text)
            time_str = m_time.group(1) if m_time else ""

            # Game Type
            game_type = "NLH"
            if "PLO" in full_text:
                game_type = "PLO"
            elif "SNG" in full_text:
                game_type = "SNG"
            elif "MTT" in full_text:
                game_type = "MTT"

            # Blinds / Buy-in
            m_blinds = re.search(r"(\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?)", full_text)
            blinds_str = m_blinds.group(1).replace(" ", "") if m_blinds else None
            if not blinds_str and ("+" in full_text or "Buy-in" in full_text):
                m_buyin = re.search(r"(\d+(?:\.\d+)?\s*\+\s*\d+(?:\.\d+)?)", full_text)
                blinds_str = m_buyin.group(1).replace(" ", "") if m_buyin else "BuyIn"

            # Profit & Loss
            m_pnl = re.search(r"([+-]\s*\d+(?:\.\d+)?)", full_text)
            pnl_str = m_pnl.group(1).replace(" ", "") if m_pnl else "0"

            tap_y = SLOT_CENTERS[i]

            # Unique key for deduplication
            key = f"{date_str}_{time_str}_{game_type}_{blinds_str}_{hand_count}hands_{pnl_str}"

            slots.append({
                "slot_idx": i + 1,
                "key": key,
                "date": date_str,
                "time": time_str,
                "game_type": game_type,
                "blinds": blinds_str,
                "hands": hand_count,
                "pnl": pnl_str,
                "tap_point": (TAP_X, tap_y),
                "raw_text": full_text
            })

        return slots

    def extract_hand_info(self, hand_img: Image.Image) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
        w, h = hand_img.size
        
        # 1. Top region for Hand ID: y in [0.05, 0.14]
        top_crop = hand_img.crop((0, int(h * 0.05), w, int(h * 0.14)))
        top_words = self.detector.run_ocr(top_crop)
        hand_id = None
        for t in top_words:
            m_id = re.search(r"#?(\d{7,12})", t[4])
            if m_id:
                hand_id = m_id.group(1)
                break

        # 2. Bottom region for Hand Counter: y in [0.90, 0.98]
        bot_crop = hand_img.crop((int(w * 0.6), int(h * 0.90), w, int(h * 0.98)))
        bot_words = self.detector.run_ocr(bot_crop)
        hand_idx = None
        for t in bot_words:
            m_idx = re.search(r"(\d+)\s*/\s*(\d+)", t[4])
            if m_idx:
                hand_idx = (int(m_idx.group(1)), int(m_idx.group(2)))
                break

        return hand_id, hand_idx

    def crawl_session(self, slot_info: Dict[str, Any]) -> bool:
        slot_num = slot_info["slot_idx"]
        key = slot_info["key"]
        expected_hands = slot_info["hands"] or 1
        date_str = slot_info["date"] or "unknown_date"
        time_clean = (slot_info["time"] or "00-00").replace(":", "-")
        type_str = slot_info["game_type"]
        blinds_clean = (slot_info["blinds"] or "blinds").replace("/", "-")
        pnl_clean = slot_info["pnl"].replace("+", "plus").replace("-", "minus")

        folder_name = f"{date_str}_{time_clean}_{type_str}_{blinds_clean}_{expected_hands}hands_{pnl_clean}"
        session_dir = os.path.join(HISTORY_DIR, folder_name)
        os.makedirs(session_dir, exist_ok=True)

        print(f"\n[SESSION] Slot {slot_num}: {slot_info['date']} {slot_info['time']} | {expected_hands} Hands | {slot_info['blinds']} | PnL: {slot_info['pnl']}")
        print(f"          Target Folder: {folder_name}")

        # Tap into session
        tap_x, tap_y = slot_info["tap_point"]
        self.tap(tap_x, tap_y, delay=1.2)

        # Inspect initial hand in session viewer
        init_screen = self.capture_screen()
        init_id, init_idx = self.extract_hand_info(init_screen)
        
        actual_total = expected_hands
        current_hand = expected_hands
        if init_idx:
            current_hand, actual_total = init_idx
            print(f"  [VIEWER] Ground-truth hand count: {actual_total} hands (currently at {current_hand}/{actual_total})")

        captured_records = []

        # Replay hands backwards from current_hand down to 1
        for h_num in range(current_hand, 0, -1):
            if h_num == current_hand:
                screen = init_screen
                hand_id = init_id
            else:
                screen = self.capture_screen()
                hand_id, _ = self.extract_hand_info(screen)

            id_str = f"#{hand_id}" if hand_id else f"hand{h_num:02d}"
            filename = f"hand_{h_num:02d}_{id_str}.png"
            filepath = os.path.join(session_dir, filename)
            screen.save(filepath)

            captured_records.append({
                "hand_number": h_num,
                "hand_id": hand_id,
                "filename": filename,
                "size_bytes": os.path.getsize(filepath)
            })
            print(f"  [HAND {h_num:02d}/{actual_total}] Saved {filename} ({os.path.getsize(filepath) // 1024} KB)")

            if h_num > 1:
                self.tap(REPLAYER_LEFT_ARROW[0], REPLAYER_LEFT_ARROW[1], delay=0.25)

        # Write session metadata manifest
        manifest = {
            "session_key": key,
            "slot": slot_num,
            "date": slot_info["date"],
            "time": slot_info["time"],
            "game_type": slot_info["game_type"],
            "blinds": slot_info["blinds"],
            "pnl": slot_info["pnl"],
            "total_hands": actual_total,
            "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "hands": captured_records
        }
        with open(os.path.join(session_dir, "session_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        # Return back to list screen
        self.tap(REPLAYER_BACK_BTN[0], REPLAYER_BACK_BTN[1], delay=0.8)

        # Update master catalog
        self.crawled_sessions[key] = {
            "folder": folder_name,
            "total_hands": actual_total,
            "completed_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_catalog()
        return True

    def run(self, max_sessions: int = 50):
        print("=" * 60)
        print("Starting ClubGG Game History Crawler (Bottom-Up Traversal)")
        print(f"Already crawled: {len(self.crawled_sessions)} sessions")
        print("=" * 60)

        total_crawled_this_run = 0

        while total_crawled_this_run < max_sessions:
            screen = self.capture_screen()
            slots = self.parse_slots_on_screen(screen)

            # Filter valid slots
            valid_slots = [s for s in slots if s["hands"] is not None and s["date"] is not None]
            if not valid_slots:
                print("[WARN] No valid session slots detected on screen. Waiting...")
                time.sleep(1.0)
                continue

            # Process slots bottom-to-top (Slot 7 down to Slot 1)
            new_in_view = 0
            for slot in reversed(slots):
                if slot["hands"] is None or slot["date"] is None:
                    continue
                if slot["key"] in self.crawled_sessions:
                    print(f"[SKIP] Already crawled: {slot['key']}")
                    continue

                success = self.crawl_session(slot)
                if success:
                    total_crawled_this_run += 1
                    new_in_view += 1
                    if total_crawled_this_run >= max_sessions:
                        break

            # If no new sessions on this screen, scroll up to reveal newer ones
            if new_in_view == 0:
                print("\n[SCROLL] No unvisited sessions in current view. Scrolling up to reveal newer sessions...")
                # Drag down to scroll up: 500, 550 -> 500, 1855 (~5 slots)
                self.swipe(500, 550, 500, 1855, duration_ms=400, delay=1.2)
                
                # Verify if screen actually moved
                new_screen = self.capture_screen()
                new_slots = self.parse_slots_on_screen(new_screen)
                unvisited = [s for s in new_slots if s["hands"] and s["date"] and s["key"] not in self.crawled_sessions]
                if not unvisited:
                    print("[INFO] Reached the top of the history list! All sessions have been crawled.")
                    break

        print("\n" + "=" * 60)
        print(f"Crawl Complete! Total sessions crawled in this run: {total_crawled_this_run}")
        print(f"Total sessions cataloged: {len(self.crawled_sessions)}")
        print("=" * 60)


if __name__ == "__main__":
    crawler = ClubGGHistoryCrawler()
    limit = 50
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        limit = int(sys.argv[1])
    crawler.run(max_sessions=limit)

