const defaults = window.APP_DEFAULTS || {};

const PIECES = {
  P: "♙", N: "♘", B: "♗", R: "♖", Q: "♕", K: "♔",
  p: "♟", n: "♞", b: "♝", r: "♜", q: "♛", k: "♚",
};

const files = ["a", "b", "c", "d", "e", "f", "g", "h"];
const ranks = ["8", "7", "6", "5", "4", "3", "2", "1"];

const state = {
  currentFen: "rn1qkbnr/pppb1ppp/8/8/8/8/8/8 w - - 0 1",
  jobId: null,
  pollTimer: null,
  results: [],
  activeIndex: -1,
};

const el = {
  enginePath: document.getElementById("enginePath"),
  fenInput: document.getElementById("fenInput"),
  movesInput: document.getElementById("movesInput"),
  presetSelect: document.getElementById("presetSelect"),
  threadsInput: document.getElementById("threadsInput"),
  hashInput: document.getElementById("hashInput"),
  rootDepthInput: document.getElementById("rootDepthInput"),
  shallowDepthInput: document.getElementById("shallowDepthInput"),
  replyDepthInput: document.getElementById("replyDepthInput"),
  continuationDepthInput: document.getElementById("continuationDepthInput"),
  frontierInput: document.getElementById("frontierInput"),
  treePlyInput: document.getElementById("treePlyInput"),
  treeNodesInput: document.getElementById("treeNodesInput"),
  multipvInput: document.getElementById("multipvInput"),
  previewBtn: document.getElementById("previewBtn"),
  loadPgnBtn: document.getElementById("loadPgnBtn"),
  pgnFileInput: document.getElementById("pgnFileInput"),
  scanBtn: document.getElementById("scanBtn"),
  cancelBtn: document.getElementById("cancelBtn"),
  board: document.getElementById("board"),
  rankLabels: document.getElementById("rankLabels"),
  fileLabels: document.getElementById("fileLabels"),
  boardMeta: document.getElementById("boardMeta"),
  turnBadge: document.getElementById("turnBadge"),
  statusText: document.getElementById("statusText"),
  resultCount: document.getElementById("resultCount"),
  progressLog: document.getElementById("progressLog"),
  resultsList: document.getElementById("resultsList"),
  detailsView: document.getElementById("detailsView"),
  exportPgnBtn: document.getElementById("exportPgnBtn"),
  exportJsonBtn: document.getElementById("exportJsonBtn"),
};

function init() {
  hydrateDefaults();
  renderCoords();
  renderBoard(defaults.settings?.fen || defaults.fen || "startpos");
  wireEvents();
  previewPosition();
}

function hydrateDefaults() {
  el.enginePath.value = defaults.engine_path || "";
  el.fenInput.value = defaults.fen || "";
  el.movesInput.value = defaults.moves || "";

  const presets = defaults.presets || {};
  Object.keys(presets).forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    el.presetSelect.appendChild(option);
  });
  el.presetSelect.value = defaults.preset || "Balanced";
  applyPreset(el.presetSelect.value, false);

  if (defaults.settings) {
    setNumericFields(defaults.settings);
  }
}

function setNumericFields(settings) {
  el.threadsInput.value = settings.threads;
  el.hashInput.value = settings.hash_mb;
  el.rootDepthInput.value = settings.root_depth;
  el.shallowDepthInput.value = settings.shallow_depth;
  el.replyDepthInput.value = settings.reply_depth;
  el.continuationDepthInput.value = settings.continuation_depth;
  el.frontierInput.value = settings.frontier_width;
  el.treePlyInput.value = settings.tree_max_ply;
  el.treeNodesInput.value = settings.tree_node_cap;
  el.multipvInput.value = settings.multipv;
}

function applyPreset(name, announce = true) {
  const preset = defaults.presets?.[name];
  if (!preset) return;
  setNumericFields(preset);
  if (announce) setStatus(`Applied ${name} preset.`);
}

function wireEvents() {
  el.previewBtn.addEventListener("click", previewPosition);
  el.scanBtn.addEventListener("click", startScan);
  el.cancelBtn.addEventListener("click", cancelScan);
  el.presetSelect.addEventListener("change", (event) => applyPreset(event.target.value));
  el.loadPgnBtn.addEventListener("click", () => el.pgnFileInput.click());
  el.pgnFileInput.addEventListener("change", onPgnSelected);
}

