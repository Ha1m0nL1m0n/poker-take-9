/**
 * ClubGG Poker Table HUD & Card Recognition - Frontend Controller
 */

// Normalized 8-Max Seat Layout Relative to Table Container (%)
const SEAT_LAYOUT = {
  1: { x: 12, y: 24 }, // Top-Left
  2: { x: 50, y: 8 },  // Top-Center
  3: { x: 88, y: 24 }, // Top-Right
  4: { x: 94, y: 50 }, // Mid-Right
  5: { x: 88, y: 76 }, // Bottom-Right
  6: { x: 50, y: 92 }, // Bottom-Hero
  7: { x: 12, y: 76 }, // Lower-Left
  8: { x: 6, y: 50 }   // Mid-Left
};

const SUIT_DISPLAY = {
  c: { name: "Clubs", symbol: "♣", color: "#2ecc71" },
  d: { name: "Diamonds", symbol: "♦", color: "#3498db" },
  h: { name: "Hearts", symbol: "♥", color: "#e74c3c" },
  s: { name: "Spades", symbol: "♠", color: "#bdc3c7" }
};

let currentData = null;
let currentImage = null;
let isLivePolling = false;
let liveAbortController = null;
let frameCount = 0;
let fpsStartTime = 0;

// DOM Elements
const headerBlinds = document.getElementById("header-blinds");
const headerPot = document.getElementById("header-pot");
const headerStage = document.getElementById("header-stage");
const headerIntegrity = document.getElementById("header-integrity");
const tablePotVal = document.getElementById("table-pot-val");
const tableStakesVal = document.getElementById("table-stakes-val");
const tableQueueVal = document.getElementById("table-queue-val");
const seatsRing = document.getElementById("seats-ring");
const analysisTimeStamp = document.getElementById("analysis-time-stamp");
const selectCapture = document.getElementById("select-capture");
const btnCaptureLive = document.getElementById("btn-capture-live");
const btnLiveText = document.getElementById("btn-live-text");
const btnSnapshot = document.getElementById("btn-snapshot");
const liveIndicator = document.getElementById("live-indicator");
const fpsCounter = document.getElementById("fps-counter");
const chkAutoPoll = document.getElementById("chk-auto-poll");
const currentImageTag = document.getElementById("current-image-tag");
const overlayCanvas = document.getElementById("overlay-canvas");
const ctx = overlayCanvas ? overlayCanvas.getContext("2d") : null;

// Checkboxes
const chkShowSeats = document.getElementById("chk-show-seats");
const chkShowCards = document.getElementById("chk-show-cards");
const chkShowPot = document.getElementById("chk-show-pot");

// Integrity Tab Elements
const healthBadge = document.getElementById("health-badge");
const intStage = document.getElementById("int-stage");
const intCardCount = document.getElementById("int-card-count");
const intDuplicateCheck = document.getElementById("int-duplicate-check");
const intStageRule = document.getElementById("int-stage-rule");
const detectedCardsList = document.getElementById("detected-cards-list");
const cardPackGallery = document.getElementById("card-pack-gallery");

// JSON Tab
const jsonViewer = document.getElementById("json-viewer");
const btnCopyJson = document.getElementById("btn-copy-json");
const copyHint = document.getElementById("copy-hint");

// ---------------------------------------------------------
// Initialization
// ---------------------------------------------------------
function initApp() {
  console.log("[ClubGG HUD] Initializing HUD Controller...");
  try { setupTabs(); } catch (e) { console.error("Error setting up tabs:", e); }
  try { setupEventListeners(); } catch (e) { console.error("Error setting up event listeners:", e); }
  try { initCardPackGallery(); } catch (e) { console.error("Error initializing gallery:", e); }
  try { loadCapturesList(); } catch (e) { console.error("Error loading captures:", e); }
  try { fetchTableState(); } catch (e) { console.error("Error fetching table state:", e); }
  console.log("[ClubGG HUD] HUD Controller ready.");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initApp);
} else {
  // DOM is already ready (e.g., loaded dynamically or from cache)
  initApp();
}

