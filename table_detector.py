#!/usr/bin/env python3
"""
ClubGG Poker Table Detector & HUD State Extractor
=================================================
Extracts real-time poker table state from ClubGG screenshots and ADB screencaps:
  - Table configuration (8-max / 6-max, game type, blinds / tournament rates)
  - Total pot size and community cards (preflop, flop, turn, river)
  - Dealer button ('D') detection and automatic position calculation (BTN, SB, BB, UTG, CO, etc.)
  - Per-seat analysis (Seat 1 to 8):
      * Occupied vs empty ("Take Seat")
      * Active in hand (card backs) vs folded vs sitting out
      * Player username
      * Stack size (in chips / BB)
      * VPIP / stat score
      * Current action badge (Check, Call, Bet, Raise, All-In, Fold)
      * Bet amount on table felt
  - Waiting player queue count

Powered by high-performance native Windows OCR (Windows.Media.Ocr) & Pillow.
"""

import argparse
import asyncio
from dataclasses import dataclass, field, asdict
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import List, Optional, Dict, Any, Tuple
from PIL import Image, ImageOps

# Native Windows OCR
try:
    import winrt.windows.media.ocr as win_ocr
    import winrt.windows.graphics.imaging as win_imaging
    import winrt.windows.storage as win_storage
    WINRT_OCR_AVAILABLE = True
except ImportError:
    WINRT_OCR_AVAILABLE = False


# ---------------------------------------------------------
# Normalized Geometry Layout for 8-Max Tables (ClubGG)
# ---------------------------------------------------------
# Normalized coordinates relative to full table: (xmin, ymin, xmax, ymax)
SEATS_8MAX = [
    {
        "seat_id": 1,
        "name": "Seat 1 (Top-Left)",
        "box": (0.00, 0.25, 0.25, 0.36),
        "card_box": (0.02, 0.26, 0.17, 0.31),
        "name_box": (0.03, 0.30, 0.22, 0.34),
        "stack_box": (0.04, 0.32, 0.20, 0.36),
        "vpip_box": (0.00, 0.28, 0.08, 0.33),
        "bet_box": (0.18, 0.32, 0.32, 0.37),
    },
    {
        "seat_id": 2,
        "name": "Seat 2 (Top-Center)",
        "box": (0.38, 0.16, 0.62, 0.27),
        "card_box": (0.42, 0.17, 0.57, 0.22),
        "name_box": (0.40, 0.21, 0.60, 0.24),
        "stack_box": (0.42, 0.23, 0.58, 0.26),
        "vpip_box": (0.36, 0.19, 0.44, 0.24),
        "bet_box": (0.44, 0.26, 0.56, 0.31),
    },
    {
        "seat_id": 3,
        "name": "Seat 3 (Top-Right)",
        "box": (0.75, 0.25, 1.00, 0.36),
        "card_box": (0.82, 0.26, 0.97, 0.31),
        "name_box": (0.78, 0.30, 0.98, 0.34),
        "stack_box": (0.80, 0.32, 0.96, 0.36),
        "vpip_box": (0.76, 0.28, 0.84, 0.33),
        "bet_box": (0.68, 0.32, 0.82, 0.37),
    },
    {
        "seat_id": 4,
        "name": "Seat 4 (Mid-Right)",
        "box": (0.75, 0.36, 1.00, 0.47),
        "card_box": (0.82, 0.37, 0.97, 0.42),
        "name_box": (0.78, 0.41, 0.98, 0.45),
        "stack_box": (0.80, 0.43, 0.96, 0.47),
        "vpip_box": (0.76, 0.39, 0.84, 0.44),
        "bet_box": (0.68, 0.41, 0.82, 0.46),
    },
    {
        "seat_id": 5,
        "name": "Seat 5 (Bottom-Right)",
        "box": (0.74, 0.56, 1.00, 0.68),
        "card_box": (0.81, 0.57, 0.96, 0.62),
        "name_box": (0.78, 0.62, 0.98, 0.65),
        "stack_box": (0.80, 0.64, 0.96, 0.68),
        "vpip_box": (0.75, 0.60, 0.83, 0.65),
        "bet_box": (0.65, 0.56, 0.80, 0.62),
    },
    {
        "seat_id": 6,
        "name": "Seat 6 (Bottom-Left)",
        "box": (0.03, 0.73, 0.35, 0.86),
        "card_box": (0.06, 0.74, 0.26, 0.81),
        "name_box": (0.04, 0.80, 0.30, 0.84),
        "stack_box": (0.06, 0.82, 0.26, 0.86),
        "vpip_box": (0.02, 0.77, 0.12, 0.82),
        "bet_box": (0.24, 0.70, 0.38, 0.76),
    },
    {
        "seat_id": 7,
        "name": "Seat 7 (Lower-Mid-Left)",
        "box": (0.00, 0.56, 0.26, 0.68),
        "card_box": (0.02, 0.57, 0.19, 0.63),
        "name_box": (0.02, 0.62, 0.22, 0.65),
        "stack_box": (0.04, 0.64, 0.20, 0.68),
        "vpip_box": (0.00, 0.60, 0.08, 0.65),
        "bet_box": (0.18, 0.56, 0.32, 0.62),
    },
    {
        "seat_id": 8,
        "name": "Seat 8 (Mid-Left)",
        "box": (0.00, 0.36, 0.26, 0.47),
        "card_box": (0.02, 0.37, 0.17, 0.42),
        "name_box": (0.02, 0.41, 0.22, 0.45),
        "stack_box": (0.04, 0.43, 0.20, 0.47),
        "vpip_box": (0.00, 0.39, 0.08, 0.44),
        "bet_box": (0.18, 0.41, 0.32, 0.46),
    }
]

