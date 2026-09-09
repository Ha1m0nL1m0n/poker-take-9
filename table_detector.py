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
        "name_box": (0.055, 0.302, 0.210, 0.330),
        "stack_box": (0.055, 0.330, 0.210, 0.355),
        "vpip_box": (0.00, 0.28, 0.08, 0.33),
        "bet_box": (0.18, 0.32, 0.32, 0.38),
    },
    {
        "seat_id": 2,
        "name": "Seat 2 (Top-Center)",
        "box": (0.38, 0.16, 0.62, 0.27),
        "card_box": (0.42, 0.17, 0.57, 0.22),
        "name_box": (0.440, 0.210, 0.600, 0.235),
        "stack_box": (0.440, 0.235, 0.600, 0.258),
        "vpip_box": (0.36, 0.19, 0.44, 0.24),
        "bet_box": (0.42, 0.26, 0.58, 0.31),
    },
    {
        "seat_id": 3,
        "name": "Seat 3 (Top-Right)",
        "box": (0.75, 0.25, 1.00, 0.36),
        "card_box": (0.82, 0.26, 0.97, 0.31),
        "name_box": (0.835, 0.302, 0.990, 0.330),
        "stack_box": (0.835, 0.330, 0.990, 0.355),
        "vpip_box": (0.76, 0.28, 0.84, 0.33),
        "bet_box": (0.68, 0.32, 0.82, 0.38),
    },
    {
        "seat_id": 4,
        "name": "Seat 4 (Mid-Right)",
        "box": (0.75, 0.36, 1.00, 0.47),
        "card_box": (0.82, 0.37, 0.97, 0.42),
        "name_box": (0.835, 0.410, 0.990, 0.440),
        "stack_box": (0.835, 0.440, 0.990, 0.465),
        "vpip_box": (0.76, 0.39, 0.84, 0.44),
        "bet_box": (0.68, 0.40, 0.82, 0.47),
    },
    {
        "seat_id": 5,
        "name": "Seat 5 (Bottom-Right)",
        "box": (0.74, 0.56, 1.00, 0.68),
        "card_box": (0.81, 0.57, 0.96, 0.62),
        "name_box": (0.820, 0.625, 0.985, 0.655),
        "stack_box": (0.820, 0.655, 0.985, 0.680),
        "vpip_box": (0.75, 0.60, 0.83, 0.65),
        "bet_box": (0.66, 0.64, 0.80, 0.72),
    },
    {
        "seat_id": 6,
        "name": "Seat 6 (Bottom-Left)",
        "box": (0.03, 0.73, 0.35, 0.86),
        "card_box": (0.06, 0.74, 0.26, 0.81),
        "name_box": (0.100, 0.805, 0.260, 0.830),
        "stack_box": (0.100, 0.830, 0.260, 0.855),
        "vpip_box": (0.02, 0.77, 0.12, 0.82),
        "bet_box": (0.32, 0.74, 0.48, 0.81),
    },
    {
        "seat_id": 7,
        "name": "Seat 7 (Lower-Mid-Left)",
        "box": (0.00, 0.56, 0.26, 0.68),
        "card_box": (0.02, 0.57, 0.19, 0.63),
        "name_box": (0.055, 0.625, 0.220, 0.655),
        "stack_box": (0.055, 0.655, 0.220, 0.680),
        "vpip_box": (0.00, 0.60, 0.08, 0.65),
        "bet_box": (0.20, 0.64, 0.34, 0.72),
    },
    {
        "seat_id": 8,
        "name": "Seat 8 (Mid-Left)",
        "box": (0.00, 0.36, 0.26, 0.47),
        "card_box": (0.02, 0.37, 0.17, 0.42),
        "name_box": (0.055, 0.420, 0.220, 0.445),
        "stack_box": (0.055, 0.445, 0.220, 0.468),
        "vpip_box": (0.00, 0.39, 0.08, 0.44),
        "bet_box": (0.18, 0.40, 0.32, 0.47),
    }
]

SEATS_HEADS_UP = [
    {
        "seat_id": 1,
        "name": "Hero (Bottom-Left)",
        "box": (0.03, 0.73, 0.35, 0.86),
        "card_box": (0.06, 0.74, 0.26, 0.81),
        "name_box": (0.050, 0.805, 0.260, 0.830),
        "stack_box": (0.080, 0.830, 0.260, 0.855),
        "vpip_box": (0.02, 0.77, 0.12, 0.82),
        "bet_box": (0.32, 0.74, 0.48, 0.81),
    },
    {
        "seat_id": 2,
        "name": "Opponent (Top-Center)",
        "box": (0.38, 0.16, 0.62, 0.27),
        "card_box": (0.42, 0.17, 0.57, 0.22),
        "name_box": (0.440, 0.210, 0.600, 0.235),
        "stack_box": (0.440, 0.235, 0.600, 0.258),
        "vpip_box": (0.36, 0.19, 0.44, 0.24),
        "bet_box": (0.42, 0.26, 0.58, 0.31),
    }
]