function setupEventListeners() {
  const btnLive = document.getElementById("btn-capture-live");
  const btnSnap = document.getElementById("btn-snapshot");
  const chkPoll = document.getElementById("chk-auto-poll");
  const selCap = document.getElementById("select-capture");
  const btnCopy = document.getElementById("btn-copy-json");

  if (btnLive) {
    btnLive.addEventListener("click", (e) => {
      e.preventDefault();
      toggleLivePolling();
    });
  }

  if (btnSnap) {
    btnSnap.addEventListener("click", (e) => {
      e.preventDefault();
      captureSnapshot();
    });
  }

  if (chkPoll) {
    chkPoll.addEventListener("change", (e) => {
      toggleLivePolling(e.target.checked);
    });
  }

  if (selCap) {
    selCap.addEventListener("change", (e) => {
      if (e.target.value) {
        if (isLivePolling) {
          stopLivePolling();
        }
        analyzeCapture(e.target.value);
      }
    });
  }

  [chkShowSeats, chkShowCards, chkShowPot].forEach(chk => {
    if (chk) chk.addEventListener("change", renderCanvasOverlay);
  });

  if (btnCopy) {
    btnCopy.addEventListener("click", () => {
      if (currentData) {
        navigator.clipboard.writeText(JSON.stringify(currentData, null, 2));
        if (copyHint) {
          copyHint.textContent = "Copied to clipboard!";
          setTimeout(() => { copyHint.textContent = ""; }, 2000);
        }
      }
    });
  }
}

function setupTabs() {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach(btn => {
    btn.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
      btn.classList.add("active");
      const target = document.getElementById(btn.dataset.tab);
      if (target) target.classList.add("active");
    });
  });
}

// ---------------------------------------------------------
// Live Polling & Snapshot
// ---------------------------------------------------------
function toggleLivePolling(forceState = null) {
  const shouldRun = forceState !== null ? forceState : !isLivePolling;
  console.log(`[ClubGG HUD] toggleLivePolling called: current=${isLivePolling}, target=${shouldRun}`);
  if (shouldRun === isLivePolling) return;

  if (shouldRun) {
    startLivePolling();
  } else {
    stopLivePolling();
  }
}

function startLivePolling() {
  if (isLivePolling) return;
  console.log("[ClubGG HUD] Starting live polling loop...");
  isLivePolling = true;

  const btnLive = document.getElementById("btn-capture-live");
  const txtLive = document.getElementById("btn-live-text");
  const indLive = document.getElementById("live-indicator");
  const chkPoll = document.getElementById("chk-auto-poll");

  if (btnLive) btnLive.classList.add("btn-live-active");
  if (txtLive) txtLive.textContent = "Stop Live Polling";
  if (indLive) indLive.classList.remove("hidden");
  if (chkPoll) chkPoll.checked = true;

  liveLoop();
}

function stopLivePolling() {
  console.log("[ClubGG HUD] Stopping live polling loop...");
  isLivePolling = false;
  if (liveAbortController) {
    liveAbortController.abort();
    liveAbortController = null;
  }

  const btnLive = document.getElementById("btn-capture-live");
  const txtLive = document.getElementById("btn-live-text");
  const indLive = document.getElementById("live-indicator");
  const chkPoll = document.getElementById("chk-auto-poll");
  const fpsEl = document.getElementById("fps-counter");

  if (btnLive) btnLive.classList.remove("btn-live-active");
  if (txtLive) txtLive.textContent = "Start Live Polling";
  if (indLive) indLive.classList.add("hidden");
  if (chkPoll) chkPoll.checked = false;
  if (fpsEl) fpsEl.textContent = "-- FPS";
}

// Expose functions globally for inline HTML events
window.toggleLivePolling = toggleLivePolling;
window.startLivePolling = startLivePolling;
window.stopLivePolling = stopLivePolling;
window.captureSnapshot = captureSnapshot;

async function liveLoop() {
  fpsStartTime = performance.now();
  frameCount = 0;

  while (isLivePolling) {
    liveAbortController = new AbortController();
    try {
      const res = await fetch("/api/capture_live", { signal: liveAbortController.signal });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const json = await res.json();
      if (json.success && isLivePolling) {
        updateDashboard(json);
        frameCount++;

        const now = performance.now();
        const elapsedSec = (now - fpsStartTime) / 1000.0;
        if (elapsedSec >= 1.0) {
          const fps = (frameCount / elapsedSec).toFixed(1);
          if (fpsCounter) fpsCounter.textContent = `${fps} FPS`;
          frameCount = 0;
          fpsStartTime = now;
        }
      }
    } catch (err) {
      if (err.name === "AbortError") {
        break;
      }
      console.warn("Live poll frame dropped:", err);
      // Wait slightly on error before retry
      await new Promise(resolve => setTimeout(resolve, 800));
    } finally {
      liveAbortController = null;
    }

    // Small yield between captures so browser renders smoothly
    if (isLivePolling) {
      await new Promise(resolve => setTimeout(resolve, 40));
    }
  }
}