COMMUNITY_CARDS_BOX = (0.18, 0.47, 0.82, 0.56)
POT_BOX = (0.35, 0.43, 0.65, 0.48)
BLINDS_BOX = (0.30, 0.63, 0.70, 0.72)
WAITING_QUEUE_BOX = (0.50, 0.93, 0.95, 0.98)


# ---------------------------------------------------------
# Data Models
# ---------------------------------------------------------
@dataclass
class PlayerSeat:
    seat_id: int
    name: str
    is_occupied: bool = False
    is_sitting_out: bool = False
    is_in_hand: bool = False
    position: Optional[str] = None
    username: Optional[str] = None
    stack: Optional[float] = None
    vpip: Optional[int] = None
    action: Optional[str] = None
    current_bet: Optional[float] = None

@dataclass
class PokerTableState:
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    table_type: str = "8-max"
    game_type: str = "NLH"
    blinds: Optional[str] = None
    total_pot: Optional[float] = None
    board_stage: str = "preflop"
    community_cards: List[str] = field(default_factory=list)
    dealer_seat: Optional[int] = None
    total_seats: int = 8
    occupied_seats: int = 0
    active_players_in_hand: int = 0
    sitting_out_players: int = 0
    waiting_players: Optional[int] = None
    seats: List[PlayerSeat] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------
