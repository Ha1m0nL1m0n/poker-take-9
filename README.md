# Android Touch & Tap Recorder (`record_touches.py`)

Simultaneously records touch events from:
1. **Physical Phone Taps** (finger taps, presses, and swipes via ADB `getevent`)
2. **PC scrcpy Window Clicks** (mouse clicks, long-presses, and drag-swipes inside the `scrcpy` window)

### 📸 Automatic Centered Screenshot Capture
Whenever an action occurs (tap, click, press, or swipe):
1. **Instant Capture**: Takes a clean, pixel-perfect screenshot of the active Android screen at the **exact moment** of the event (`action_001_event.png`).
2. **Delayed Capture**: Takes a follow-up screenshot **delayed by 500ms** (or custom `--delayed-ms`) after the event (`action_001_delayed_500ms.png`) to capture the resulting UI state.

- **Centered & Borderless**: Uses GDI+ viewport-aligned cropping that excludes Windows title bars, window frames, and letterbox bars.
- **Never Overwrites**: Automatically detects existing files in `./captures/` and auto-increments indices (e.g. restarts pick up at `action_114_...`). If `recorded_touches.json` exists, saves to `recorded_touches_1.json` unless `--overwrite` is specified.

---

## Quick Start

### 1. Record Taps & Clicks

Run in PowerShell:
```powershell
python record_touches.py
```
- **Tap on your phone** OR **Click with your mouse inside `scrcpy`**.
- Every action and its screenshots are logged in real time:
  ```text
  [01] TAP        (scrcpy) at ( 504, 1122)   | Hold:  65ms | Delay: 1200ms
       📸 Captured: captures/action_001_event.png  &  captures/action_001_delayed_500ms.png
  [02] LONG_PRESS (Phone ) at ( 810,  320)   | Hold: 720ms | Delay: 1540ms
       📸 Captured: captures/action_002_event.png  &  captures/action_002_delayed_500ms.png
  ```
- Press `Ctrl+C` when done to save to `recorded_touches.json`.

---

### 2. Replay Recorded Actions

Replay on the phone with original delays:
```powershell
python record_touches.py --replay
```
Or double-click the generated `recorded_touches_replay.bat`.

To replay at **2x speed**:
```powershell
python record_touches.py --replay --speed 2.0
```

---

## Options & Flags

| Flag | Description |
|---|---|
| *(default)* | Dual-mode: records both phone taps and scrcpy clicks with centered auto-captures |
| `-c <dir>` | Directory to save screenshots (default: `captures`) |
| `--delayed-ms <ms>` | Delay in ms for post-event capture (default: `500`) |
| `--screencap` | Capture full-resolution 1008x2244 direct device screencaps instead of scrcpy window |
| `--capture-source <type>` | Screenshot engine: `scrcpy` (fast ~5ms) or `adb` (full-res ~700ms) |
| `--overwrite` | Overwrite existing output JSON file instead of generating `_1.json` |
| `--no-capture` | Disable screenshot captures |
| `--mode phone` | Record only physical phone taps |
| `--mode scrcpy` | Record only scrcpy mouse clicks |
| `-o <file>` | Custom JSON output path (default: `recorded_touches.json`) |
| `--replay [file]` | Replay recorded actions on phone |
| `--speed <factor>` | Replay speed multiplier (e.g. `1.5`, `2.0`, `0.5`) |
| `--count <N>` | Automatically stop after recording N touches |
| `--pointer-location on/off` | Toggle Android's built-in coordinate bar on screen |
| `--show-touches on/off` | Toggle Android's visual touch circles |

---

## 🔔 Sound & Vibration Event Monitor (`monitor_app_events.py`)

Monitors an Android app in real time for **sound playback / audio bursts** and **vibrations/haptics**, automatically captures screenshots on each event, and documents everything in a structured JSON file.

### ✨ Key Features
- **Zero False Triggers at Startup**: Pre-initializes baseline device state on launch, automatically ignoring past vibration records and pre-existing audio tracks.
- **Audio Signal Power Burst Detection**: Captures real-time sound effects (such as chips, cards, timer beeps, button clicks) with 50ms resolution from Android AudioFlinger's signal power history, complete with duration and dBFS levels. Works for both Unity/game apps and standard Android apps.
- **Vibration / Haptic Detection**: Accurately logs duration, waveform pattern, calling app, and UID.
- **Auto Screenshots**: Takes an instant centered screenshot (`event_001_audio.png`, `event_002_vibration.png`) and follow-up delayed screenshot (`+500ms`) into `./captures/`.
- **Never Overwrites**: Automatically picks up the next unused event index across any existing captures and auto-increments JSON output (`app_events_1.json`, etc.).

### Quick Start

Run in PowerShell:
```powershell
python monitor_app_events.py
```