async function captureSnapshot() {
  if (btnSnapshot) btnSnapshot.disabled = true;
  const originalHtml = btnSnapshot ? btnSnapshot.innerHTML : "";
  if (btnSnapshot) btnSnapshot.innerHTML = '<span class="btn-icon">⏳</span> Saving...';

  try {
    const res = await fetch("/api/capture_live?save=true", { method: "POST" });
    const json = await res.json();
    if (json.success) {
      updateDashboard(json);
      await loadCapturesList();
      if (selectCapture && json.image_name) {
        selectCapture.value = json.image_name;
      }
    } else {
      alert("Snapshot Error: " + (json.error || "Failed to capture snapshot"));
    }
  } catch (err) {
    console.error("Error capturing snapshot:", err);
    alert("Network error capturing snapshot");
  } finally {
    if (btnSnapshot) {
      btnSnapshot.disabled = false;
      btnSnapshot.innerHTML = originalHtml;
    }
  }
}

// ---------------------------------------------------------
// Dashboard Rendering
// ---------------------------------------------------------
function updateDashboard(data) {
  currentData = data.state;
  const state = data.state;

  // Header
  headerBlinds.textContent = state.blinds || "N/A";
  headerPot.textContent = state.total_pot !== null ? state.total_pot : "--";
  headerStage.textContent = (state.board_stage || "PREFLOP").toUpperCase();

  // Table Center
  tablePotVal.textContent = state.total_pot !== null ? state.total_pot : "0.00";
  tableStakesVal.textContent = `${state.blinds || "Stakes N/A"} (${state.table_type || "8-max"})`;
  tableQueueVal.textContent = `Queue: ${state.waiting_players !== null ? state.waiting_players : 0}`;
  
  if (data.image_name) {
    currentImageTag.textContent = data.image_name;
  }
  if (state.analysis_time_sec) {
    const capInfo = state.capture_time_ms ? ` (Cap: ${state.capture_time_ms}ms, OCR: ${state.analysis_time_sec}s [${state.capture_source || 'live'}])` : ` (${state.analysis_time_sec}s)`;
    analysisTimeStamp.textContent = `Analyzed in ${state.analysis_time_sec}s${capInfo} - ${state.timestamp || ""}`;
  } else {
    analysisTimeStamp.textContent = state.timestamp || "Active";
  }

  // Community Cards
  renderCommunityCards(state.community_cards || []);

  // Seats
  renderSeats(state.seats || [], state.dealer_seat);

  // Integrity Tab
  renderIntegrity(state);

  // Raw JSON
  jsonViewer.textContent = JSON.stringify(state, null, 2);

  // Source Screenshot & Overlay Canvas
  if (data.image_url) {
    loadImageForCanvas(data.image_url);
  }
}

function renderCommunityCards(cards) {
  for (let i = 0; i < 5; i++) {
    const slot = document.getElementById(`card-slot-${i}`);
    if (!slot) continue;
    slot.innerHTML = "";
    if (i < cards.length) {
      slot.classList.remove("empty");
      const cardStr = cards[i];
      const img = document.createElement("img");
      img.src = `/static/cards/${cardStr}.png`;
      img.className = "card-img";
      img.alt = cardStr;
      img.title = `Card ${i + 1}: ${cardStr}`;
      slot.appendChild(img);
    } else {
      slot.classList.add("empty");
      slot.innerHTML = `<span class="slot-placeholder">${i + 1}</span>`;
    }
  }
}

