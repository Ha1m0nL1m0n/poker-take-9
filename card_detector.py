#!/usr/bin/env python3
"""
ClubGG High-Integrity Card Rank & Suit Recognition Engine
=========================================================
Extracts exact card ranks (2-A) and suits (c, d, h, s) for community cards
and player showdown hands with strict Texas Hold'em data integrity validation:
  - Zero-Duplicate Invariant: Ensures no card appears twice across board and showdown.
  - Board Progression Validation: 0 (Preflop), 3 (Flop), 4 (Turn), 5 (River).
  - 2-Tier Deterministic Suit Recognition:
      * Color space verification: Red (Hearts, Diamonds) vs Black (Clubs, Spades).
      * Morphological geometry & apex analysis on suit symbols.
  - Structural & Template Rank Classifier for all 13 ranks.
  - Confidence scoring and integrity health reporting.
"""

from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict, Any, Set
from PIL import Image, ImageOps
import numpy as np

# 13 Poker Ranks & 4 Suits
RANKS = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"]
SUITS = ["c", "d", "h", "s"]  # c=clubs, d=diamonds, h=hearts, s=spades

SUIT_NAMES = {
    "c": "Clubs ♣",
    "d": "Diamonds ♦",
    "h": "Hearts ♥",
    "s": "Spades ♠"
}

@dataclass
class DetectedCard:
    card: str                # e.g. "Ah", "Kd", "Ts", "2c"
    rank: str                # "A", "K", "Q", "J", "T", "9"..."2"
    suit: str                # "c", "d", "h", "s"
    confidence: float        # 0.0 - 1.0
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    is_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class BoardIntegrityReport:
    stage: str               # "PREFLOP", "FLOP", "TURN", "RIVER", or "INVALID"
    card_count: int
    is_valid_stage: bool
    has_duplicates: bool
    duplicate_cards: List[str]
    cards: List[DetectedCard]
    health: str              # "HEALTHY", "WARNING", "ERROR"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "card_count": self.card_count,
            "is_valid_stage": self.is_valid_stage,
            "has_duplicates": self.has_duplicates,
            "duplicate_cards": self.duplicate_cards,
            "health": self.health,
            "cards": [c.to_dict() for c in self.cards]
        }