- **Target App**: Defaults to `com.nsus.clubgg`. Pass `-p ""` to monitor all apps or `-p <package>` for a specific app.
- **Live Terminal Logging**:
  ```text
  ===========================================================================
         ANDROID APP SOUND & VIBRATION EVENT RECORDER
  ===========================================================================
   Device Serial    : 46261FDAS003BU
   Target App       : com.nsus.clubgg
   Screen Size      : 1008 x 2244
   scrcpy Window    : Pixel 9 Pro XL (HWND: 787746)
   Screenshot Engine: scrcpy Centered Window [instant + 500ms delayed]
   Captures Dir     : ./captures/ (Starting at event_001)
   Output JSON File : app_events.json
   Polling Interval : 150ms
   Audio Debounce   : 350ms
  ---------------------------------------------------------------------------
   [*] Initializing baseline device state...
   [*] Baseline established successfully:
       - Ignored 534 pre-existing vibrations in history
       - Ignored 14 pre-existing audio bursts in history
       - Tracked 2 currently open audio tracks
   [*] Ready! ZERO false triggers at startup. Waiting for real-time events...
   [*] Press Ctrl+C at any time to stop.
  ===========================================================================

   [21:45:12.345] 📳 [EVENT 001] VIBRATION TRIGGERED!
                App      : com.nsus.clubgg (UID 10395)
                Duration : 250 ms
                Effect   : [Step=0ms(amplitude=0.00)...]
                📸 Shot  : captures/event_001_vibration.png & captures/event_001_vibration_delayed_500ms.png
  ---------------------------------------------------------------------------
   [21:45:15.123] 🔊 [EVENT 002] AUDIO SOUND BURST DETECTED!
                App      : com.nsus.clubgg
                Duration : 450 ms (9 samples @ 50ms)
                Peak/Sum : -38.1 dBFS
                📸 Shot  : captures/event_002_audio.png & captures/event_002_audio_delayed_500ms.png
  ---------------------------------------------------------------------------
  ```

### Monitor Options

| Flag | Description |
|---|---|
| `-p <package>` | Package name to monitor (default: `com.nsus.clubgg`, empty `""` for all apps) |
| `-o <file>` | JSON output file (default: `app_events.json`) |
| `--delayed-ms <ms>` | Delay for post-event capture (default: `500`, `0` to disable) |
| `--audio-debounce <sec>` | Debounce window in seconds for rapid audio bursts (default: `0.35s` / 350ms) |
| `--screencap` | Capture raw 1008x2244 device screencaps instead of scrcpy window |
| `--capture-source <src>` | Screenshot engine: `scrcpy` (fast ~5ms) or `adb` (full-res 1008x2244) |
| `--overwrite` | Overwrite existing output JSON instead of auto-incrementing |
| `--no-capture` | Disable screenshot captures |
| `--interval <sec>` | Polling interval in seconds (default: `0.15s` / 150ms) |
| `--detect-table` | Automatically analyze full poker table state (players, stacks, VPIP, pot) on each event |

---

## ♠️ Poker Table Detector & HUD (`table_detector.py`)

Extracts real-time poker table state from ClubGG screenshots and live device screencaps:
- **Player Count & Seating**: Detects total seated players (e.g. 8/8) vs open empty seats (`Take Seat`).
- **In-Hand vs Folded**: Distinguishes players holding active cards vs folded players vs sitting out.
- **Player Metrics**: Extracts username, stack size (chips), VPIP stat score, and current action badge (`Check`, `Call`, `Bet`, `Raise`, `All-In`, `Fold`).
- **Dealer Button & Positions**: Detects the gold `'D'` coin and automatically assigns positions clockwise: `BTN`, `SB`, `BB`, `UTG`, `UTG+1`, `MP`, `HJ`, `CO`.
- **Table State**: Reads Total Pot, Board Stage (`PREFLOP`, `FLOP`, `TURN`, `RIVER`), Community Cards, and Table Blinds (e.g. `0.25/0.50`).
- **Waiting Queue**: Detects the number of waiting players in queue.

### Standalone Usage

1. **Analyze a specific screenshot**:
   ```powershell
   python table_detector.py --image captures/event_206_audio.png
   ```

2. **Capture and analyze live table from connected device**:
   ```powershell
   python table_detector.py --screencap
   ```

3. **Save structured JSON output**:
   ```powershell
   python table_detector.py --screencap -o table_state.json
   ```

### Output Example
```text
===========================================================================
                CLUBGG POKER TABLE DETECTION RESULT
===========================================================================
 Table Type       : 8-max (NLH)
 Blinds           : 0.25/0.5
 Board Stage      : FLOP (3 cards)
 Total Pot        : 4.5
 Dealer Seat      : Seat 2 (BTN)
 Seated Players   : 8 / 8
 Active In Hand   : 3
 Waiting Queue    : 3
 Analysis Time    : 0.59s
---------------------------------------------------------------------------
 Seat       Pos      Username        Stack      VPIP     In Hand    Action    
---------------------------------------------------------------------------
 1          CO       playforfun321   50.50      -        No (fold)  -         
 2          BTN      liran levi      33.15      -        YES        -         
 3          SB       nativ666        45.45      38%      No (fold)  -         
 4          BB       Unknown         51.40      20%      No (fold)  -         
 5          UTG      Oridh5          101.81     43%      No (fold)  -         
 6          UTG+1    Tzur karmon     28.62      29%      No (fold)  -         
 7          MP       gaditz66        19.10      71%      YES        -         
 8          HJ       2-7nuts         67.52      63%      YES        -         
===========================================================================
```