function renderSeats(seats, dealerSeatId) {
  seatsRing.innerHTML = "";

  seats.forEach(seat => {
    const sid = seat.seat_id;
    const pos = SEAT_LAYOUT[sid] || { x: 50, y: 50 };

    const seatNode = document.createElement("div");
    seatNode.className = "seat-hud";
    seatNode.style.left = `${pos.x}%`;
    seatNode.style.top = `${pos.y}%`;

    if (!seat.is_occupied) {
      seatNode.classList.add("empty");
      seatNode.innerHTML = `<span style="font-size: 0.72rem; color: #718096;">Seat ${sid} (Empty)</span>`;
      seatsRing.appendChild(seatNode);
      return;
    }

    if (!seat.is_in_hand) {
      seatNode.classList.add("folded");
    } else {
      seatNode.classList.add("active-hand");
    }

    // Top Row: Pos badge + VPIP
    const isBtn = sid === dealerSeatId || (seat.position && seat.position.toUpperCase() === "BTN");
    const posClass = isBtn ? "btn" : (seat.position ? seat.position.toLowerCase() : "");
    const posLabel = isBtn ? "BTN" : (seat.position || `S${sid}`);

    const vpipHtml = seat.vpip ? `<span class="vpip-pill">${seat.vpip}%</span>` : "";

    // Action Badge
    let actionHtml = "";
    if (seat.action) {
      const actClass = seat.action.toLowerCase();
      actionHtml = `<span class="action-pill ${actClass}">${seat.action}</span>`;
    }

    // Avatar initial
    const initial = (seat.username && seat.username.length > 0) ? seat.username[0].toUpperCase() : sid;

    // Hole Cards
    let cardsHtml = "";
    if (seat.cards && seat.cards.length > 0) {
      cardsHtml = `<div class="seat-cards-row">` +
        seat.cards.map(c => `<img src="/static/cards/${c}.png" alt="${c}" title="${c}">`).join("") +
        `</div>`;
    } else if (seat.is_in_hand) {
      // Show 2 card backs
      cardsHtml = `<div class="seat-cards-row">
        <img src="/static/cards/back.png" alt="Back">
        <img src="/static/cards/back.png" alt="Back">
      </div>`;
    }

    seatNode.innerHTML = `
      <div class="seat-top-row">
        <span class="pos-tag ${posClass}">${posLabel}</span>
        ${vpipHtml}
      </div>
      <div class="seat-user-row">
        <div class="user-avatar">${initial}</div>
        <span class="username" title="${seat.username || 'Unknown'}">${seat.username || 'Unknown'}</span>
      </div>
      <div class="seat-stack-row">
        <span class="stack-val">${seat.stack !== null ? seat.stack : '--'}</span>
        ${actionHtml}
      </div>
      ${cardsHtml}
    `;

    seatsRing.appendChild(seatNode);

    // Render bet chip on felt if player has a current bet
    if (seat.current_bet && seat.current_bet > 0) {
      const betBadge = document.createElement("div");
      betBadge.className = "felt-bet-badge";
      // Offset towards table center
      const dx = (50 - pos.x) * 0.45;
      const dy = (50 - pos.y) * 0.45;
      betBadge.style.left = `${pos.x + dx}%`;
      betBadge.style.top = `${pos.y + dy}%`;
      betBadge.innerHTML = `<img src="/static/chips/chip_red.png" alt="Chip"> <span>${seat.current_bet}</span>`;
      seatsRing.appendChild(betBadge);
    }
  });
}

function renderIntegrity(state) {
  const rep = state.board_integrity || {};
  const health = rep.health || "HEALTHY";

  headerIntegrity.textContent = health;
  headerIntegrity.className = `m-val badge-integrity ${health.toLowerCase()}`;

  healthBadge.textContent = health;
  healthBadge.className = `health-badge ${health.toLowerCase()}`;

  intStage.textContent = (rep.stage || state.board_stage || "PREFLOP").toUpperCase();
  intCardCount.textContent = `${rep.card_count !== undefined ? rep.card_count : (state.community_cards || []).length} cards`;

  if (rep.has_duplicates) {
    intDuplicateCheck.textContent = `DUPLICATES: ${rep.duplicate_cards.join(", ")}`;
    intDuplicateCheck.className = "val fail";
  } else {
    intDuplicateCheck.textContent = "0 Duplicates (Clean)";
    intDuplicateCheck.className = "val success";
  }

  if (rep.is_valid_stage === false) {
    intStageRule.textContent = "Invalid stage count";
    intStageRule.className = "val fail";
  } else {
    intStageRule.textContent = "Valid (0/3/4/5 cards)";
    intStageRule.className = "val success";
  }

  // Cards detail list
  detectedCardsList.innerHTML = "";
  const details = state.cards_detail || [];
  if (details.length === 0) {
    detectedCardsList.innerHTML = `<p class="empty-state">No community cards currently dealt.</p>`;
  } else {
    details.forEach((c, idx) => {
      const sInfo = SUIT_DISPLAY[c.suit] || { name: c.suit, symbol: "", color: "#fff" };
      const row = document.createElement("div");
      row.className = "card-item-row";
      row.innerHTML = `
        <img src="/static/cards/${c.card}.png" alt="${c.card}">
        <div class="card-item-info">
          <span class="card-item-title">${c.rank} of ${sInfo.name} <span style="color:${sInfo.color}">${sInfo.symbol}</span></span>
          <span class="card-item-meta">Slot ${idx + 1} | Box: [${c.bbox ? c.bbox.join(", ") : ""}]</span>
        </div>
        <div class="card-item-conf">${Math.round((c.confidence || 1) * 100)}% Conf</div>
      `;
      detectedCardsList.appendChild(row);
    });
  }
}