# OCR & Vision Processing Engine
# ---------------------------------------------------------
class ClubGGTableDetector:
    def __init__(self, temp_dir="scratch"):
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)
        self.ocr_engine = None
        if WINRT_OCR_AVAILABLE:
            try:
                self.ocr_engine = win_ocr.OcrEngine.try_create_from_user_profile_languages()
            except Exception as e:
                print(f"[WARN] Failed to initialize Windows OCR: {e}")

    async def _run_winrt_ocr(self, img: Image.Image) -> List[Tuple[float, float, float, float, str]]:
        """Runs Windows OCR on image, returning list of (norm_x, norm_y, norm_w, norm_h, text)."""
        if not self.ocr_engine:
            return []
        
        # Save temp image
        tmp_file = os.path.abspath(os.path.join(self.temp_dir, f"_ocr_tmp_{os.getpid()}.png"))
        img.save(tmp_file)
        
        try:
            storage_file = await win_storage.StorageFile.get_file_from_path_async(tmp_file)
            stream = await storage_file.open_async(win_storage.FileAccessMode.READ)
            decoder = await win_imaging.BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            ocr_result = await self.ocr_engine.recognize_async(bitmap)
            
            w, h = img.size
            results = []
            for line in ocr_result.lines:
                for word in line.words:
                    bb = word.bounding_rect
                    results.append((
                        bb.x / float(w),
                        bb.y / float(h),
                        bb.width / float(w),
                        bb.height / float(h),
                        word.text.strip()
                    ))
            return results
        except Exception:
            return []
        finally:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass

    def run_ocr(self, img: Image.Image) -> List[Tuple[float, float, float, float, str]]:
        """Synchronous wrapper for Windows OCR."""
        try:
            return asyncio.run(self._run_winrt_ocr(img))
        except Exception:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            res = loop.run_until_complete(self._run_winrt_ocr(img))
            loop.close()
            return res

    def detect_table_state(self, image_input) -> PokerTableState:
        """Main detection entrypoint taking PIL Image or file path."""
        if isinstance(image_input, str):
            img = Image.open(image_input)
        else:
            img = image_input

        # Normalize to RGB
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size

        # Upscale for OCR if small
        scale = 2.0 if w < 600 else 1.0
        if scale > 1.0:
            ocr_img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        else:
            ocr_img = img

        ocr_words = self.run_ocr(ocr_img)

        table_state = PokerTableState()

        # 1. Parse Table Blinds & Tournament Info
        table_state.blinds = self._extract_blinds(ocr_words)

        # 2. Parse Total Pot
        table_state.total_pot = self._extract_total_pot(ocr_words)

        # 3. Detect Community Cards & Board Stage
        cards, stage = self._detect_community_cards(img)
        table_state.community_cards = cards
        table_state.board_stage = stage

        # 4. Detect Waiting Queue Count
        table_state.waiting_players = self._extract_waiting_queue(ocr_words)

        # 5. Detect Dealer Button ('D') Position
        dealer_seat_id = self._detect_dealer_button(img)
        table_state.dealer_seat = dealer_seat_id

        # 6. Parse All 8 Seats
        seats = []
        for seat_cfg in SEATS_8MAX:
            seat_obj = self._analyze_seat(img, seat_cfg, ocr_words)
            seats.append(seat_obj)

        # 7. Assign Positions (BTN, SB, BB, UTG, etc.)
        self._assign_positions(seats, dealer_seat_id)

        table_state.seats = seats
        table_state.occupied_seats = sum(1 for s in seats if s.is_occupied)
        table_state.active_players_in_hand = sum(1 for s in seats if s.is_in_hand)
        table_state.sitting_out_players = sum(1 for s in seats if s.is_sitting_out)

        return table_state

    # ---------------------------------------------------------
    # Helper Extractors
    # ---------------------------------------------------------
    def _extract_blinds(self, words) -> Optional[str]:
        # Search for pattern like "0.25/0.50" or "0.50/1" or "Blinds X/Y"
        for _, ny, _, _, text in words:
            if 0.58 <= ny <= 0.75:
                m = re.search(r'(\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?)', text)
                if m:
                    return m.group(1).replace(" ", "")
        return None

    def _extract_total_pot(self, words) -> Optional[float]:
        # Search around center pot area (norm_y ~ 0.40 .. 0.50)
        pot_candidates = []
        is_after_pot_label = False
        for nx, ny, _, _, text in words:
            if 0.40 <= ny <= 0.52 and 0.35 <= nx <= 0.65:
                if "pot" in text.lower():
                    is_after_pot_label = True
                    continue
                # Extract float
                num_str = re.sub(r'[^\d\.]', '', text)
                if num_str and num_str.count('.') <= 1:
                    try:
                        val = float(num_str)
                        if 0.1 <= val <= 1000000:
                            pot_candidates.append(val)
                    except ValueError:
                        pass
        return pot_candidates[0] if pot_candidates else None

    def _extract_waiting_queue(self, words) -> Optional[int]:
        for _, ny, _, _, text in words:
            if ny >= 0.90:
                m = re.search(r'Player\s*:\s*(\d+)', text, re.IGNORECASE)
                if m:
                    return int(m.group(1))
                if text.isdigit():
                    return int(text)
        return None

    def _detect_community_cards(self, img: Image.Image) -> Tuple[List[str], str]:
        w, h = img.size
        bx1, by1, bx2, by2 = COMMUNITY_CARDS_BOX
        box = (int(bx1 * w), int(by1 * h), int(bx2 * w), int(by2 * h))
        crop = img.crop(box)
        
        # Check white pixel ratio across 5 potential card slots
        slot_w = crop.width // 5
        detected_cards = 0
        
        for i in range(5):
            slot = crop.crop((i * slot_w, 0, (i + 1) * slot_w, crop.height))
            pixels = [slot.getpixel((x, y)) for y in range(slot.height) for x in range(slot.width)]
            white_px = sum(1 for p in pixels if p[0] > 195 and p[1] > 195 and p[2] > 195)
            if (white_px / len(pixels)) > 0.25:
                detected_cards += 1

        stage_map = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}
        stage = stage_map.get(detected_cards, "preflop" if detected_cards < 3 else "river")
        
        # Card slots
        card_names = [f"BoardCard_{i+1}" for i in range(detected_cards)]
        return card_names, stage

    def _detect_dealer_button(self, img: Image.Image) -> Optional[int]:
        """Finds gold circular Dealer Button 'D' and maps it to closest seat."""
        w, h = img.size
        # Search on table felt
        felt_box = (int(0.05 * w), int(0.20 * h), int(0.95 * w), int(0.75 * h))
        felt = img.crop(felt_box)
        
        gold_pts = []
        for y in range(0, felt.height, 2):
            for x in range(0, felt.width, 2):
                r, g, b = felt.getpixel((x, y))[:3]
                if r > 190 and g > 150 and b < 90:
                    gold_pts.append((x + felt_box[0], y + felt_box[1]))
        
        if not gold_pts:
            return None

        # Simple clustering
        clusters = []
        for p in gold_pts:
            added = False
            for c in clusters:
                cx = sum(pt[0] for pt in c) / len(c)
                cy = sum(pt[1] for pt in c) / len(c)
                if abs(p[0] - cx) < 35 and abs(p[1] - cy) < 35:
                    c.append(p)
                    added = True
                    break
            if not added:
                clusters.append([p])

        # Filter for dealer button cluster (circular, ~30 to 200 points)
        btn_center = None
        for c in clusters:
            if 30 <= len(c) <= 250:
                cx = sum(pt[0] for pt in c) / len(c)
                cy = sum(pt[1] for pt in c) / len(c)
                btn_center = (cx / w, cy / h)
                break

        if not btn_center:
            return None

        # Map to nearest seat
        min_dist = 999.0
        nearest_seat = None
        for s in SEATS_8MAX:
            sx = (s["box"][0] + s["box"][2]) / 2.0
            sy = (s["box"][1] + s["box"][3]) / 2.0
            dist = math.hypot(btn_center[0] - sx, btn_center[1] - sy)
            if dist < min_dist:
                min_dist = dist
                nearest_seat = s["seat_id"]

        return nearest_seat

    def _analyze_seat(self, img: Image.Image, seat_cfg: Dict[str, Any], words: List[Tuple]) -> PlayerSeat:
        w, h = img.size
        sid = seat_cfg["seat_id"]
        sname = seat_cfg["name"]
        bx1, by1, bx2, by2 = seat_cfg["box"]
        
        seat = PlayerSeat(seat_id=sid, name=sname)

        # 1. Check Words in this Seat Box
        seat_words = []
        for nx, ny, nw, nh, text in words:
            # Word center inside seat bounding box
            if (bx1 - 0.03) <= nx <= (bx2 + 0.03) and (by1 - 0.02) <= ny <= (by2 + 0.03):
                seat_words.append((nx, ny, text))

        all_text_lower = " ".join(t[2].lower() for t in seat_words)
        
        # Check empty seat
        if "take" in all_text_lower and "seat" in all_text_lower:
            seat.is_occupied = False
            return seat

        # 2. In-Hand Detection (Check Card Backs)
        cx1, cy1, cx2, cy2 = seat_cfg["card_box"]
        c_crop = img.crop((int(cx1 * w), int(cy1 * h), int(cx2 * w), int(cy2 * h)))
        c_pix = [c_crop.getpixel((x, y)) for y in range(c_crop.height) for x in range(c_crop.width)]
        bright_px = sum(1 for p in c_pix if p[0] > 170 and p[1] > 170 and p[2] > 170)
        card_ratio = bright_px / float(len(c_pix)) if c_pix else 0.0
        seat.is_in_hand = (card_ratio > 0.16)

        # 3. Action Badges (Check, Call, Bet, Raise, All-In, WIN)
        action_keywords = ["check", "call", "bet", "raise", "all-in", "allin", "fold", "win"]
        for _, _, text in seat_words:
            tl = text.lower().replace("-", "")
            for act in action_keywords:
                if act in tl:
                    seat.action = act.capitalize()
                    break
            if seat.action:
                break

        # 4. Stack Size (Cyan / Blue number below name)
        # Look for numbers with decimal e.g. 14.89, 47.43 or integers like 47
        stack_candidates = []
        for nx, ny, text in seat_words:
            clean = re.sub(r'[^\d\.]', '', text)
            if clean and clean.count('.') <= 1:
                try:
                    val = float(clean)
                    if 0.01 <= val <= 1000000 and val != sid:
                        stack_candidates.append((ny, val))
                except ValueError:
                    pass
        
        if stack_candidates:
            # Stack is lowest down in the seat box
            stack_candidates.sort(key=lambda x: x[0], reverse=True)
            seat.stack = stack_candidates[0][1]
            seat.is_occupied = True

        # 5. VPIP Score (Badge top-left of name)
        vbx1, vby1, vbx2, vby2 = seat_cfg["vpip_box"]
        vpip_words = []
        for nx, ny, text in seat_words:
            if (vbx1 - 0.04) <= nx <= (vbx2 + 0.04) and (by1 - 0.02) <= ny <= (by1 + 0.08):
                clean = re.sub(r'\D', '', text)
                if clean and 5 <= int(clean) <= 100:
                    vpip_words.append(int(clean))
        if vpip_words:
            seat.vpip = vpip_words[0]
            seat.is_occupied = True

        # 6. Username
        name_tokens = []
        for nx, ny, text in seat_words:
            # Skip if it's the stack, vpip, or action badge
            tl = text.lower()
            if any(act in tl for act in action_keywords):
                continue
            if re.sub(r'[^\d\.]', '', text) and seat.stack and abs(float(re.sub(r'[^\d\.]', '', text) or 0) - seat.stack) < 0.01:
                continue
            if text.isdigit() and seat.vpip and int(text) == seat.vpip:
                continue
            if len(text) >= 2 and not text.isdigit():
                name_tokens.append(text)

        if name_tokens:
            seat.username = " ".join(name_tokens)
            seat.is_occupied = True

        # Check Sitting Out
        if "sitting" in all_text_lower or "away" in all_text_lower:
            seat.is_sitting_out = True

        # Check Bet Amount on Felt
        bet_x1, bet_y1, bet_x2, bet_y2 = seat_cfg["bet_box"]
        for nx, ny, _, _, text in words:
            if bet_x1 <= nx <= bet_x2 and bet_y1 <= ny <= bet_y2:
                clean = re.sub(r'[^\d\.]', '', text)
                if clean and clean.count('.') <= 1:
                    try:
                        b_val = float(clean)
                        if 0.01 <= b_val <= 100000:
                            seat.current_bet = b_val
                    except ValueError:
                        pass

        return seat

    def _assign_positions(self, seats: List[PlayerSeat], dealer_seat_id: Optional[int]):
        """Calculates poker positions (BTN, SB, BB, UTG, MP, CO) relative to dealer seat."""
        if not dealer_seat_id:
            return

        occupied = [s for s in seats if s.is_occupied]
        num_players = len(occupied)
        if num_players < 2:
            return

        # Sort occupied seats in clockwise order starting from BTN
        occ_sorted = sorted(occupied, key=lambda s: (s.seat_id - dealer_seat_id) % len(seats))

        if num_players == 2:
            positions = ["BTN/SB", "BB"]
        elif num_players == 3:
            positions = ["BTN", "SB", "BB"]
        elif num_players == 6:
            positions = ["BTN", "SB", "BB", "UTG", "MP", "CO"]
        elif num_players == 8:
            positions = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO"]
        else:
            positions = ["BTN", "SB", "BB"] + [f"UTG+{i}" for i in range(num_players - 4)] + ["CO"]

        for i, seat in enumerate(occ_sorted):
            if i < len(positions):
                seat.position = positions[i]


