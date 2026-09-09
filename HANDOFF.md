# HANDOFF: Game History Automated Data Collection & Processing

**Branch**: `feature/game-history`  
**Date**: 2026-09-09  
**Status**: In Progress — Crawler Built & Micro-Verified, Initial 762 Hand Images Collected, Issues Identified & Patched.

---

## 1. Executive Summary

We developed an automated bottom-up crawler for ClubGG hand histories. The pipeline traverses game history from the oldest sessions at the bottom (`2026-09-03`) to the newest sessions at the top (`2026-09-09`), reads session card metadata, enters each session viewer, cycles backwards through all hands, captures full-resolution screenshots ($1008 \times 2244$), and indexes everything in a central catalog.

- **Total Hand Images Captured**: **762 hand screenshots**
- **Cataloged Complete Sessions**: **31 sessions** (48 hands verified in first batch, 714 in second batch)
- **Output Directory**: [`game_history/`](file:///c:/Users/chhu3/OneDrive/Documents/android/game_history)
- **Master Index**: [`game_history/crawled_sessions.json`](file:///c:/Users/chhu3/OneDrive/Documents/android/game_history/crawled_sessions.json)

---

## 2. Core Architecture & Key Discoveries

### Device & Navigation Geometry (Pixel 9 Pro XL, $1008 \times 2244$)
1. **7-Box List Screen Model**:
   - Exactly 7 session cards fit on the screen at a time.
   - Card height: `261px`. Top offset: `400px`.
   - Slot center coordinates: $y \in [530, 791, 1052, 1313, 1574, 1835, 2096]$.
2. **Replayer Controls**:
   - **Back to List**: Hardware `KEYCODE_BACK` fails in ClubGG. Must tap top-left chevron at **`(45, 210)`**.
   - **Previous Hand**: Circular arrow button on bottom-left at **`(60, 2166)`**.
   - **Next Hand**: Circular arrow button on bottom-right at **`(946, 2165)`**.
   - **Hand Counter Badge**: Green pill at bottom right `(814, 2126)` displaying `Current / Total` (e.g., `13 / 13`).
3. **Scroll Dynamics**:
   - Swiping finger **up** (`500, 1800 -> 500, 600`) scrolls **down** toward older sessions.
   - Swiping finger **down** (`500, 800 -> 500, 1583`) scrolls **up** toward newer sessions.

---

## 3. Current Problems & Root Causes Identified

### Problem 1: Incomplete Sessions (1-Hand Captures)
- **Issue**: 7 sessions in the full run only captured 1 hand despite having 16, 74, or 84 hands.
- **Root Cause**: Windows Media OCR parsed the bottom pill counter `74 / 74` as three distinct tokens: `['74', '/', '74']`. The regex `re.search(r"(\d+)\s*/\s*(\d+)", t[4])` inspected each token individually and failed. The crawler fell back to `expected_hands = 1`.
- **Fix Applied**:
  1. Updated `extract_hand_info()` to join tokens with whitespace: `bot_text = " ".join(t[4] for t in bot_words)` before regex matching.
  2. Moved folder creation after `init_idx` extraction so folders reflect the true replayer hand count.
  3. Purged the 7 incomplete folders and deleted their catalog keys so they will be cleanly re-crawled.

### Problem 2: Card Skipping Due to Large Scroll Distance
- **Issue**: The original scroll swipe jumped $\approx 1300\text{px}$ (5–6 cards). Momentum or slight scroll acceleration caused several cards on `2026-09-07` to be skipped over.
- **Root Cause**: Scroll step was too coarse relative to screen size ($5$ cards out of $7$).
- **Fix Applied**: Reduced scroll distance to exactly **3 slots** ($783\text{px}$, swipe `500, 800 -> 500, 1583`). This maintains a 4-card overlap between views. Because crawled sessions are deduplicated in $\sim 1\text{ms}$ via `crawled_sessions.json`, overlapping ensures zero dropped sessions.

### Problem 3: Date/Time OCR Incompleteness
- **Issue**: In list cards, timestamps (`18:48:50`) are rendered in dark gray on black, and Windows OCR occasionally only reads the date `2026-09-03` while omitting the time.
- **Impact**: Some folders have `00-00` for time, but are still unique due to `date_type_blinds_hands_pnl`.
- **Mitigation**: Every individual hand screenshot inside the session view captures the unique hand ID (e.g. `#1785981091`), providing an immutable, globally unique identifier.

### Problem 4: Player Name Instability at Showdown
- **Context**: During showdown replays, player name labels are replaced by equity percentages (`96.16%`) or `WIN` banners.
- **Rule**: When parsing table states from hand screenshots, never overwrite an established player name unless a seat becomes vacant.

---

## 4. Key Files & Artifacts

| Path | Purpose |
|---|---|
| [`crawl_game_history.py`](file:///c:/Users/chhu3/OneDrive/Documents/android/crawl_game_history.py) | Full standalone automated crawler script. |
| [`game_history/crawled_sessions.json`](file:///c:/Users/chhu3/OneDrive/Documents/android/game_history/crawled_sessions.json) | Master index tracking all completed sessions. |
| [`game_history/`](file:///c:/Users/chhu3/OneDrive/Documents/android/game_history) | Storage directory containing 762+ hand replay screenshots. |
| [`table_detector.py`](file:///c:/Users/chhu3/OneDrive/Documents/android/table_detector.py) | OCR and table state detector. |
| [`record_touches.py`](file:///c:/Users/chhu3/OneDrive/Documents/android/record_touches.py) | Touch recording script with Windows encoding fixes. |

---

## 5. Next Steps to Resume

1. **Run the Patched Crawler**:
   Execute the crawler to capture the remaining un-crawled sessions and the 7 purged sessions:
   ```powershell
   .\.venv\Scripts\python.exe crawl_game_history.py 50
   ```
2. **Verify Full Dataset**:
   Run the verification snippet:
   ```powershell
   .\.venv\Scripts\python.exe -c "
   import json, os
   cat = json.load(open(r'game_history\crawled_sessions.json'))
   print('Total sessions:', len(cat), '| Total hands:', sum(s['total_hands'] for s in cat.values()))
   "
   ```
3. **Restart Web Dashboard**:
   If needed, relaunch Flask server:
   ```powershell
   .\.venv\Scripts\python.exe web_gui\app.py
   ```
4. **Git Hygiene**:
   Ensure user approval before committing changes to `feature/game-history`.