function initCardPackGallery() {
  cardPackGallery.innerHTML = "";
  const ranks = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"];
  const suits = ["s", "h", "d", "c"];

  suits.forEach(s => {
    ranks.forEach(r => {
      const cardStr = `${r}${s}`;
      const img = document.createElement("img");
      img.src = `/static/cards/${cardStr}.png`;
      img.alt = cardStr;
      img.title = cardStr;
      cardPackGallery.appendChild(img);
    });
  });
}

// ---------------------------------------------------------
// Canvas Overlay Rendering
// ---------------------------------------------------------
function loadImageForCanvas(url) {
  const img = new Image();
  img.onload = () => {
    currentImage = img;
    renderCanvasOverlay();
  };
  img.src = url;
}

function renderCanvasOverlay() {
  if (!currentImage) return;

  overlayCanvas.width = currentImage.naturalWidth;
  overlayCanvas.height = currentImage.naturalHeight;

  ctx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
  ctx.drawImage(currentImage, 0, 0);

  const W = overlayCanvas.width;
  const H = overlayCanvas.height;

  // Draw Seat Overlays
  if (chkShowSeats.checked && currentData && currentData.seats) {
    currentData.seats.forEach(s => {
      // 8-max layout geometry normalized
      const SEATS_GEOM = [
        [0.00, 0.25, 0.25, 0.36],
        [0.38, 0.16, 0.62, 0.27],
        [0.75, 0.25, 1.00, 0.36],
        [0.75, 0.36, 1.00, 0.47],
        [0.74, 0.56, 1.00, 0.68],
        [0.03, 0.73, 0.35, 0.86],
        [0.00, 0.56, 0.26, 0.68],
        [0.00, 0.36, 0.26, 0.47]
      ];
      const box = SEATS_GEOM[s.seat_id - 1];
      if (box) {
        const x = box[0] * W;
        const y = box[1] * H;
        const w = (box[2] - box[0]) * W;
        const h = (box[3] - box[1]) * H;

        ctx.strokeStyle = s.is_occupied ? "#00d2d3" : "rgba(255,255,255,0.3)";
        ctx.lineWidth = 2;
        ctx.strokeRect(x, y, w, h);

        ctx.fillStyle = s.is_occupied ? "#00d2d3" : "rgba(255,255,255,0.5)";
        ctx.font = "bold 14px monospace";
        ctx.fillText(`Seat ${s.seat_id}: ${s.username || (s.is_occupied ? 'Occupied' : 'Empty')}`, x + 4, y + 16);
      }
    });
  }

  // Draw Community Cards Overlay
  if (chkShowCards.checked && currentData && currentData.cards_detail) {
    currentData.cards_detail.forEach((c, idx) => {
      if (c.bbox) {
        const [x1, y1, x2, y2] = c.bbox;
        ctx.strokeStyle = "#2ecc71";
        ctx.lineWidth = 3;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

        ctx.fillStyle = "#2ecc71";
        ctx.font = "bold 16px monospace";
        ctx.fillText(`${c.card} (${Math.round((c.confidence || 1) * 100)}%)`, x1, y1 - 6);
      }
    });
  }

  // Draw Pot & Blinds Overlays
  if (chkShowPot.checked) {
    // Pot Box
    const px1 = 0.35 * W, py1 = 0.43 * H, pw = 0.30 * W, ph = 0.05 * H;
    ctx.strokeStyle = "#f39c12";
    ctx.lineWidth = 2;
    ctx.strokeRect(px1, py1, pw, ph);
    ctx.fillStyle = "#f39c12";
    ctx.font = "bold 13px monospace";
    ctx.fillText("POT REGION", px1 + 4, py1 - 4);
  }
}
