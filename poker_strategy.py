#!/usr/bin/env python3
"""
ClubGG Heads-Up Poker Tournament Strategy & Decision Engine
===========================================================
Generates game-theoretic and heuristic actions for Heads-Up (1-on-1) SNG/tournaments:
  - Stack Depth Awareness: Effective Stack in Big Blinds (M / BB ratio).
  - Preflop SNG/Spin Push-Fold Matrix (Nash Equilibrium / Sklansky-Chubukov).
  - Preflop Position Strategy: BTN/SB (opener/first to act) vs BB (defender).
  - Postflop Rules: Free checks, Pot-odds calling, C-betting, and Value raising.
  - Safe Action Mapping: Direct device coordinate generation with sanity fallbacks.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

# Rank hierarchy for hand evaluation
RANK_VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14
}

class ActionType(str, Enum):
    FOLD = "FOLD"
    CHECK = "CHECK"
    CALL = "CALL"
    BET = "BET"
    RAISE = "RAISE"
    ALL_IN = "ALL_IN"

@dataclass
class ActionPlan:
    action: ActionType
    amount: Optional[float] = None
    target_tap: Tuple[int, int] = (504, 2165) # Default to center Check/Call
    preset_tap: Optional[Tuple[int, int]] = None
    confidence: float = 1.0
    hand_notation: str = ""
    effective_bb: float = 0.0
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "amount": self.amount,
            "target_tap": self.target_tap,
            "preset_tap": self.preset_tap,
            "confidence": self.confidence,
            "hand_notation": self.hand_notation,
            "effective_bb": round(self.effective_bb, 1),
            "rationale": self.rationale
        }


# Push-Fold Shove Tiers for Heads-Up SB (Small Blind / Button)
# Hands pushable at or below the given effective BB stack
HU_SB_PUSH_CHART: Dict[str, float] = {
    # Pairs
    "AA": 100.0, "KK": 100.0, "QQ": 100.0, "JJ": 100.0, "TT": 100.0,
    "99": 25.0,  "88": 20.0,  "77": 16.0,  "66": 14.0,  "55": 12.0,
    "44": 10.0,  "33": 9.0,   "22": 8.0,
    # Suited Aces
    "AKs": 100.0, "AQs": 100.0, "AJs": 100.0, "ATs": 50.0,
    "A9s": 25.0,  "A8s": 22.0,  "A7s": 20.0,  "A6s": 18.0,
    "A5s": 18.0,  "A4s": 16.0,  "A3s": 15.0,  "A2s": 14.0,
    # Offsuit Aces
    "AKo": 100.0, "AQo": 100.0, "AJo": 30.0,  "ATo": 22.0,
    "A9o": 18.0,  "A8o": 15.0,  "A7o": 14.0,  "A6o": 12.0,
    "A5o": 12.0,  "A4o": 11.0,  "A3o": 10.0,  "A2o": 9.0,
    # Suited Broadways & Connectors
    "KQs": 35.0,  "KJs": 25.0,  "KTs": 20.0,  "K9s": 15.0,  "K8s": 12.0,
    "QJs": 22.0,  "QTs": 18.0,  "Q9s": 14.0,  "JTs": 18.0,  "J9s": 14.0,
    "T9s": 14.0,  "98s": 12.0,  "87s": 10.0,  "76s": 9.0,   "65s": 8.0,
    # Offsuit Broadways
    "KQo": 25.0,  "KJo": 18.0,  "KTo": 14.0,  "K9o": 11.0,  "K8o": 9.0,
    "QJo": 15.0,  "QTo": 12.0,  "Q9o": 9.0,   "JTo": 12.0,  "J9o": 9.0,
    "T9o": 9.0,   "98o": 7.0
}


class HeadsUpPokerStrategy:
    """Strategy advisor and decision engine for Heads-Up tournaments."""

    def __init__(self):
        pass

    def get_hand_notation(self, cards: List[str]) -> str:
        """Converts ['Td', '9d'] -> 'T9s', ['Ah', 'Kc'] -> 'AKo', ['8s', '8c'] -> '88'."""
        if len(cards) < 2:
            return ""
        c1, c2 = cards[0], cards[1]
        r1, s1 = c1[0], c1[1] if len(c1) > 1 else ""
        r2, s2 = c2[0], c2[1] if len(c2) > 1 else ""
        
        v1 = RANK_VALUES.get(r1, 0)
        v2 = RANK_VALUES.get(r2, 0)
        
        if v1 < v2:
            r1, r2 = r2, r1
            s1, s2 = s2, s1
            
        if r1 == r2:
            return f"{r1}{r2}"
        elif s1 and s2 and s1 == s2:
            return f"{r1}{r2}s"
        else:
            return f"{r1}{r2}o"

    def decide_action(
        self,
        hero_cards: List[str],
        community_cards: List[str],
        board_stage: str,
        total_pot: Optional[float],
        hero_stack: Optional[float],
        opp_stack: Optional[float],
        big_blind: Optional[float],
        position: Optional[str],
        action_buttons: Dict[str, Any]
    ) -> ActionPlan:
        """
        Calculates optimal action based on table state and available buttons.
        """
        can_fold = action_buttons.get("can_fold", False)
        can_check = action_buttons.get("can_check", False)
        can_call = action_buttons.get("can_call", False)
        can_bet = action_buttons.get("can_bet", False)
        can_raise = action_buttons.get("can_raise", False)
        call_amt = action_buttons.get("call_amount") or 0.0
        bet_raise_amt = action_buttons.get("bet_raise_amount")
        fold_tap = action_buttons.get("fold_tap", (184, 2169))
        check_call_tap = action_buttons.get("check_call_tap", (504, 2165))
        bet_raise_tap = action_buttons.get("bet_raise_tap", (867, 2165))
        
        # Effective stack and BB calculation
        h_stk = hero_stack if (hero_stack and hero_stack > 0) else 3000.0
        o_stk = opp_stack if (opp_stack and opp_stack > 0) else 3000.0
        eff_stack = min(h_stk, o_stk)
        bb = big_blind if (big_blind and big_blind > 0) else 100.0
        eff_bb = eff_stack / bb
        
        hand_not = self.get_hand_notation(hero_cards)

        # -------------------------------------------------------------
        # Safeguard 1: Unknown hole cards
        # -------------------------------------------------------------
        if not hand_not or len(hero_cards) < 2:
            if can_check:
                return ActionPlan(
                    action=ActionType.CHECK,
                    target_tap=check_call_tap,
                    confidence=0.80,
                    hand_notation="??",
                    effective_bb=eff_bb,
                    rationale="Unknown hole cards; safe check."
                )
            else:
                return ActionPlan(
                    action=ActionType.FOLD,
                    target_tap=fold_tap,
                    confidence=0.80,
                    hand_notation="??",
                    effective_bb=eff_bb,
                    rationale="Unknown hole cards facing a bet; safe fold."
                )

        # -------------------------------------------------------------
        # Preflop Strategy
        # -------------------------------------------------------------
        if board_stage.lower() == "preflop" or len(community_cards) == 0:
            return self._decide_preflop(
                hand_not, eff_bb, position,
                can_check, can_call, can_bet, can_raise,
                call_amt, bet_raise_amt,
                fold_tap, check_call_tap, bet_raise_tap
            )

        # -------------------------------------------------------------
        # Postflop Strategy
        # -------------------------------------------------------------
        return self._decide_postflop(
            hero_cards, community_cards, board_stage,
            total_pot, eff_bb,
            can_check, can_call, can_bet, can_raise,
            call_amt, bet_raise_amt,
            fold_tap, check_call_tap, bet_raise_tap
        )

    def _decide_preflop(
        self,
        hand_not: str,
        eff_bb: float,
        position: Optional[str],
        can_check: bool,
        can_call: bool,
        can_bet: bool,
        can_raise: bool,
        call_amt: float,
        bet_raise_amt: Optional[float],
        fold_tap: Tuple[int, int],
        check_call_tap: Tuple[int, int],
        bet_raise_tap: Tuple[int, int]
    ) -> ActionPlan:
        is_btn = (position and "BTN" in position) or (not can_check and can_raise)
        shove_threshold = HU_SB_PUSH_CHART.get(hand_not, 0.0)

        # 1. Short Stack Shove Regime (<= 14 BB)
        if eff_bb <= 14.0:
            if eff_bb <= shove_threshold:
                if can_raise:
                    return ActionPlan(
                        action=ActionType.ALL_IN,
                        amount=bet_raise_amt,
                        target_tap=bet_raise_tap,
                        confidence=0.95,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"Nash short-stack shove ({eff_bb:.1f} BB <= {shove_threshold:.0f} BB chart) with {hand_not}."
                    )
                elif can_call:
                    return ActionPlan(
                        action=ActionType.CALL,
                        amount=call_amt,
                        target_tap=check_call_tap,
                        confidence=0.92,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"Calling all-in/shove with top tier {hand_not}."
                    )
            else:
                # Weak hand short stack
                if can_check:
                    return ActionPlan(
                        action=ActionType.CHECK,
                        target_tap=check_call_tap,
                        confidence=0.95,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"Free check with weak hand {hand_not}."
                    )
                else:
                    return ActionPlan(
                        action=ActionType.FOLD,
                        target_tap=fold_tap,
                        confidence=0.95,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"Fold below push range ({eff_bb:.1f} BB > {shove_threshold:.0f} BB) with {hand_not}."
                    )

        # 2. Medium/Deep Stack Regime (> 14 BB)
        if is_btn:
            # As Button: open raise top ~75% of hands
            if shove_threshold >= 10.0 or hand_not.endswith("s") or hand_not[0] in "AKQJ":
                if can_raise:
                    return ActionPlan(
                        action=ActionType.RAISE,
                        amount=bet_raise_amt,
                        target_tap=bet_raise_tap,
                        confidence=0.90,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"BTN open-raise with playable {hand_not}."
                    )
                elif can_call:
                    return ActionPlan(
                        action=ActionType.CALL,
                        amount=call_amt,
                        target_tap=check_call_tap,
                        confidence=0.88,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"BTN call with {hand_not}."
                    )
            else:
                if can_check:
                    return ActionPlan(
                        action=ActionType.CHECK,
                        target_tap=check_call_tap,
                        confidence=0.95,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"BTN check back with {hand_not}."
                    )
                else:
                    return ActionPlan(
                        action=ActionType.FOLD,
                        target_tap=fold_tap,
                        confidence=0.95,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"BTN fold trash hand {hand_not}."
                    )
        else:
            # As Big Blind:
            if can_check:
                # Opponent just limped or unraised
                if shove_threshold >= 18.0 and can_raise:
                    return ActionPlan(
                        action=ActionType.RAISE,
                        amount=bet_raise_amt,
                        target_tap=bet_raise_tap,
                        confidence=0.88,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"BB isolate/raise premium {hand_not} over limp."
                    )
                return ActionPlan(
                    action=ActionType.CHECK,
                    target_tap=check_call_tap,
                    confidence=0.98,
                    hand_notation=hand_not,
                    effective_bb=eff_bb,
                    rationale=f"BB free check option with {hand_not}."
                )
            else:
                # Facing a raise
                if shove_threshold >= 15.0 and can_call:
                    return ActionPlan(
                        action=ActionType.CALL,
                        amount=call_amt,
                        target_tap=check_call_tap,
                        confidence=0.88,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"BB call raise with playable {hand_not}."
                    )
                else:
                    return ActionPlan(
                        action=ActionType.FOLD,
                        target_tap=fold_tap,
                        confidence=0.95,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"BB fold to aggression with {hand_not}."
                    )

    def _decide_postflop(
        self,
        hero_cards: List[str],
        community_cards: List[str],
        board_stage: str,
        total_pot: Optional[float],
        eff_bb: float,
        can_check: bool,
        can_call: bool,
        can_bet: bool,
        can_raise: bool,
        call_amt: float,
        bet_raise_amt: Optional[float],
        fold_tap: Tuple[int, int],
        check_call_tap: Tuple[int, int],
        bet_raise_tap: Tuple[int, int]
    ) -> ActionPlan:
        hero_ranks = [c[0] for c in hero_cards if c]
        board_ranks = [c[0] for c in community_cards if c]
        hero_suits = [c[1] for c in hero_cards if len(c) > 1]
        board_suits = [c[1] for c in community_cards if len(c) > 1]
        
        matches = [r for r in hero_ranks if r in board_ranks]
        is_pocket_pair = (len(hero_ranks) >= 2 and hero_ranks[0] == hero_ranks[1])
        has_pair = len(matches) > 0 or is_pocket_pair
        
        board_vals = [RANK_VALUES.get(r, 0) for r in board_ranks]
        top_board_val = max(board_vals) if board_vals else 0
        has_top_pair = any(RANK_VALUES.get(r, 0) >= top_board_val for r in matches) or \
                       (is_pocket_pair and RANK_VALUES.get(hero_ranks[0], 0) > top_board_val)
                       
        has_flush_draw = False
        for s in set(hero_suits):
            suit_count = hero_suits.count(s) + board_suits.count(s)
            if suit_count >= 4:
                has_flush_draw = True
                break

        hand_not = self.get_hand_notation(hero_cards)

        if can_check:
            if (has_top_pair or (has_pair and len(matches) >= 2)) and can_bet:
                return ActionPlan(
                    action=ActionType.BET,
                    amount=bet_raise_amt,
                    target_tap=bet_raise_tap,
                    confidence=0.90,
                    hand_notation=hand_not,
                    effective_bb=eff_bb,
                    rationale=f"Value bet with strong pair/draw ({hand_not} connected with {board_stage})."
                )
            elif has_flush_draw and can_bet and board_stage.lower() == "flop":
                return ActionPlan(
                    action=ActionType.BET,
                    amount=bet_raise_amt,
                    target_tap=bet_raise_tap,
                    confidence=0.85,
                    hand_notation=hand_not,
                    effective_bb=eff_bb,
                    rationale=f"Semi-bluff c-bet with flush draw on {board_stage}."
                )
            else:
                return ActionPlan(
                    action=ActionType.CHECK,
                    target_tap=check_call_tap,
                    confidence=0.95,
                    hand_notation=hand_not,
                    effective_bb=eff_bb,
                    rationale=f"Safe free check on {board_stage} with {hand_not}."
                )
        else:
            if has_top_pair or (has_pair and len(matches) >= 2):
                if can_raise and has_top_pair and (len(matches) >= 2 or is_pocket_pair):
                    return ActionPlan(
                        action=ActionType.RAISE,
                        amount=bet_raise_amt,
                        target_tap=bet_raise_tap,
                        confidence=0.90,
                        hand_notation=hand_not,
                        effective_bb=eff_bb,
                        rationale=f"Value raise monster hand on {board_stage}."
                    )
                return ActionPlan(
                    action=ActionType.CALL,
                    amount=call_amt,
                    target_tap=check_call_tap,
                    confidence=0.88,
                    hand_notation=hand_not,
                    effective_bb=eff_bb,
                    rationale=f"Call bet with connected pair on {board_stage}."
                )
            elif has_flush_draw and can_call and call_amt <= (total_pot or 500) * 0.4:
                return ActionPlan(
                    action=ActionType.CALL,
                    amount=call_amt,
                    target_tap=check_call_tap,
                    confidence=0.82,
                    hand_notation=hand_not,
                    effective_bb=eff_bb,
                    rationale=f"Call bet getting good pot odds for flush draw."
                )
            else:
                return ActionPlan(
                    action=ActionType.FOLD,
                    target_tap=fold_tap,
                    confidence=0.92,
                    hand_notation=hand_not,
                    effective_bb=eff_bb,
                    rationale=f"Fold missed board on {board_stage} facing bet."
                )