# ---------------------------------------------------------
# CLI & Standalone Runner
# ---------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ClubGG Poker Table Detector & HUD State Extractor")
    parser.add_argument("-i", "--image", help="Path to table screenshot image (PNG/JPG)")
    parser.add_argument("-s", "--screencap", action="store_true", help="Capture live screencap directly from connected device via ADB")
    parser.add_argument("-o", "--output", help="Save output JSON to specified file path")
    parser.add_argument("--pretty", action="store_true", default=True, help="Pretty print JSON output")
    args = parser.parse_args()

    detector = ClubGGTableDetector()

    if args.screencap:
        print("[*] Capturing live screencap from device...")
        os.makedirs("scratch", exist_ok=True)
        img_path = "scratch/live_screencap.png"
        with open(img_path, "wb") as f:
            subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=f, check=True)
        target = img_path
    elif args.image:
        target = args.image
    else:
        # Default to latest capture in captures/ if available
        import glob
        caps = sorted(glob.glob("captures/event_*_audio.png"), reverse=True)
        if caps:
            target = caps[0]
            print(f"[*] No image specified. Using latest capture: {target}")
        else:
            parser.print_help()
            sys.exit(1)

    print(f"[*] Analyzing poker table: {target} ...")
    t0 = time.time()
    state = detector.detect_table_state(target)
    elapsed = time.time() - t0

    out_data = state.to_dict()

    print("=" * 75)
    print("                CLUBGG POKER TABLE DETECTION RESULT")
    print("=" * 75)
    print(f" Table Type       : {state.table_type} ({state.game_type})")
    print(f" Blinds           : {state.blinds or 'N/A'}")
    print(f" Board Stage      : {state.board_stage.upper()} ({len(state.community_cards)} cards)")
    print(f" Total Pot        : {state.total_pot if state.total_pot is not None else 'N/A'}")
    print(f" Dealer Seat      : Seat {state.dealer_seat} (BTN)")
    print(f" Seated Players   : {state.occupied_seats} / {state.total_seats}")
    print(f" Active In Hand   : {state.active_players_in_hand}")
    print(f" Waiting Queue    : {state.waiting_players if state.waiting_players is not None else 'N/A'}")
    print(f" Analysis Time    : {elapsed:.2f}s")
    print("-" * 75)
    print(f" {'Seat':<10} {'Pos':<8} {'Username':<15} {'Stack':<10} {'VPIP':<8} {'In Hand':<10} {'Action':<10}")
    print("-" * 75)
    for s in state.seats:
        if s.is_occupied:
            in_hand_str = "YES" if s.is_in_hand else "No (fold)"
            vpip_str = f"{s.vpip}%" if s.vpip is not None else "-"
            stack_str = f"{s.stack:.2f}" if s.stack is not None else "-"
            action_str = s.action or "-"
            pos_str = s.position or "-"
            print(f" {s.seat_id:<10} {pos_str:<8} {s.username or 'Unknown':<15} {stack_str:<10} {vpip_str:<8} {in_hand_str:<10} {action_str:<10}")
        else:
            print(f" {s.seat_id:<10} {'-':<8} {'[EMPTY SEAT]':<15} {'-':<10} {'-':<8} {'-':<10} {'Take Seat':<10}")
    print("=" * 75)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out_data, f, indent=2 if args.pretty else None)
        print(f"[+] Saved structured table state to: {args.output}")

if __name__ == "__main__":
    main()