async function onPgnSelected(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  const response = await fetch("/api/parse-pgn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const payload = await response.json();
  if (!response.ok) {
    alert(payload.error || "Could not load PGN.");
    return;
  }
  el.fenInput.value = "";
  el.movesInput.value = payload.moves;
  setStatus(`Loaded PGN with ${payload.ply_count} plies.`);
  previewPosition();
}

async function previewPosition() {
  const response = await fetch("/api/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fen: el.fenInput.value.trim(),
      moves: el.movesInput.value.trim(),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    alert(payload.error || "Could not preview position.");
    return;
  }
  state.currentFen = payload.fen;
  renderBoard(payload.fen);
  el.boardMeta.textContent = `${payload.legal_move_count} legal moves${payload.is_check ? " · check" : ""}`;
  el.turnBadge.textContent = payload.turn === "white" ? "White to move" : "Black to move";
}

function renderCoords() {
  el.rankLabels.innerHTML = ranks.map((r) => `<span>${r}</span>`).join("");
  el.fileLabels.innerHTML = files.map((f) => `<span>${f}</span>`).join("");
}

function renderBoard(fen) {
  const position = parseFenBoard(fen);
  el.board.innerHTML = "";
  position.forEach((piece, index) => {
    const square = document.createElement("div");
    const file = index % 8;
    const rank = Math.floor(index / 8);
    square.className = `square ${(file + rank) % 2 === 0 ? "light" : "dark"}`;
    square.textContent = piece ? PIECES[piece] : "";
    el.board.appendChild(square);
  });
}

function parseFenBoard(fen) {
  const boardPart = (fen || "").split(" ")[0];
  if (!boardPart || boardPart === "startpos") {
    return parseFenBoard("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  }
  const squares = [];
  boardPart.split("/").forEach((rank) => {
    rank.split("").forEach((char) => {
      if (/\d/.test(char)) {
        for (let i = 0; i < Number(char); i += 1) squares.push("");
      } else {
        squares.push(char);
      }
    });
  });
  return squares;
}

function collectSettings() {
  return {
    threads: Number(el.threadsInput.value),
    hash_mb: Number(el.hashInput.value),
    root_depth: Number(el.rootDepthInput.value),
    shallow_depth: Number(el.shallowDepthInput.value),
    reply_depth: Number(el.replyDepthInput.value),
    continuation_depth: Number(el.continuationDepthInput.value),
    frontier_width: Number(el.frontierInput.value),
    tree_max_ply: Number(el.treePlyInput.value),
    tree_node_cap: Number(el.treeNodesInput.value),
    multipv: Number(el.multipvInput.value),
  };
}

async function startScan() {
  clearPolling();
  resetResults();
  setStatus("Starting scan...");
  const response = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      engine_path: el.enginePath.value.trim(),
      fen: el.fenInput.value.trim(),
      moves: el.movesInput.value.trim(),
      preset: el.presetSelect.value,
      settings: collectSettings(),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    alert(payload.error || "Could not start scan.");
    return;
  }
  state.jobId = payload.job_id;
  state.pollTimer = window.setInterval(pollJob, 900);
  pollJob();
}

async function pollJob() {
  if (!state.jobId) return;
  const response = await fetch(`/api/jobs/${state.jobId}`);
  const payload = await response.json();
  if (!response.ok) {
    clearPolling();
    alert(payload.error || "Could not fetch job state.");
    return;
  }
  updateJobView(payload);
  if (["done", "cancelled", "error"].includes(payload.status)) {
    clearPolling();
  }
}

async function cancelScan() {
  if (!state.jobId) return;
  await fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
  setStatus("Cancelling search...");
}

function updateJobView(job) {
  setStatus(job.status === "running" ? "Scanning..." : capitalize(job.status));
  el.progressLog.textContent = (job.progress || []).join("\n");
  el.progressLog.scrollTop = el.progressLog.scrollHeight;
  el.resultCount.textContent = `${job.result_count || 0} hit${job.result_count === 1 ? "" : "s"}`;
  renderResults(job.results || []);
  updateExports(job);
  if (job.error) {
    el.detailsView.textContent = job.error;
  }
}

function renderResults(results) {
  state.results = results;
  if (!results.length) {
    el.resultsList.className = "results-list empty-state";
    el.resultsList.textContent = "No brilliant moves yet.";
    if (state.activeIndex === -1) {
      el.detailsView.className = "details empty-state";
      el.detailsView.textContent = "Select a result to inspect the line, flags, and defense tree.";
    }
    return;
  }

  if (state.activeIndex < 0 || state.activeIndex >= results.length) {
    state.activeIndex = 0;
  }

  el.resultsList.className = "results-list";
  el.resultsList.innerHTML = results.map((result, index) => `
    <article class="result-card ${index === state.activeIndex ? "active" : ""}" data-index="${index}">
      <div class="result-title">${escapeHtml(result.move_san)}</div>
      <div class="result-meta">
        <span class="pill">${escapeHtml(result.compensation_type)}</span>
        <span class="pill">${escapeHtml(result.sacrifice_category)}</span>
        <span class="pill">Sac ${escapeHtml(String(result.sacrifice_value))}</span>
      </div>
      <div class="result-meta">
        <span>${escapeHtml(result.path_label)}</span>
      </div>
    </article>
  `).join("");

  el.resultsList.querySelectorAll(".result-card").forEach((node) => {
    node.addEventListener("click", () => {
      state.activeIndex = Number(node.dataset.index);
      renderResults(state.results);
      renderDetails(state.results[state.activeIndex]);
    });
  });

  renderDetails(results[state.activeIndex]);
}

function renderDetails(result) {
  el.detailsView.className = "details";
  const flagLines = Object.entries(result.flags || {})
    .map(([key, value]) => `<div class="pill">${escapeHtml(key)}: ${escapeHtml(String(value))}</div>`)
    .join("");

  el.detailsView.innerHTML = `
    <div class="detail-section">
      <div class="detail-heading">Line</div>
      <div class="detail-code">${escapeHtml(result.line_san)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-heading">Evaluation</div>
      <div class="detail-grid">
        <div class="pill">deep ${escapeHtml(result.eval_cp.toFixed(1))} cp</div>
        <div class="pill">shallow ${escapeHtml(result.shallow_eval_cp.toFixed(1))} cp</div>
        <div class="pill">best defense ${escapeHtml(result.best_defense_eval_cp.toFixed(1))} cp</div>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-heading">Critical Replies</div>
      <div class="detail-grid">
        <div class="pill">defense: ${escapeHtml(result.best_defense_san || "none")}</div>
        <div class="pill">acceptance: ${escapeHtml(result.best_acceptance_san || "none")}</div>
        <div class="pill">decline: ${escapeHtml(result.best_decline_san || "none")}</div>
        <div class="pill">continuation: ${escapeHtml(result.continuation_san || "none")}</div>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-heading">Flags</div>
      <div class="detail-grid">${flagLines}</div>
    </div>
  `;
}

function updateExports(job) {
  if (job.result_count > 0) {
    el.exportPgnBtn.classList.remove("disabled");
    el.exportJsonBtn.classList.remove("disabled");
    el.exportPgnBtn.href = `/api/jobs/${job.id}/export/pgn`;
    el.exportJsonBtn.href = `/api/jobs/${job.id}/export/json`;
  } else {
    el.exportPgnBtn.classList.add("disabled");
    el.exportJsonBtn.classList.add("disabled");
    el.exportPgnBtn.href = "#";
    el.exportJsonBtn.href = "#";
  }
}

function clearPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function resetResults() {
  state.results = [];
  state.activeIndex = -1;
  el.resultsList.className = "results-list empty-state";
  el.resultsList.textContent = "Search running...";
  el.detailsView.className = "details empty-state";
  el.detailsView.textContent = "Waiting for the first brilliant hit.";
  el.progressLog.textContent = "";
  el.resultCount.textContent = "0 hits";
  updateExports({ id: "", result_count: 0 });
}

function setStatus(text) {
  el.statusText.textContent = text;
}

function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

init();