COMMUNITY_CARDS_BOX = (0.18, 0.47, 0.82, 0.56)
COMMUNITY_CARDS_HU_BOX = (0.15, 0.47, 0.85, 0.57)
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
    cards: List[str] = field(default_factory=list)
    cards_detail: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class PokerTableState:
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    table_type: str = "8-max" # "8-max" or "heads_up"
    game_type: str = "NLH"
    blinds: Optional[str] = None
    total_pot: Optional[float] = None
    board_stage: str = "preflop"
    community_cards: List[str] = field(default_factory=list)
    cards_detail: List[Dict[str, Any]] = field(default_factory=list)
    board_integrity: Optional[Dict[str, Any]] = None
    dealer_seat: Optional[int] = None
    total_seats: int = 8
    occupied_seats: int = 0
    active_players_in_hand: int = 0
    sitting_out_players: int = 0
    waiting_players: Optional[int] = None
    seats: List[PlayerSeat] = field(default_factory=list)
    
    # Heads-Up & Automation Additions
    hero_cards: List[str] = field(default_factory=list)
    hero_cards_detail: List[Dict[str, Any]] = field(default_factory=list)
    is_hero_turn: bool = False
    action_buttons: Optional[Dict[str, Any]] = None
    tournament_info: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



# ---------------------------------------------------------
# OCR & Vision Processing Engine
# ---------------------------------------------------------
from card_detector import CardDetector