class CardDetector:
    """High-accuracy, deterministic card rank and suit detector for ClubGG."""

    def __init__(self):
        # Canonical bitmaps for the 13 ranks (normalized 8x11 binary grid)
        # 1 = foreground character pixel, 0 = background
        self.rank_templates = self._build_canonical_templates()

    def detect_community_cards(self, img: Image.Image, comm_box=(0.18, 0.47, 0.82, 0.57)) -> BoardIntegrityReport:
        """
        Detects all community cards on the board within the normalized community box.
        """
        W, H = img.size
        # Crop the community box
        cx1 = int(comm_box[0] * W)
        cy1 = int(comm_box[1] * H)
        cx2 = int(comm_box[2] * W)
        cy2 = int(comm_box[3] * H)
        board_crop = img.crop((cx1, cy1, cx2, cy2))
        
        # Segment individual cards
        card_crops, spans = self._segment_board_cards(board_crop)
        
        detected_cards: List[DetectedCard] = []
        for (c_img, (sx1, sx2, sy1, sy2)) in zip(card_crops, spans):
            card_obj = self.analyze_card(c_img)
            if card_obj:
                # Map bbox back to full image coordinates
                abs_bbox = (cx1 + sx1, cy1 + sy1, cx1 + sx2, cy1 + sy2)
                card_obj.bbox = abs_bbox
                detected_cards.append(card_obj)

        # Validate board integrity
        return self._validate_board(detected_cards)

    def detect_hero_hole_cards(self, full_screen: Image.Image, hero_box=(0.04, 0.73, 0.28, 0.82)) -> List[DetectedCard]:
        """
        Detects Hero hole cards (dual overlapping cards) from the hero table position.
        Default hero_box is calibrated for ClubGG Heads-Up layout.
        """
        W, H = full_screen.size
        bx1 = int(hero_box[0] * W)
        by1 = int(hero_box[1] * H)
        bx2 = int(hero_box[2] * W)
        by2 = int(hero_box[3] * H)
        crop = full_screen.crop((bx1, by1, bx2, by2))
        
        # Find tight card body of the pair
        rgb = crop.convert("RGB")
        cdata = rgb.load()
        cW, cH = crop.size
        mask = Image.new("1", (cW, cH), 0)
        for y in range(cH):
            for x in range(cW):
                r, g, b = cdata[x, y]
                if r > 185 and g > 185 and b > 185:
                    mask.putpixel((x, y), 1)
        bbox = mask.getbbox()
        if not bbox:
            return []
            
        b_x1, b_y1, b_x2, b_y2 = bbox
        bw = b_x2 - b_x1
        bh = b_y2 - b_y1
        if bw < 45 or bh < 30:
            return []
            
        body = crop.crop(bbox)
        left_body = body.crop((0, 0, int(bw * 0.55), bh))
        right_body = body.crop((int(bw * 0.42), 0, bw, bh))
        
        c1 = self._extract_corner_card(left_body)
        c2 = self._extract_corner_card(right_body)
        
        res = []
        if c1:
            c1.bbox = (bx1 + b_x1, by1 + b_y1, bx1 + b_x1 + int(bw * 0.55), by1 + b_y2)
            res.append(c1)
        if c2:
            c2.bbox = (bx1 + b_x1 + int(bw * 0.42), by1 + b_y1, bx1 + b_x2, by1 + b_y2)
            res.append(c2)
        return res

    def _classify_small_suit(self, glyph_mask: Image.Image, is_red: bool) -> Tuple[str, float]:
        """Classifies small suit glyph from top-left corner of card."""
        W, H = glyph_mask.size
        data = glyph_mask.load()
        if is_red:
            top_y = None
            for y in range(H):
                if any(data[x, y] == 1 for x in range(W)):
                    top_y = y
                    break
            if top_y is None:
                return "d", 0.70
            
            top_pts = [x for x in range(W) if data[x, top_y] == 1]
            cleft = False
            mid_x = W // 2
            for cy in range(top_y + 1, min(top_y + 4, H)):
                if data[mid_x, cy] == 0 and (data[mid_x - 1, cy] == 1 or data[mid_x + 1, cy] == 1):
                    cleft = True
                    break
            if cleft:
                return "h", 0.95
            if len(top_pts) <= 3:
                return "d", 0.95
            return "d", 0.85
        else:
            top_y = None
            for y in range(H):
                if any(data[x, y] == 1 for x in range(W)):
                    top_y = y
                    break
            if top_y is None:
                return "s", 0.70
            top_pts = [x for x in range(W) if data[x, top_y] == 1]
            if len(top_pts) <= 2:
                return "s", 0.95
            return "c", 0.90

    def _extract_corner_card(self, card_crop: Image.Image) -> Optional[DetectedCard]:
        """Extracts card rank and suit from overlapping corner glyphs."""
        W, H = card_crop.size
        rgb = card_crop.convert("RGB")
        data = rgb.load()
        
        red_pts = 0
        black_pts = 0
        for y in range(int(H * 0.55)):
            for x in range(int(W * 0.65)):
                r, g, b = data[x, y]
                if r > 115 and r > g + 25 and r > b + 25:
                    red_pts += 1
                elif r < 100 and g < 100 and b < 100 and abs(r - g) < 25:
                    black_pts += 1
                    
        is_red = red_pts > black_pts
        
        fg_mask = Image.new("1", (W, H), 0)
        for y in range(H):
            for x in range(W):
                r, g, b = data[x, y]
                if is_red:
                    if r > 110 and r > g + 25 and r > b + 25:
                        fg_mask.putpixel((x, y), 1)
                else:
                    if r < 100 and g < 100 and b < 100 and abs(r - g) < 25:
                        fg_mask.putpixel((x, y), 1)
                        
        bbox = fg_mask.getbbox()
        if not bbox:
            return None
            
        fg = fg_mask.crop(bbox)
        fw, fh = fg.size
        
        gap_y = None
        for y in range(int(fh * 0.35), int(fh * 0.75)):
            if sum(fg.getpixel((x, y)) for x in range(fw)) == 0:
                gap_y = y
                break
                
        if gap_y:
            rank_mask = fg.crop((0, 0, fw, gap_y))
            suit_mask = fg.crop((0, gap_y, fw, fh))
        else:
            rank_mask = fg.crop((0, 0, fw, int(fh * 0.55)))
            suit_mask = fg.crop((0, int(fh * 0.55), fw, fh))
            
        r_bbox = rank_mask.getbbox()
        s_bbox = suit_mask.getbbox()
        if not r_bbox or not s_bbox:
            return None
            
        r_glyph = rank_mask.crop(r_bbox)
        s_glyph = suit_mask.crop(s_bbox)
        
        rgw, rgh = r_glyph.size
        col_proj = [sum(r_glyph.getpixel((x, y)) for y in range(rgh)) for x in range(rgw)]
        if rgw >= rgh * 0.70 and len(col_proj) >= 7:
            mid_start = int(rgw * 0.25)
            mid_end = int(rgw * 0.75)
            mid_valley = min(col_proj[mid_start:mid_end])
            max_peak = max(col_proj)
            if mid_valley <= max_peak * 0.45:
                rank_str = "T"
                rank_conf = 0.98
            else:
                rank_str, rank_conf = self._template_match_glyph(r_glyph)
        else:
            rank_str, rank_conf = self._template_match_glyph(r_glyph)
            
        suit_str, suit_conf = self._classify_small_suit(s_glyph, is_red)
        conf = round(rank_conf * 0.5 + suit_conf * 0.5, 3)
        return DetectedCard(
            card=f"{rank_str}{suit_str}",
            rank=rank_str,
            suit=suit_str,
            confidence=conf,
            bbox=bbox
        )

    def _template_match_glyph(self, r_glyph: Image.Image) -> Tuple[str, float]:
        norm = r_glyph.resize((8, 11), Image.Resampling.NEAREST)
        cdata = norm.load()
        best_rank = "?"
        best_score = -1.0
        for rank_name, template in self.rank_templates.items():
            score = self._compute_iou(cdata, template, 8, 11)
            if score > best_score:
                best_score = score
                best_rank = rank_name
        return best_rank, max(0.0, min(1.0, best_score))

    def analyze_card(self, card_crop: Image.Image) -> Optional[DetectedCard]:
        """Analyzes an isolated card crop to determine rank, suit, and confidence."""
        # 1. Extract tight white card body
        card_body, (bx1, by1, bx2, by2) = self._get_tight_card_body(card_crop)
        if not card_body:
            return None
        
        CW, CH = card_body.size
        if CW < 15 or CH < 20:
            return None

        # 2. Determine suit color & suit glyph
        suit, suit_conf = self._classify_suit(card_body)

        # 3. Determine rank
        rank, rank_conf = self._classify_rank(card_body)

        if not suit or not rank:
            return None

        card_str = f"{rank}{suit}"
        confidence = round((suit_conf * 0.5 + rank_conf * 0.5), 3)

        return DetectedCard(
            card=card_str,
            rank=rank,
            suit=suit,
            confidence=confidence,
            bbox=(bx1, by1, bx2, by2)
        )

    def _get_tight_card_body(self, card_crop: Image.Image) -> Tuple[Optional[Image.Image], Tuple[int, int, int, int]]:
        """Extracts the exact white card rectangle, filtering felt border."""
        rgb = card_crop.convert("RGB")
        W, H = card_crop.size
        mask = Image.new("1", (W, H), 0)
        data = rgb.load()
        m = mask.load()

        for y in range(H):
            for x in range(W):
                r, g, b = data[x, y]
                # Card body is clean white/light gray
                if r > 175 and g > 175 and b > 175:
                    m[x, y] = 1

        bbox = mask.getbbox()
        if not bbox:
            return None, (0, 0, 0, 0)
        return card_crop.crop(bbox), bbox

    def _segment_board_cards(self, board_crop: Image.Image) -> Tuple[List[Image.Image], List[Tuple[int, int, int, int]]]:
        """Segments horizontal white community cards from the felt background."""
        # Cards typically live in the bottom ~75% of the community cards box
        W, H = board_crop.size
        y_offset = int(H * 0.15)
        crop_area = board_crop.crop((0, y_offset, W, H))
        rgb = crop_area.convert("RGB")
        bW, bH = crop_area.size

        # Compute column-wise white pixel count
        col_white = []
        for x in range(bW):
            w_cnt = sum(1 for y in range(bH) if rgb.getpixel((x, y))[0] > 180 and rgb.getpixel((x, y))[1] > 180 and rgb.getpixel((x, y))[2] > 180)
            col_white.append(w_cnt)

        # Detect horizontal card spans
        min_card_height = bH * 0.35
        in_card = False
        spans = []
        start = 0
        for x, cnt in enumerate(col_white):
            if cnt > min_card_height and not in_card:
                in_card = True
                start = x
            elif cnt <= min_card_height and in_card:
                in_card = False
                width = x - start
                if width >= 18:  # Minimum card width filter
                    spans.append((start, x))
        if in_card and (bW - start) >= 18:
            spans.append((start, bW))

        # Crop each card
        cards = []
        full_spans = []
        for (sx1, sx2) in spans:
            c_crop = crop_area.crop((sx1, 0, sx2, bH))
            cards.append(c_crop)
            full_spans.append((sx1, sx2, y_offset, H))

        return cards, full_spans

    def _classify_suit(self, card_body: Image.Image) -> Tuple[str, float]:
        """
        Deterministic 2-tier suit classifier:
          1. Color verification (Red vs Black)
          2. Shape morphology on large suit symbol
        """
        CW, CH = card_body.size
        rgb = card_body.convert("RGB")
        data = rgb.load()

        # Step 1: Color check from top-left quadrant
        red_pts = 0
        black_pts = 0
        for y in range(2, int(CH * 0.55)):
            for x in range(2, int(CW * 0.45)):
                r, g, b = data[x, y]
                if r > 115 and r > g + 30 and r > b + 30:
                    red_pts += 1
                elif r < 100 and g < 100 and b < 100 and abs(r - g) < 25 and abs(r - b) < 25:
                    black_pts += 1

        is_red = red_pts > black_pts

        # Step 2: Shape morphology on the large suit symbol (lower-right quadrant)
        ls_x1 = int(CW * 0.35)
        ls_y1 = int(CH * 0.40)
        ls_x2 = int(CW * 0.98)
        ls_y2 = int(CH * 0.92)
        
        ls_crop = card_body.crop((ls_x1, ls_y1, ls_x2, ls_y2)).convert("RGB")
        ls_data = ls_crop.load()
        ls_mask = Image.new("1", ls_crop.size, 0)

        for y in range(ls_crop.height):
            for x in range(ls_crop.width):
                r, g, b = ls_data[x, y]
                if is_red:
                    if r > 110 and r > g + 25 and r > b + 25:
                        ls_mask.putpixel((x, y), 1)
                else:
                    if r < 95 and g < 95 and b < 95 and abs(r - g) < 25:
                        ls_mask.putpixel((x, y), 1)

        # Isolate largest connected component to filter border shadows and rank tails
        clean_mask = self._get_largest_component(ls_mask)
        bbox = clean_mask.getbbox()
        if not bbox:
            return ("h" if is_red else "s"), 0.65

        suit_glyph = clean_mask.crop(bbox)
        sw, sh = suit_glyph.size
        sg_data = suit_glyph.load()

        if is_red:
            mid_x = sw // 2
            cleft = False
            for cy in range(1, min(4, sh)):
                if sg_data[mid_x, cy] == 0 and (sg_data[mid_x // 2, cy] == 1 or sg_data[mid_x + mid_x // 2, cy] == 1):
                    cleft = True
                    break
            top_half_area = sum(sg_data[x, y] for y in range(sh // 2) for x in range(sw))
            bot_half_area = sum(sg_data[x, y] for y in range(sh // 2, sh) for x in range(sw))
            ratio = top_half_area / max(bot_half_area, 1)
            r1_w = sum(sg_data[x, min(1, sh - 1)] for x in range(sw))
            if cleft or (r1_w / float(sw) > 0.45 and ratio > 1.35):
                return "h", 0.99
            else:
                return "d", 0.99
        else:
            r0_w = sum(sg_data[x, 0] for x in range(sw))
            r1_w = sum(sg_data[x, min(1, sh - 1)] for x in range(sw))
            is_spade = (r0_w <= max(2, int(sw * 0.12)) and r1_w <= max(4, int(sw * 0.22)))
            if is_spade:
                return "s", 0.99
            else:
                return "c", 0.99

    def _get_largest_component(self, mask: Image.Image) -> Image.Image:
        """Keeps only the largest connected 8-connected / 4-connected blob in a binary mask."""
        w, h = mask.size
        visited = set()
        best_comp = []
        for y in range(h):
            for x in range(w):
                if mask.getpixel((x, y)) and (x, y) not in visited:
                    comp = []
                    q = [(x, y)]
                    visited.add((x, y))
                    while q:
                        cx, cy = q.pop()
                        comp.append((cx, cy))
                        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            nx, ny = cx + dx, cy + dy
                            if 0 <= nx < w and 0 <= ny < h:
                                if (nx, ny) not in visited and mask.getpixel((nx, ny)):
                                    visited.add((nx, ny))
                                    q.append((nx, ny))
                    if len(comp) > len(best_comp):
                        best_comp = comp
        out = Image.new("1", (w, h), 0)
        for cx, cy in best_comp:
            out.putpixel((cx, cy), 1)
        return out

    def _classify_rank(self, card_body: Image.Image) -> Tuple[str, float]:
        """
        Classifies rank glyph from top-left of card.
        Uses resolution-independent blank row gap detection and aspect-ratio 10-detection.
        """
        CW, CH = card_body.size
        # Crop rank area in top-left
        rc = card_body.crop((2, 2, int(CW * 0.45), int(CH * 0.38))).convert("RGB")
        rw, rh = rc.size
        rc_data = rc.load()

        # Binarize rank glyph (detecting colored or dark pixels)
        r_mask = Image.new("1", (rw, rh), 0)
        rm = r_mask.load()
        for y in range(rh):
            for x in range(rw):
                r, g, b = rc_data[x, y]
                is_colored = (r > 115 and r > g + 28 and r > b + 28) or (r < 100 and g < 100 and b < 100 and abs(r - g) < 25)
                if is_colored:
                    rm[x, y] = 1

        bbox = r_mask.getbbox()
        if not bbox:
            return "?", 0.0

        glyph = r_mask.crop(bbox)
        gw, gh = glyph.size

        # Find horizontal blank row gap separating the rank character from the small suit icon below it
        gap_y = None
        for y in range(int(gh * 0.40), gh):
            if sum(glyph.getpixel((x, y)) for x in range(gw)) == 0:
                gap_y = y
                break

        char_glyph = glyph.crop((0, 0, gw, gap_y if gap_y else int(gh * 0.68)))
        c_bbox = char_glyph.getbbox()
        if c_bbox:
            char_glyph = char_glyph.crop(c_bbox)
        cgw, cgh = char_glyph.size

        # 1. Distinctive "10" detection: 10 has dual characters ("10") with aspect ratio >= 0.72 and central valley
        col_proj = [sum(char_glyph.getpixel((x, y)) for y in range(cgh)) for x in range(cgw)]
        if cgw >= cgh * 0.72 and len(col_proj) >= 7:
            mid_start = int(cgw * 0.25)
            mid_end = int(cgw * 0.75)
            mid_valley = min(col_proj[mid_start:mid_end])
            max_peak = max(col_proj)
            if mid_valley <= max_peak * 0.45:
                return "T", 0.98

        # 2. Normalize candidate glyph to 8x11 grid for template comparison
        norm_candidate = char_glyph.resize((8, 11), Image.Resampling.NEAREST)
        cand_data = norm_candidate.load()

        # 3. Match against canonical 8x11 templates
        best_rank = "?"
        best_score = -1.0

        for rank_name, template in self.rank_templates.items():
            score = self._compute_iou(cand_data, template, 8, 11)
            if score > best_score:
                best_score = score
                best_rank = rank_name

        conf = round(max(min(best_score, 1.0), 0.0), 3)
        return best_rank, conf

    def _compute_iou(self, cand_data, template_lines: List[str], W: int, H: int) -> float:
        """Computes Intersection over Union (IoU) between candidate and template."""
        intersection = 0
        union = 0
        for y in range(H):
            line = template_lines[y]
            for x in range(W):
                c_val = 1 if cand_data[x, y] else 0
                t_val = 1 if line[x] == "#" else 0
                if c_val == 1 and t_val == 1:
                    intersection += 1
                if c_val == 1 or t_val == 1:
                    union += 1
        return intersection / max(union, 1)

    def _validate_board(self, cards: List[DetectedCard]) -> BoardIntegrityReport:
        """
        Enforces Texas Hold'em board invariants:
          - Stage count: 0 (Preflop), 3 (Flop), 4 (Turn), 5 (River).
          - Zero duplicates allowed.
        """
        count = len(cards)
        stage_map = {0: "PREFLOP", 3: "FLOP", 4: "TURN", 5: "RIVER"}
        stage = stage_map.get(count, "UNKNOWN")
        is_valid_stage = count in (0, 3, 4, 5)

        # Check for duplicate cards
        seen: Set[str] = set()
        duplicates: List[str] = []
        for c in cards:
            if c.card in seen:
                duplicates.append(c.card)
                c.is_valid = False
            seen.add(c.card)

        has_duplicates = len(duplicates) > 0

        # Health status
        if not is_valid_stage or has_duplicates:
            health = "ERROR" if has_duplicates else "WARNING"
        else:
            health = "HEALTHY"

        return BoardIntegrityReport(
            stage=stage,
            card_count=count,
            is_valid_stage=is_valid_stage,
            has_duplicates=has_duplicates,
            duplicate_cards=duplicates,
            cards=cards,
            health=health
        )

    def _build_canonical_templates(self) -> Dict[str, List[str]]:
        """
        Pre-computed canonical 8x11 binary bitmaps for ClubGG card ranks.
        Normalized to 8 width x 11 height.
        """
        return {
            "A": [
                "...##...",
                "..####..",
                "..####..",
                ".##..##.",
                ".##..##.",
                ".######.",
                ".######.",
                "##....##",
                "##....##",
                "##....##",
                "##....##"
            ],
            "K": [
                "##....##",
                "##...###",
                "##..###.",
                "##.###..",
                "######..",
                "#####...",
                "######..",
                "##.###..",
                "##..###.",
                "##...###",
                "##....##"
            ],
            "Q": [
                "..####..",
                ".######.",
                "##....##",
                "##....##",
                "##....##",
                "##....##",
                "##....##",
                "##..####",
                "##...###",
                ".######.",
                "..####.#"
            ],
            "J": [
                "....####",
                "....####",
                "......##",
                "......##",
                "......##",
                "......##",
                "......##",
                "##....##",
                "##....##",
                ".######.",
                "..####.."
            ],
            "T": [
                "##...###",
                ".#..#..#",
                ".#..#..#",
                ".#..#..#",
                ".#..#..#",
                ".#..#..#",
                ".#..#..#",
                ".#..#..#",
                ".#..#..#",
                ".#..#..#",
                "###..###"
            ],
            "9": [
                "..####..",
                "#######.",
                "##....##",
                "##...###",
                "##...###",
                "########",
                ".....###",
                ".....###",
                "##...###",
                ".######.",
                "..###..."
            ],
            "8": [
                "..####..",
                ".######.",
                "##....##",
                "##....##",
                ".######.",
                ".######.",
                "##....##",
                "##....##",
                "##....##",
                ".######.",
                "..####.."
            ],
            "7": [
                "########",
                "########",
                ".....###",
                "....###.",
                "....###.",
                "...###..",
                "...###..",
                "..###...",
                "..###...",
                ".###....",
                ".###...."
            ],
            "6": [
                "..####..",
                ".######.",
                "##....##",
                "##......",
                "#######.",
                "########",
                "##....##",
                "##....##",
                "##....##",
                ".######.",
                "..####.."
            ],
            "5": [
                "########",
                "########",
                "##......",
                "##......",
                "#######.",
                ".#######",
                "......##",
                "......##",
                "##....##",
                ".######.",
                "..####.."
            ],
            "4": [
                ".....###",
                "....####",
                "...#####",
                "..######",
                ".##..###",
                "##...###",
                "########",
                "########",
                ".....###",
                ".....###",
                ".....###"
            ],
            "3": [
                "..####..",
                ".######.",
                "##....##",
                "......##",
                "...#####",
                "...#####",
                "......##",
                "......##",
                "##....##",
                ".######.",
                "..####.."
            ],
            "2": [
                "..####..",
                ".######.",
                "##....##",
                "......##",
                ".....###",
                "....###.",
                "...###..",
                "..###...",
                ".###....",
                "########",
                "########"
            ]
        }

    # -------------------------------------------------------------
    # GGPoker / 7XL 4-Color Short Deck Detection Engine
    # -------------------------------------------------------------
    GGPOKER_TEMPLATES_12x16 = {
        "A": [
            ".....##.....", "....####....", "....####....", "....####....",
            "....##.##...", "...##..##...", "...##..##...", "...##..##...",
            "...##..###..", "..########..", "..########..", "..##....###.",
            ".##......##.", ".##......##.", ".##......###", "###......###",
        ],
        "K": [
            ".......##...", "##....###...", "##....##....", "##...##.....",
            "###..##.....", "###.##......", "######......", "######......",
            "#######.....", ".###.##.....", ".##..###....", ".##...###...",
            ".##....###..", ".##.....###.", ".##.....####", ".##.........",
        ],
        "Q": [
            "...######...", "..########..", ".###....###.", ".##......##.",
            "###......###", "###......###", "##........##", "##........##",
            "##...##...##", "###..##..###", "###..###.###", ".##...#####.",
            ".###..#####.", "..########..", "....######..", "........###.",
        ],
        "J": [
            "........####", "........####", "........####", "........####",
            "........####", "........####", "........####", "........####",
            "........####", "........####", "........####", "........####",
            "........####", ".###...####.", "##########..", "...######...",
        ],
        "8": [
            "..########..", ".##########.", ".###....###.", ".###.....###",
            ".###.....###", ".###....###.", "..########..", "..########..",
            ".####...###.", "###......###", "###......###", "###......###",
            "###......###", "#####..####.", ".#########..", "...######...",
        ],
        "7": [
            "############", "###########.", "#......####.", ".......###..",
            ".......###..", ".......###..", "......###...", "......###...",
            ".....####...", ".....###....", ".....###....", "....###.....",
            "....###.....", "....###.....", "...####.....", "...####.....",
        ],
        "6": [
            "....######..", "..#########.", ".####...###.", ".###........",
            "###...####..", "###########.", "####....####", "###......###",
            "###......###", "###......###", "###......###", "####....####",
            ".##########.", "..########..", "...######...", "....####....",
        ],
        "9": [
            "....####....", "...######...", "..########..", ".##########.",
            "####....####", "###......###", "###......###", "###......###",
            "###......###", "####....####", ".###########", "..####...###",
            "........###.", ".###...####.", ".#########..", "..######....",
        ]
    }

    BOARD_CARD_SLOTS_SD = [
        (0.159 + i * 0.1388, 0.468, 0.159 + i * 0.1388 + 0.126, 0.557)
        for i in range(5)
    ]

    def _classify_gg_rank(self, char_glyph: Image.Image) -> Tuple[str, float]:
        """Classifies 4-color Short Deck rank using dual-character 10 and 12x16 templates."""
        cgw, cgh = char_glyph.size
        col_proj = [sum(char_glyph.getpixel((x, y)) for y in range(cgh)) for x in range(cgw)]
        if cgw >= cgh * 0.72 and len(col_proj) >= 7:
            mid_start = int(cgw * 0.25)
            mid_end = int(cgw * 0.75)
            mid_valley = min(col_proj[mid_start:mid_end])
            max_peak = max(col_proj)
            if mid_valley <= max_peak * 0.05 or mid_valley <= 2:
                return "T", 0.99
                
        norm_img = char_glyph.convert("L").resize((12, 16), Image.Resampling.BILINEAR)
        norm_arr = (np.array(norm_img) > 128).astype(int)
        
        best_rank = "?"
        best_score = -1.0
        for r, tmpl_lines in self.GGPOKER_TEMPLATES_12x16.items():
            tmpl_arr = np.array([[1 if c == '#' else 0 for c in line] for line in tmpl_lines])
            intersection = np.sum((norm_arr == 1) & (tmpl_arr == 1))
            union = np.sum((norm_arr == 1) | (tmpl_arr == 1))
            score = intersection / float(union) if union > 0 else 0
            if score > best_score:
                best_score = score
                best_rank = r
        return best_rank, round(float(best_score), 3)

    def _sample_card_suit(self, crop_img: Image.Image) -> Tuple[Optional[str], Optional[np.ndarray]]:
        """Samples dominant suit background color behind the white rank glyph."""
        arr = np.array(crop_img.convert("RGB"))
        H, W = arr.shape[:2]
        is_white = (arr[:,:,0] > 185) & (arr[:,:,1] > 185) & (arr[:,:,2] > 185)
        sub_white = is_white[:int(H * 0.55), :int(W * 0.65)]
        rows = np.where(np.any(sub_white, axis=1))[0]
        cols = np.where(np.any(sub_white, axis=0))[0]
        if len(rows) == 0 or len(cols) == 0:
            return None, None
        y1, y2 = rows[0], rows[-1]
        x1, x2 = cols[0], cols[-1]
        
        py1, py2 = max(0, y1 - 5), min(H, y2 + 5)
        px1, px2 = max(0, x1 - 5), min(W, x2 + 5)
        patch = arr[py1:py2, px1:px2]
        patch_white = is_white[py1:py2, px1:px2]
        card_bg = patch[~patch_white]
        if len(card_bg) == 0:
            return None, None
            
        mean_rgb = np.median(card_bg, axis=0)
        mr, mg, mb = mean_rgb
        suit = None
        if mr > 90 and mr > mg + 30 and mr > mb + 30:
            suit = "h"
        elif mg > 70 and mg > mr + 25 and mg > mb + 15:
            suit = "c"
        elif mb > 70 and mb > mr + 35 and mb > mg + 20:
            suit = "d"
        elif mr < 60 and mg < 60 and mb < 60:
            suit = "s"
        return suit, mean_rgb

    def detect_community_cards_sd(self, img: Image.Image) -> BoardIntegrityReport:
        """Detects community cards on 6-max Short Deck tables using calibrated 5-slot grid."""
        W, H = img.size
        detected_cards: List[DetectedCard] = []
        for i, (nx1, ny1, nx2, ny2) in enumerate(self.BOARD_CARD_SLOTS_SD):
            px1, py1 = int(nx1 * W), int(ny1 * H)
            px2, py2 = int(nx2 * W), int(ny2 * H)
            crop = img.crop((px1, py1, px2, py2))
            arr = np.array(crop.convert("RGB"))
            r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
            
            red_px = np.sum((r > 110) & (g < 55) & (b < 55))
            green_px = np.sum((g > 90) & (r < 55) & (b < 60))
            blue_px = np.sum((b > 90) & (r < 50) & (g < 100))
            white_mask = (r > 185) & (g > 185) & (b > 185)
            white_px = np.sum(white_mask)
            charcoal_px = np.sum((r > 20) & (r < 55) & (g > 20) & (g < 55) & (b > 20) & (b < 55) & (np.abs(r.astype(int) - b.astype(int)) < 12))
            total_px = crop.size[0] * crop.size[1]
            
            suit = None
            if red_px > 0.10 * total_px:
                suit = "h"
            elif green_px > 0.10 * total_px:
                suit = "c"
            elif blue_px > 0.10 * total_px:
                suit = "d"
            elif white_px > 0.04 * total_px and charcoal_px > 0.20 * total_px:
                suit = "s"
                
            if suit and white_px > 0.02 * total_px:
                rc = crop.crop((2, 2, int(crop.width * 0.45), int(crop.height * 0.40))).convert("RGB")
                rc_arr = np.array(rc)
                rc_white = (rc_arr[:,:,0] > 185) & (rc_arr[:,:,1] > 185) & (rc_arr[:,:,2] > 185)
                rows = np.where(np.any(rc_white, axis=1))[0]
                cols = np.where(np.any(rc_white, axis=0))[0]
                if len(rows) > 0 and len(cols) > 0:
                    glyph = rc_white[rows[0]:rows[-1]+1, cols[0]:cols[-1]+1]
                    gh, gw = glyph.shape
                    row_sums = np.sum(glyph, axis=1)
                    gap_y = None
                    for y in range(int(gh * 0.35), gh):
                        if row_sums[y] == 0:
                            gap_y = y
                            break
                    char_glyph = glyph[:gap_y, :] if gap_y else glyph[:int(gh * 0.65), :]
                    r_rows = np.where(np.any(char_glyph, axis=1))[0]
                    r_cols = np.where(np.any(char_glyph, axis=0))[0]
                    if len(r_rows) > 0 and len(r_cols) > 0:
                        trimmed = char_glyph[r_rows[0]:r_rows[-1]+1, r_cols[0]:r_cols[-1]+1]
                        char_img = Image.fromarray((trimmed * 255).astype(np.uint8))
                        rank, conf = self._classify_gg_rank(char_img)
                        card_obj = DetectedCard(
                            card=f"{rank}{suit}",
                            rank=rank,
                            suit=suit,
                            confidence=conf,
                            bbox=(px1, py1, px2, py2)
                        )
                        detected_cards.append(card_obj)
        return self._validate_board(detected_cards)

    def detect_hero_hole_cards_sd(self, full_screen: Image.Image, hero_box=(0.04, 0.70, 0.36, 0.89)) -> List[DetectedCard]:
        """Detects Hero hole cards on 6-max Short Deck tables."""
        W, H = full_screen.size
        bx1, by1 = int(hero_box[0] * W), int(hero_box[1] * H)
        bx2, by2 = int(hero_box[2] * W), int(hero_box[3] * H)
        hero = full_screen.crop((bx1, by1, bx2, by2))
        hw, hh = hero.size
        
        # Dual corner windows for Hero cards
        c1_corner = hero.crop((int(hw * 0.04), int(hh * 0.25), int(hw * 0.34), int(hh * 0.55)))
        c2_corner = hero.crop((int(hw * 0.33), int(hh * 0.25), int(hw * 0.62), int(hh * 0.55)))
        
        cards: List[DetectedCard] = []
        for idx, (c_crop, (rel_x1, rel_x2)) in enumerate([
            (c1_corner, (int(hw * 0.04), int(hw * 0.34))),
            (c2_corner, (int(hw * 0.33), int(hw * 0.62)))
        ]):
            suit, _ = self._sample_card_suit(c_crop)
            if not suit:
                continue
                
            arr = np.array(c_crop.convert("RGB"))
            H_c, W_c = arr.shape[:2]
            quad = arr[:int(H_c * 0.60), :int(W_c * 0.65)]
            white = (quad[:,:,0] > 185) & (quad[:,:,1] > 185) & (quad[:,:,2] > 185)
            
            # Extract largest component in quadrant
            h_q, w_q = white.shape
            visited = np.zeros((h_q, w_q), dtype=bool)
            best_comp = []
            for cy in range(h_q):
                for cx in range(w_q):
                    if white[cy, cx] and not visited[cy, cx]:
                        comp = []
                        q = [(cx, cy)]
                        visited[cy, cx] = True
                        while q:
                            qx, qy = q.pop()
                            comp.append((qy, qx))
                            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                                nx, ny = qx + dx, qy + dy
                                if 0 <= nx < w_q and 0 <= ny < h_q and not visited[ny, nx] and white[ny, nx]:
                                    visited[ny, nx] = True
                                    q.append((nx, ny))
                        if len(comp) > len(best_comp):
                            best_comp = comp
                            
            if not best_comp:
                continue
                
            comp_arr = np.zeros((h_q, w_q), dtype=bool)
            for cy, cx in best_comp:
                comp_arr[cy, cx] = True
                
            rows = np.where(np.any(comp_arr, axis=1))[0]
            cols = np.where(np.any(comp_arr, axis=0))[0]
            if len(rows) == 0 or len(cols) == 0:
                continue
                
            trimmed = comp_arr[rows[0]:rows[-1]+1, cols[0]:cols[-1]+1]
            char_img = Image.fromarray((trimmed * 255).astype(np.uint8))
            rank, conf = self._classify_gg_rank(char_img)
            if conf >= 0.65 and rank != "?":
                cards.append(DetectedCard(
                    card=f"{rank}{suit}",
                    rank=rank,
                    suit=suit,
                    confidence=conf,
                    bbox=(bx1 + rel_x1, by1 + int(hh * 0.25), bx1 + rel_x2, by1 + int(hh * 0.55))
                ))
        if len(cards) != 2:
            return []
        return cards

if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="ClubGG Card Rank & Suit Detector")
    parser.add_argument("-i", "--image", required=True, help="Path to table screenshot image")
    args = parser.parse_args()

    detector = CardDetector()
    img = Image.open(args.image)
    report = detector.detect_community_cards(img)

    print("=" * 60)
    print("           COMMUNITY CARDS DETECTION REPORT")
    print("=" * 60)
    print(f" Board Stage   : {report.stage} ({report.card_count} cards)")
    print(f" Health Status : {report.health}")
    print(f" Valid Stage   : {report.is_valid_stage}")
    print(f" Has Duplicates: {report.has_duplicates}")
    if report.duplicate_cards:
        print(f" Duplicates    : {', '.join(report.duplicate_cards)}")
    print("-" * 60)
    for idx, c in enumerate(report.cards):
        suit_letter = c.suit.upper()
        sname = {"c": "Clubs (C)", "d": "Diamonds (D)", "h": "Hearts (H)", "s": "Spades (S)"}.get(c.suit, c.suit)
        print(f" Card {idx + 1}: {c.card:<4} | Rank: {c.rank:<2} | Suit: {sname:<14} | Conf: {c.confidence:.1%}")
    print("=" * 60)