class ClubGGTableDetector:
    def __init__(self, temp_dir="scratch"):
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)
        self.card_detector = CardDetector()
        self.ocr_engine = None
        if WINRT_OCR_AVAILABLE:
            try:
                self.ocr_engine = win_ocr.OcrEngine.try_create_from_user_profile_languages()
            except Exception as e:
                print(f"[WARN] Failed to initialize Windows OCR: {e}")

        # Temporal Hand State Tracking & Persistence
        self._last_confirmed_pot: Optional[float] = None
        self._last_community_cards: List[str] = []
        self._last_board_stage: str = "preflop"
        self._seat_usernames: Dict[int, str] = {}
        self._seat_stacks: Dict[int, float] = {}


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

    def _is_cyan_token(self, img: Image.Image, nx: float, ny: float, nw: float, nh: float) -> bool:
        """Determines if an OCR word consists of ClubGG neon cyan stack text."""
        w, h = img.size
        x1 = max(0, int(nx * w))
        y1 = max(0, int(ny * h))
        x2 = min(w, int((nx + nw) * w))
        y2 = min(h, int((ny + nh) * h))
        if x2 <= x1 or y2 <= y1:
            return False
        crop = img.crop((x1, y1, x2, y2))
        cw, ch = crop.size
        total = cw * ch
        if total == 0:
            return False
        cyan_count = sum(
            1 for y in range(ch) for x in range(cw)
            if crop.getpixel((x, y))[0] < 120 and crop.getpixel((x, y))[1] > 140 and crop.getpixel((x, y))[2] > 170
        )
        return (cyan_count / float(total)) > 0.04

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
        ocr_words = self.run_ocr(img)

        table_state = PokerTableState()

        # Determine table mode: Heads-Up vs 8-Max
        all_text_lower = " ".join(t[4].lower() for t in ocr_words)
        is_heads_up = ("rank" in all_text_lower and ("1st" in all_text_lower or "2nd" in all_text_lower)) or \
                      ("min." in all_text_lower and "stack" in all_text_lower) or \
                      ("next" in all_text_lower and "blinds" in all_text_lower)

        if is_heads_up:
            table_state.table_type = "heads_up"
            table_state.total_seats = 2

            # 1. Parse Blinds & Tournament Info
            tourney_info = self._extract_tournament_info(ocr_words)
            table_state.tournament_info = tourney_info
            if tourney_info and tourney_info.get("blinds"):
                table_state.blinds = tourney_info["blinds"]
            else:
                table_state.blinds = self._extract_blinds(ocr_words)

            # 2. Parse Total Pot
            table_state.total_pot = self._extract_total_pot(ocr_words, img)

            # 3. Detect Community Cards & Board Stage (with HU community box)
            rep = self.card_detector.detect_community_cards(img, comm_box=COMMUNITY_CARDS_HU_BOX)
            table_state.community_cards = [c.card for c in rep.cards]
            table_state.board_stage = rep.stage.lower()
            table_state.cards_detail = [c.to_dict() for c in rep.cards]
            table_state.board_integrity = rep.to_dict()

            # 4. Detect Hero Hole Cards
            hero_card_objs = self.card_detector.detect_hero_hole_cards(img)
            table_state.hero_cards = [c.card for c in hero_card_objs]
            table_state.hero_cards_detail = [c.to_dict() for c in hero_card_objs]

            # 5. Detect Dealer Button ('D') Position in Heads-Up
            dealer_seat_id = self._detect_hu_dealer_button(img)
            table_state.dealer_seat = dealer_seat_id

            # 6. Parse Heads-Up Seats (Seat 1 = Hero, Seat 2 = Opponent)
            seats = []
            for seat_cfg in SEATS_HEADS_UP:
                seat_obj = self._analyze_seat(img, seat_cfg, ocr_words)
                if seat_cfg["seat_id"] == 1:
                    seat_obj.cards = list(table_state.hero_cards)
                    seat_obj.cards_detail = list(table_state.hero_cards_detail)
                    if not seat_obj.username:
                        seat_obj.username = "Hero"
                        seat_obj.is_occupied = True
                seats.append(seat_obj)

            # 7. Assign Positions (BTN/SB vs BB)
            if dealer_seat_id == 1:
                seats[0].position = "BTN/SB"
                seats[1].position = "BB"
            elif dealer_seat_id == 2:
                seats[0].position = "BB"
                seats[1].position = "BTN/SB"

            # 8. Parse Action Buttons & Hero Turn Status
            act_info = self._extract_action_buttons(ocr_words, w, h)
            table_state.is_hero_turn = act_info["is_hero_turn"]
            table_state.action_buttons = act_info
        else:
            # Standard 8-Max Pipeline
            # 1. Parse Table Blinds & Tournament Info
            table_state.blinds = self._extract_blinds(ocr_words)

            # 2. Parse Total Pot (with crop fallback)
            table_state.total_pot = self._extract_total_pot(ocr_words, img)

            # 3. Detect Community Cards & Board Stage with CardDetector
            cards, stage, cards_detail, board_integrity = self._detect_community_cards(img)
            table_state.community_cards = cards
            table_state.board_stage = stage
            table_state.cards_detail = cards_detail
            table_state.board_integrity = board_integrity

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

            # 8. Parse Action Buttons
            act_info = self._extract_action_buttons(ocr_words, w, h)
            table_state.is_hero_turn = act_info["is_hero_turn"]
            table_state.action_buttons = act_info

        # 8. Temporal Hand State Smoothing
        is_new_hand = False
        prev_card_count = len(self._last_community_cards)
        curr_card_count = len(table_state.community_cards)

        if prev_card_count > 0:
            if curr_card_count < prev_card_count:
                # Board cards contracted (e.g. 5 cards down to 0 cards), hand ended
                is_new_hand = True
            elif not all(c in table_state.community_cards for c in self._last_community_cards):
                # Cards on board do not contain previous street's cards, new hand started
                is_new_hand = True

        if is_new_hand:
            self._last_confirmed_pot = None

        # Pot Smoothing: Pot in Texas Hold'em is non-decreasing during a hand
        if table_state.total_pot is not None and table_state.total_pot > 0:
            if self._last_confirmed_pot is None or table_state.total_pot >= self._last_confirmed_pot:
                self._last_confirmed_pot = table_state.total_pot
            elif curr_card_count > 0 and self._last_confirmed_pot is not None:
                table_state.total_pot = self._last_confirmed_pot
        else:
            # If OCR missed the pot during an active hand (cards present or flop/turn/river)
            if (curr_card_count > 0 or table_state.board_stage != "preflop") and self._last_confirmed_pot is not None:
                table_state.total_pot = self._last_confirmed_pot

        self._last_community_cards = list(table_state.community_cards)
        self._last_board_stage = table_state.board_stage

        # Username Smoothing: retain known usernames for occupied seats across frames
        for seat in seats:
            if not seat.is_occupied:
                self._seat_usernames.pop(seat.seat_id, None)
                self._seat_stacks.pop(seat.seat_id, None)
            elif seat.username:
                self._seat_usernames[seat.seat_id] = seat.username
            elif seat.seat_id in self._seat_usernames:
                seat.username = self._seat_usernames[seat.seat_id]

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

    def _extract_total_pot(self, words: List[Tuple], img: Optional[Image.Image] = None) -> Optional[float]:
        """Extracts total pot from center pot badge or felt chips badge with crop OCR fallback."""
        top_candidates = []
        lower_candidates = []
        for nx, ny, nw, nh, text in words:
            tl = text.lower()
            if "pot" in tl or "total" in tl or "/" in text:
                continue
            m = re.search(r'\d+(?:\.\d+)?', text)
            if m:
                try:
                    val = float(m.group(0))
                    if 0.1 <= val <= 1000000:
                        if 0.40 <= ny <= 0.52 and 0.35 <= nx <= 0.65:
                            top_candidates.append(val)
                        elif 0.58 <= ny <= 0.65 and 0.40 <= nx <= 0.60:
                            lower_candidates.append(val)
                except ValueError:
                    pass

        if top_candidates:
            return top_candidates[0]
        if lower_candidates:
            return lower_candidates[0]

        # Fast Crop OCR Fallback if global OCR missed the pot
        if img is not None:
            w, h = img.size
            # 1. Targeted crop on Top Pot pill (norm x: 0.40..0.60, y: 0.42..0.50)
            c1 = img.crop((int(0.40 * w), int(0.42 * h), int(0.60 * w), int(0.50 * h)))
            c1_2x = c1.resize((c1.width * 2, c1.height * 2), Image.LANCZOS)
            c1_words = self.run_ocr(c1_2x)
            for _, _, _, _, text in c1_words:
                tl = text.lower()
                if "pot" in tl or "total" in tl or "/" in text:
                    continue
                m = re.search(r'\d+(?:\.\d+)?', text)
                if m:
                    try:
                        val = float(m.group(0))
                        if 0.1 <= val <= 1000000:
                            return val
                    except ValueError:
                        pass

            # 2. Targeted crop on Lower Pot chip badge (norm x: 0.40..0.60, y: 0.58..0.65)
            c2 = img.crop((int(0.40 * w), int(0.58 * h), int(0.60 * w), int(0.65 * h)))
            c2_2x = c2.resize((c2.width * 2, c2.height * 2), Image.LANCZOS)
            c2_words = self.run_ocr(c2_2x)
            for _, _, _, _, text in c2_words:
                if "/" in text:
                    continue
                m = re.search(r'\d+(?:\.\d+)?', text)
                if m:
                    try:
                        val = float(m.group(0))
                        if 0.1 <= val <= 1000000:
                            return val
                    except ValueError:
                        pass

        return None

    def _extract_waiting_queue(self, words) -> Optional[int]:
        for _, ny, _, _, text in words:
            if ny >= 0.90:
                m = re.search(r'Player\s*:\s*(\d+)', text, re.IGNORECASE)
                if m:
                    return int(m.group(1))
                if text.isdigit():
                    return int(text)
        return None

    def _detect_community_cards(self, img: Image.Image) -> Tuple[List[str], str, List[Dict[str, Any]], Dict[str, Any]]:
        """Extracts high-integrity community card ranks and suits via CardDetector."""
        report = self.card_detector.detect_community_cards(img)
        card_names = [c.card for c in report.cards]
        cards_detail = [c.to_dict() for c in report.cards]
        stage = report.stage.lower()
        return card_names, stage, cards_detail, report.to_dict()


    def _detect_dealer_button(self, img: Image.Image) -> Optional[int]:
        """Finds gold circular Dealer Button 'D' and maps it to closest seat."""
        w, h = img.size
        # Search on table felt (expanded to 0.84 to cover bottom hero positions)
        felt_box = (int(0.05 * w), int(0.20 * h), int(0.95 * w), int(0.84 * h))
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

        # Filter for dealer button cluster (circular, ~20 to 250 points)
        btn_center = None
        for c in clusters:
            if 20 <= len(c) <= 250:
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

    def _detect_hu_dealer_button(self, img: Image.Image) -> Optional[int]:
        """Detects dealer button position in Heads-Up matches (1 = Hero, 2 = Opponent)."""
        w, h = img.size
        felt_box = (int(0.05 * w), int(0.15 * h), int(0.95 * w), int(0.85 * h))
        felt = img.crop(felt_box)
        gold_pts = []
        for y in range(0, felt.height, 2):
            for x in range(0, felt.width, 2):
                abs_x = (x + felt_box[0]) / float(w)
                abs_y = (y + felt_box[1]) / float(h)
                # Exclude center pot chip area and right-side sizing buttons
                if 0.42 <= abs_y <= 0.52:
                    continue
                if abs_x > 0.65 and abs_y > 0.70:
                    continue
                r, g, b = felt.getpixel((x, y))[:3]
                if r > 190 and g > 150 and b < 90:
                    gold_pts.append((x + felt_box[0], y + felt_box[1]))

        if not gold_pts:
            return None

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

        for c in sorted(clusters, key=lambda cl: len(cl), reverse=True):
            if 10 <= len(c) <= 150:
                cx = sum(pt[0] for pt in c) / len(c) / float(w)
                cy = sum(pt[1] for pt in c) / len(c) / float(h)
                d_hero = math.hypot(cx - 0.34, cy - 0.77)
                d_opp = math.hypot(cx - 0.38, cy - 0.25)
                if d_hero < d_opp and d_hero < 0.18:
                    return 1
                elif d_opp < d_hero and d_opp < 0.18:
                    return 2
        return None

    def _extract_tournament_info(self, words: List[Tuple]) -> Dict[str, Any]:
        """Extracts tournament blinds, level timer, and Hero rank."""
        tourney_words = [w for w in words if 0.62 <= w[1] <= 0.76]
        tourney_text = " ".join(w[4] for w in sorted(tourney_words, key=lambda x: (x[1], x[0])))
        
        m_blinds = re.search(r"(\d+)\s*/\s*(\d+)", tourney_text)
        sb, bb = (int(m_blinds.group(1)), int(m_blinds.group(2))) if m_blinds else (None, None)
        blinds_str = f"{sb}/{bb}" if sb and bb else None
        
        m_rank = re.search(r"(1st|2nd)\s*/\s*(\d+)", tourney_text, re.IGNORECASE)
        rank_str = m_rank.group(0) if m_rank else None
        
        return {
            "blinds": blinds_str,
            "sb": sb,
            "bb": bb,
            "rank": rank_str,
            "raw_text": tourney_text
        }

    def _extract_action_buttons(self, words: List[Tuple], img_w: int, img_h: int) -> Dict[str, Any]:
        """Extracts action buttons state (Fold, Check, Call, Bet, Raise) and pricing."""
        bottom_words = [w for w in words if w[1] > 0.92]
        full_bot = " ".join(w[4] for w in sorted(bottom_words, key=lambda x: x[0]))
        
        can_fold = "Fold" in full_bot
        can_check = "Check" in full_bot and "Fold" not in full_bot.split("Check")[0]
        can_call = "Call" in full_bot
        can_bet = "Bet" in full_bot
        can_raise = "Raise" in full_bot
        
        is_hero_turn = can_check or can_call or can_bet or can_raise or (can_fold and "Check /" not in full_bot)
        
        call_amount = None
        if can_call:
            mid_words = [w for w in bottom_words if 0.38 <= w[0] <= 0.62]
            for w in mid_words:
                clean_num = w[4].replace(",", "").replace(".", "")
                if clean_num.isdigit():
                    call_amount = float(w[4].replace(",", ""))
                    break
                    
        bet_raise_amount = None
        if can_bet or can_raise:
            right_words = [w for w in bottom_words if 0.75 <= w[0] <= 0.98]
            for w in right_words:
                clean_num = w[4].replace(",", "").replace(".", "")
                if clean_num.isdigit():
                    bet_raise_amount = float(w[4].replace(",", ""))
                    break
                    
        sizing_words = [w for w in words if 0.75 <= w[1] <= 0.92 and 0.68 <= w[0] <= 0.98]
        presets = {}
        preset_text = " ".join(w[4] for w in sorted(sizing_words, key=lambda x: (x[1], x[0])))
        for m in re.finditer(r"(33%|50%|75%|100%|Pot|Max|2x|3x|4x)\s*(?:Raise\s*to\s*)?(\d[\d,]*)", preset_text, re.IGNORECASE):
            tag = m.group(1).upper()
            val = float(m.group(2).replace(",", ""))
            presets[tag] = val

        return {
            "is_hero_turn": is_hero_turn,
            "can_fold": can_fold,
            "can_check": can_check,
            "can_call": can_call,
            "call_amount": call_amount,
            "can_bet": can_bet,
            "can_raise": can_raise,
            "bet_raise_amount": bet_raise_amount,
            "presets": presets,
            "fold_tap": (184, 2169),
            "check_call_tap": (504, 2165),
            "bet_raise_tap": (867, 2165)
        }

    def _clean_player_username(self, raw: str) -> Optional[str]:
        """Cleans and validates a candidate poker username, stripping showdown equity % and badges."""
        if not raw:
            return None
        s = raw.strip()
        # Strip leading showdown equity / badge like '(100 ' or '100% ' or '100 '
        s = re.sub(r'^\(?\d+\%?\)?\s+', '', s)
        # Strip trailing showdown equity / badge like ' 100%' or ' 100'
        s = re.sub(r'\s+\(?\d+\%?\)?$', '', s)
        # Strip non-alphanumeric punctuation at boundaries
        s = re.sub(r'^[^\w]+|[^\w]+$', '', s).strip()
        s = re.sub(r'[^\x20-\x7E]', '', s).strip()
        if len(s) < 2:
            return None
        # Must contain at least one alphabetic letter (usernames cannot be pure numbers or chance %)
        if not re.search(r'[a-zA-Z]', s):
            return None
        sl = s.lower()
        bad_words = ['take', 'seat', 'check', 'call', 'bet', 'raise', 'allin', 'all-in', 'fold', 'win', 'muck', 'sitting', 'away', 'wait', 'time']
        if any(bw == sl or sl.startswith(bw + ' ') or sl.endswith(' ' + bw) for bw in bad_words):
            return None
        if sl in ['lwin*', 'win*', '..', '--']:
            return None
        return s

    def _analyze_seat(self, img: Image.Image, seat_cfg: Dict[str, Any], words: List[Tuple]) -> PlayerSeat:
        w, h = img.size
        sid = seat_cfg["seat_id"]
        sname = seat_cfg["name"]
        bx1, by1, bx2, by2 = seat_cfg["box"]
        nb = seat_cfg["name_box"]
        sb = seat_cfg["stack_box"]

        seat = PlayerSeat(seat_id=sid, name=sname)
        # Note: VPIP recognition explicitly omitted per user directive
        seat.vpip = None

        # 1. Check Words in this Seat Box (excluding any bet tokens to prevent stack/username pollution)
        seat_words = []
        for nx, ny, nw, nh, text in words:
            cx = nx + nw / 2.0
            cy = ny + nh / 2.0
            # Exclude tokens situated inside any seat's bet box
            if any(s["bet_box"][0] <= cx <= s["bet_box"][2] and s["bet_box"][1] <= cy <= s["bet_box"][3] for s in SEATS_8MAX):
                continue
            if (bx1 - 0.02) <= cx <= (bx2 + 0.02) and (by1 - 0.02) <= cy <= (by2 + 0.02):
                is_cyan = self._is_cyan_token(img, nx, ny, nw, nh)
                seat_words.append((cx, cy, nw, nh, text, is_cyan))

        all_text_lower = " ".join(t[4].lower() for t in seat_words)

        # Check empty seat
        if ("take" in all_text_lower and "seat" in all_text_lower) or ("take" in all_text_lower and not seat_words):
            seat.is_occupied = False
            self._seat_usernames.pop(sid, None)
            self._seat_stacks.pop(sid, None)
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
        for _, _, _, _, text, _ in seat_words:
            tl = text.lower().replace("-", "")
            for act in action_keywords:
                if act in tl:
                    seat.action = act.capitalize()
                    break
            if seat.action:
                break

        # 4. Stack Size (Neon Cyan token has 1st priority; fallback to inner crop bypassing halo)
        stack_val = None
        cyan_words = [t for t in seat_words if t[5]]
        for cx, cy, nw, nh, text, _ in cyan_words:
            if not ((sb[0] - 0.03) <= cx <= (sb[2] + 0.03) and (sb[1] - 0.018) <= cy <= (sb[3] + 0.020)):
                continue
            clean = re.sub(r'[^\d\.]', '', text)
            if clean and clean.count('.') <= 1:
                try:
                    val = float(clean)
                    if 0.01 <= val <= 1000000 and val != sid:
                        stack_val = val
                        break
                except ValueError:
                    pass

        # If full-image OCR missed the stack (flickering halo washed out contrast),
        # perform an inner crop on the stack box with inset to physically exclude the outer halo!
        if stack_val is None:
            ic_x1 = int((sb[0] + 0.008) * w)
            ic_x2 = int((sb[2] - 0.008) * w)
            ic_y1 = int((sb[1] + 0.001) * h)
            ic_y2 = int((sb[3] + 0.004) * h)
            if ic_x2 > ic_x1 and ic_y2 > ic_y1:
                s_crop = img.crop((ic_x1, ic_y1, ic_x2, ic_y2))
                s_crop_2x = s_crop.resize((s_crop.width * 2, s_crop.height * 2), Image.LANCZOS)
                s_words = self.run_ocr(s_crop_2x)
                for _, _, _, _, text in s_words:
                    clean = re.sub(r'[^\d\.]', '', text)
                    if clean and clean.count('.') <= 1:
                        try:
                            val = float(clean)
                            if 0.01 <= val <= 1000000 and val != sid:
                                stack_val = val
                                break
                        except ValueError:
                            pass

        if stack_val is not None:
            seat.stack = stack_val
            seat.is_occupied = True
            self._seat_stacks[sid] = stack_val
        elif sid in self._seat_stacks and (seat.is_in_hand or seat.action or sid in self._seat_usernames):
            # Retain confirmed stack across transient halo flicker
            seat.stack = self._seat_stacks[sid]
            seat.is_occupied = True

        # 5. Username Resolution
        # CRITICAL USER DIRECTIVE: Do not update username unless a player changes (seat becomes vacant).
        # In Texas Hold'em (ClubGG), showdown replaces the player name with win chance / equity % (e.g. 100%, 75%).
        # Latching the confirmed username protects it from showdown equity % or transient occlusions.
        if sid in self._seat_usernames and self._seat_usernames[sid]:
            seat.username = self._seat_usernames[sid]
            seat.is_occupied = True
        else:
            # First-time acquisition of username for this seat
            name_tokens = []
            for cx, cy, nw, nh, text, is_cyan in seat_words:
                if is_cyan:
                    continue
                tl = text.lower()
                if any(act in tl for act in action_keywords) or any(ign in tl for ign in ["take", "seat", "sitting", "away", "out"]):
                    continue
                # Skip showdown chance percentages (e.g. 100%, 75%, (100, 100)
                if re.match(r'^\(?\d+\%?\)?$', text.strip()):
                    continue
                m_f = re.search(r'\d+(?:\.\d+)?', text)
                if m_f and seat.stack and abs(float(m_f.group(0)) - seat.stack) < 0.01:
                    continue
                # Confine strictly to name_box (excludes left-side avatar/VPIP)
                if not (nb[0] <= cx <= nb[2] and nb[1] <= cy <= nb[3]):
                    continue
                name_tokens.append((cx, text))

            cand_name = None
            if name_tokens:
                name_tokens.sort(key=lambda x: x[0])
                raw_name = " ".join(t[1] for t in name_tokens)
                cand_name = self._clean_player_username(raw_name)

            # Targeted inner crop fallback only if full OCR found no candidate
            if not cand_name and (seat.is_occupied or seat.is_in_hand or seat.stack is not None):
                in_x1 = int(nb[0] * w)
                in_x2 = int(nb[2] * w)
                in_y1 = int(nb[1] * h)
                in_y2 = int(nb[3] * h)
                if in_x2 > in_x1 and in_y2 > in_y1:
                    n_crop = img.crop((in_x1, in_y1, in_x2, in_y2))
                    n_crop_2x = n_crop.resize((n_crop.width * 2, n_crop.height * 2), Image.LANCZOS)
                    crop_words = self.run_ocr(n_crop_2x)
                    c_tokens = []
                    for _, _, _, _, text in crop_words:
                        tl = text.lower()
                        if any(act in tl for act in action_keywords) or 'take' in tl or 'seat' in tl:
                            continue
                        if re.match(r'^\(?\d+\%?\)?$', text.strip()):
                            continue
                        c_tokens.append(text)
                    if c_tokens:
                        cand_name = self._clean_player_username(" ".join(c_tokens))

            if cand_name:
                seat.username = cand_name
                seat.is_occupied = True
                self._seat_usernames[sid] = cand_name

        if seat.username:
            ul = seat.username.lower()
            if "take" in ul or "seat" in ul:
                seat.is_occupied = False
                seat.username = None
                self._seat_usernames.pop(sid, None)
                self._seat_stacks.pop(sid, None)

        # Check Sitting Out
        if "sitting" in all_text_lower or "away" in all_text_lower:
            seat.is_sitting_out = True

        # Check Bet Amount on Felt
        seat.current_bet = self._extract_bet_amount(words, seat_cfg["bet_box"], img, seat.is_occupied or seat.is_in_hand)

        # Vacancy verification: if seat has no player data at all, mark vacant
        if not seat.username and seat.stack is None and not seat.is_in_hand and not seat.action and not seat.current_bet:
            seat.is_occupied = False
            self._seat_usernames.pop(sid, None)
            self._seat_stacks.pop(sid, None)

        return seat

    def _extract_bet_amount(self, words: List[Tuple], bet_box: Tuple[float, float, float, float],
                            img: Optional[Image.Image] = None, allow_crop_ocr: bool = True) -> Optional[float]:
        """Extracts bet chips and amount on the felt inside bet_box, with split-token handling and fast crop fallback."""
        bx1, by1, bx2, by2 = bet_box
        matching = []
        for nx, ny, nw, nh, text in words:
            cx = nx + nw / 2.0
            cy = ny + nh / 2.0
            if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                matching.append((cx, cy, text))

        val = self._parse_bet_tokens(matching)

        # Fast fallback: If no bet found via full-image OCR, check if targeted crop OCR can find it
        if val is None and allow_crop_ocr and img is not None:
            w, h = img.size
            crop_box = (int(bx1 * w), int(by1 * h), int(bx2 * w), int(by2 * h))
            crop = img.crop(crop_box)
            crop_words = self.run_ocr(crop)
            if crop_words:
                crop_matching = [(t[0] + t[2]/2.0, t[1] + t[3]/2.0, t[4]) for t in crop_words]
                val = self._parse_bet_tokens(crop_matching)

        return val

    def _parse_bet_tokens(self, tokens: List[Tuple]) -> Optional[float]:
        """Parses bet numerical value from a list of (cx, cy, text) tokens."""
        if not tokens:
            return None

        tokens = sorted(tokens, key=lambda t: t[0])
        noise = {"e", ".", "-", ":", "gps&ip", "restriction", "blinds", "run", "it", "multi", "time"}
        raw_texts = [t[2].strip() for t in tokens if t[2].strip().lower() not in noise]
        if not raw_texts:
            return None

        joined = "".join(raw_texts)

        # Handle omitted decimal point on numbers with leading zero, e.g. "050" -> 0.50, "025" -> 0.25
        if re.match(r'^0\d{2}$', joined):
            try:
                return float("0." + joined[1:])
            except ValueError:
                pass

        # Handle split tokens, e.g. ["0", "25"] -> 0.25
        if len(raw_texts) == 2 and raw_texts[0] == "0" and raw_texts[1].isdigit():
            try:
                return float("0." + raw_texts[1])
            except ValueError:
                pass

        # Parse standard floats from joined or individual tokens
        for t in [joined] + raw_texts:
            if re.match(r'^0\d{2}$', t):
                try:
                    return float("0." + t[1:])
                except ValueError:
                    pass
            m = re.search(r'\d+(?:\.\d+)?', t)
            if m:
                try:
                    candidate = float(m.group(0))
                    if 0.01 <= candidate <= 500000:
                        return candidate
                except ValueError:
                    pass

        return None


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
    if state.community_cards:
        print(f" Community Cards  : {' '.join(state.community_cards)}")
    print(f" Total Pot        : {state.total_pot if state.total_pot is not None else 'N/A'}")

    print(f" Dealer Seat      : Seat {state.dealer_seat} (BTN)")
    print(f" Seated Players   : {state.occupied_seats} / {state.total_seats}")
    print(f" Active In Hand   : {state.active_players_in_hand}")
    print(f" Waiting Queue    : {state.waiting_players if state.waiting_players is not None else 'N/A'}")
    print(f" Analysis Time    : {elapsed:.2f}s")
    print("-" * 85)
    print(f" {'Seat':<6} {'Pos':<8} {'Username':<15} {'Stack':<10} {'Bet':<10} {'VPIP':<8} {'In Hand':<10} {'Action':<10}")
    print("-" * 85)
    for s in state.seats:
        if s.is_occupied:
            in_hand_str = "YES" if s.is_in_hand else "No (fold)"
            vpip_str = f"{s.vpip}%" if s.vpip is not None else "-"
            stack_str = f"{s.stack:.2f}" if s.stack is not None else "-"
            bet_str = f"{s.current_bet:.2f}" if s.current_bet is not None else "-"
            action_str = s.action or "-"
            pos_str = s.position or "-"
            print(f" {s.seat_id:<6} {pos_str:<8} {s.username or 'Unknown':<15} {stack_str:<10} {bet_str:<10} {vpip_str:<8} {in_hand_str:<10} {action_str:<10}")
        else:
            print(f" {s.seat_id:<6} {'-':<8} {'[EMPTY SEAT]':<15} {'-':<10} {'-':<10} {'-':<8} {'-':<10} {'Take Seat':<10}")
    print("=" * 85)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(out_data, f, indent=2 if args.pretty else None)
        print(f"[+] Saved structured table state to: {args.output}")

if __name__ == "__main__":
    main()
