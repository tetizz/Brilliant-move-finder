const defaults = window.APP_DEFAULTS || {};

const PIECE_ASSETS = {
  P: "/static/pieces/cburnett/wP.svg",
  N: "/static/pieces/cburnett/wN.svg",
  B: "/static/pieces/cburnett/wB.svg",
  R: "/static/pieces/cburnett/wR.svg",
  Q: "/static/pieces/cburnett/wQ.svg",
  K: "/static/pieces/cburnett/wK.svg",
  p: "/static/pieces/cburnett/bP.svg",
  n: "/static/pieces/cburnett/bN.svg",
  b: "/static/pieces/cburnett/bB.svg",
  r: "/static/pieces/cburnett/bR.svg",
  q: "/static/pieces/cburnett/bQ.svg",
  k: "/static/pieces/cburnett/bK.svg",
};

const files = ["a", "b", "c", "d", "e", "f", "g", "h"];
const ranks = ["8", "7", "6", "5", "4", "3", "2", "1"];

const state = {
  currentFen: "startpos",
  jobId: null,
  pollTimer: null,
  results: [],
  activeIndex: -1,
  orientation: "white",
  selectedSquare: null,
  lastMoveSquares: null,
  draggingFrom: null,
  dragPiece: null,
  setupMode: false,
  editorPiece: null,
  editorTurn: "w",
};

const el = {
  enginePath: document.getElementById("enginePath"),
  fenInput: document.getElementById("fenInput"),
  movesInput: document.getElementById("movesInput"),
  presetSelect: document.getElementById("presetSelect"),
  hardwareNote: document.getElementById("hardwareNote"),
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
  startPosBtn: document.getElementById("startPosBtn"),
  clearBoardBtn: document.getElementById("clearBoardBtn"),
  flipBoardBtn: document.getElementById("flipBoardBtn"),
  setupModeBtn: document.getElementById("setupModeBtn"),
  turnToggleBtn: document.getElementById("turnToggleBtn"),
  piecePalette: document.getElementById("piecePalette"),
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
  renderPiecePalette();
  renderCoords();
  renderBoard(defaults.fen || "startpos");
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
  renderHardwareNote();

  if (defaults.settings) {
    setNumericFields(defaults.settings);
    clampHardwareInputs(false);
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
  clampHardwareInputs(false);
  if (announce) setStatus(`Applied ${name} preset.`);
}

function renderHardwareNote() {
  const hardware = defaults.hardware || {};
  const ramGb = hardware.ram_mb ? (hardware.ram_mb / 1024).toFixed(1) : "?";
  const safeHashGb = hardware.safe_hash_mb ? (hardware.safe_hash_mb / 1024).toFixed(1) : "?";
  el.hardwareNote.textContent = `Detected ${hardware.threads || "?"} CPU threads and ${ramGb} GB RAM. Safe caps: ${hardware.safe_thread_cap || "?"} threads and ${safeHashGb} GB Stockfish hash.`;
}

function clampHardwareInputs(announce = true) {
  const hardware = defaults.hardware || {};
  const maxThreads = Number(hardware.safe_thread_cap || 1);
  const maxHash = Number(hardware.safe_hash_mb || 1024);
  let clamped = false;

  if (Number(el.threadsInput.value) > maxThreads) {
    el.threadsInput.value = maxThreads;
    clamped = true;
  }
  if (Number(el.hashInput.value) > maxHash) {
    el.hashInput.value = maxHash;
    clamped = true;
  }

  if (announce && clamped) {
    setStatus(`Clamped engine settings to safe hardware limits: ${maxThreads} threads, ${maxHash} MB hash.`);
  }
}

function wireEvents() {
  el.previewBtn.addEventListener("click", previewPosition);
  el.scanBtn.addEventListener("click", startScan);
  el.cancelBtn.addEventListener("click", cancelScan);
  el.startPosBtn.addEventListener("click", setStartPosition);
  el.clearBoardBtn.addEventListener("click", clearBoard);
  el.flipBoardBtn.addEventListener("click", flipBoard);
  el.setupModeBtn.addEventListener("click", toggleSetupMode);
  el.turnToggleBtn.addEventListener("click", toggleEditorTurn);
  el.presetSelect.addEventListener("change", (event) => applyPreset(event.target.value));
  el.threadsInput.addEventListener("change", () => clampHardwareInputs());
  el.hashInput.addEventListener("change", () => clampHardwareInputs());
  el.loadPgnBtn.addEventListener("click", () => el.pgnFileInput.click());
  el.pgnFileInput.addEventListener("change", onPgnSelected);
  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
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
  el.fenInput.value = payload.fen || "";
  el.movesInput.value = payload.moves;
  if (payload.fen) {
    state.currentFen = payload.fen;
    state.editorTurn = payload.turn === "black" ? "b" : "w";
    state.lastMoveSquares = null;
    renderBoard(payload.fen);
    el.boardMeta.textContent = `${payload.legal_move_count} legal moves${payload.is_check ? " | check" : ""}`;
    el.turnBadge.textContent = payload.turn === "white" ? "White to move" : "Black to move";
    syncTurnButton();
  }
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
  state.editorTurn = parseFenState(payload.fen).turn;
  state.lastMoveSquares = null;
  renderBoard(payload.fen);
  el.boardMeta.textContent = `${payload.legal_move_count} legal moves${payload.is_check ? " | check" : ""}`;
  el.turnBadge.textContent = payload.turn === "white" ? "White to move" : "Black to move";
  syncTurnButton();
}

function renderCoords() {
  const shownRanks = state.orientation === "white" ? ranks : [...ranks].reverse();
  const shownFiles = state.orientation === "white" ? files : [...files].reverse();
  el.rankLabels.innerHTML = shownRanks.map((r) => `<span>${r}</span>`).join("");
  el.fileLabels.innerHTML = shownFiles.map((f) => `<span>${f}</span>`).join("");
}

function renderBoard(fen) {
  const position = parseFenState(fen).squares;
  el.board.innerHTML = "";
  const ordered = orientedBoard(position);
  ordered.forEach(({ piece, squareName }, index) => {
    const square = document.createElement("div");
    const file = index % 8;
    const rank = Math.floor(index / 8);
    square.className = `square ${(file + rank) % 2 === 0 ? "light" : "dark"}`;
    square.dataset.square = squareName;
    if (state.lastMoveSquares && state.lastMoveSquares.includes(squareName)) {
      square.classList.add("last-move");
    }
    if (state.selectedSquare === squareName) {
      square.classList.add("selected");
    }
    square.addEventListener("dragover", (event) => {
      event.preventDefault();
      square.classList.add("drag-over");
    });
    square.addEventListener("dragleave", () => square.classList.remove("drag-over"));
    square.addEventListener("drop", async (event) => {
      event.preventDefault();
      square.classList.remove("drag-over");
      const palettePiece = event.dataTransfer.getData("application/x-piece");
      const fromSquare = event.dataTransfer.getData("text/plain") || state.draggingFrom;
      if (state.setupMode) {
        if (palettePiece) {
          applyEditorPiece(squareName, palettePiece === "erase" ? "" : palettePiece);
        } else if (fromSquare) {
          moveEditorPiece(fromSquare, squareName);
        }
      } else if (fromSquare) {
        await tryBoardMove(fromSquare, squareName);
      }
      state.draggingFrom = null;
    });
    square.addEventListener("click", async () => handleSquareClick(squareName));
    if (piece) {
      const inner = document.createElement("img");
      inner.className = "piece";
      inner.src = PIECE_ASSETS[piece] || "";
      inner.alt = piece;
      inner.draggable = true;
      inner.dataset.square = squareName;
      inner.addEventListener("dragstart", (event) => {
        state.draggingFrom = squareName;
        event.dataTransfer.setData("text/plain", squareName);
        inner.classList.add("dragging");
      });
      inner.addEventListener("dragend", () => {
        state.draggingFrom = null;
        inner.classList.remove("dragging");
      });
      inner.addEventListener("pointerdown", (event) => beginPointerDrag(event, squareName, piece));
      square.appendChild(inner);
    }
    el.board.appendChild(square);
  });
}

function beginPointerDrag(event, squareName, piece) {
  if (event.button !== 0) return;
  const ghost = document.createElement("img");
  ghost.className = "piece pointer-ghost";
  ghost.src = PIECE_ASSETS[piece] || "";
  ghost.alt = piece;
  ghost.style.left = `${event.clientX}px`;
  ghost.style.top = `${event.clientY}px`;
  document.body.appendChild(ghost);
  state.draggingFrom = squareName;
  state.dragPiece = { ghost, piece, source: squareName };
  event.currentTarget.classList.add("dragging");
  event.preventDefault();
}

function onPointerMove(event) {
  if (!state.dragPiece) return;
  state.dragPiece.ghost.style.left = `${event.clientX}px`;
  state.dragPiece.ghost.style.top = `${event.clientY}px`;
  const square = squareFromPoint(event.clientX, event.clientY);
  highlightDropSquare(square);
}

async function onPointerUp(event) {
  if (!state.dragPiece) return;
  const sourceSquare = state.dragPiece.source;
  cleanupPointerDrag();
  const targetSquare = squareFromPoint(event.clientX, event.clientY);
  if (!targetSquare || targetSquare === sourceSquare) return;
  if (state.setupMode) {
    moveEditorPiece(sourceSquare, targetSquare);
  } else {
    await tryBoardMove(sourceSquare, targetSquare);
  }
}

function cleanupPointerDrag() {
  if (!state.dragPiece) return;
  state.dragPiece.ghost.remove();
  document.querySelectorAll(".piece.dragging").forEach((piece) => piece.classList.remove("dragging"));
  highlightDropSquare(null);
  state.dragPiece = null;
  state.draggingFrom = null;
}

function squareFromPoint(x, y) {
  const element = document.elementFromPoint(x, y);
  if (!element) return null;
  const square = element.closest(".square");
  return square ? square.dataset.square : null;
}

function highlightDropSquare(squareName) {
  document.querySelectorAll(".square.drag-over").forEach((square) => square.classList.remove("drag-over"));
  if (!squareName) return;
  const square = el.board.querySelector(`.square[data-square="${squareName}"]`);
  if (square) square.classList.add("drag-over");
}

function orientedBoard(position) {
  const mapped = position.map((piece, index) => {
    const fileIndex = index % 8;
    const rankIndex = Math.floor(index / 8);
    return {
      piece,
      squareName: `${files[fileIndex]}${8 - rankIndex}`,
    };
  });
  if (state.orientation === "white") {
    return mapped;
  }
  const reversed = [];
  for (let rank = 7; rank >= 0; rank -= 1) {
    for (let file = 7; file >= 0; file -= 1) {
      reversed.push(mapped[rank * 8 + file]);
    }
  }
  return reversed;
}

function parseFenBoard(fen) {
  return parseFenState(fen).squares;
}

function parseFenState(fen) {
  const parts = (fen || "").trim().split(/\s+/);
  const boardPart = parts[0];
  if (!boardPart || boardPart === "startpos") {
    return parseFenState("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
  }
  const squares = [];
  boardPart.split("/").forEach((rank) => {
    rank.split("").forEach((char) => {
      if (/\d/.test(char)) {
        for (let i = 0; i < Number(char); i += 1) {
          squares.push("");
        }
      } else {
        squares.push(char);
      }
    });
  });
  return {
    squares,
    turn: parts[1] === "b" ? "b" : "w",
  };
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

async function handleSquareClick(squareName) {
  if (state.setupMode) {
    if (state.editorPiece !== null) {
      applyEditorPiece(squareName, state.editorPiece === "erase" ? "" : state.editorPiece);
      return;
    }
    if (pieceAtSquare(state.currentFen, squareName)) {
      state.selectedSquare = squareName;
      renderBoard(state.currentFen);
    }
    return;
  }
  if (!state.selectedSquare) {
    if (pieceAtSquare(state.currentFen, squareName)) {
      state.selectedSquare = squareName;
      renderBoard(state.currentFen);
    }
    return;
  }
  const fromSquare = state.selectedSquare;
  state.selectedSquare = null;
  renderBoard(state.currentFen);
  if (fromSquare !== squareName) {
    await tryBoardMove(fromSquare, squareName);
  }
}

async function tryBoardMove(fromSquare, toSquare) {
  const response = await fetch("/api/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fen: state.currentFen,
      from: fromSquare,
      to: toSquare,
      promotion: "q",
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    setStatus(payload.error || "Illegal move.");
    return;
  }
  state.currentFen = payload.fen;
  state.editorTurn = payload.turn === "white" ? "w" : "b";
  state.lastMoveSquares = [fromSquare, toSquare];
  el.fenInput.value = payload.fen;
  el.movesInput.value = "";
  renderBoard(payload.fen);
  el.boardMeta.textContent = `${payload.legal_move_count} legal moves${payload.is_check ? " | check" : ""}`;
  el.turnBadge.textContent = payload.turn === "white" ? "White to move" : "Black to move";
  syncTurnButton();
  setStatus(`Played ${payload.san}`);
}

function pieceAtSquare(fen, squareName) {
  const squares = parseFenBoard(fen);
  const fileIndex = files.indexOf(squareName[0]);
  const rankIndex = 8 - Number(squareName[1]);
  return squares[rankIndex * 8 + fileIndex];
}

function setStartPosition() {
  state.selectedSquare = null;
  state.editorTurn = "w";
  state.currentFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  state.lastMoveSquares = null;
  el.fenInput.value = state.currentFen;
  el.movesInput.value = "";
  renderBoard(state.currentFen);
  el.boardMeta.textContent = "20 legal moves";
  el.turnBadge.textContent = "White to move";
  syncTurnButton();
  setStatus("Reset to the starting position.");
}

function clearBoard() {
  state.selectedSquare = null;
  state.editorTurn = "w";
  state.currentFen = "8/8/8/8/8/8/8/8 w - - 0 1";
  state.lastMoveSquares = null;
  el.fenInput.value = state.currentFen;
  el.movesInput.value = "";
  renderBoard(state.currentFen);
  el.boardMeta.textContent = "0 legal moves";
  el.turnBadge.textContent = "White to move";
  syncTurnButton();
  setStatus("Cleared the board.");
}

function flipBoard() {
  state.orientation = state.orientation === "white" ? "black" : "white";
  renderCoords();
  renderBoard(state.currentFen);
  setStatus(`Flipped board to ${state.orientation} view.`);
}

function toggleSetupMode() {
  state.setupMode = !state.setupMode;
  state.selectedSquare = null;
  el.setupModeBtn.textContent = `Setup Mode: ${state.setupMode ? "On" : "Off"}`;
  el.setupModeBtn.classList.toggle("active", state.setupMode);
  renderBoard(state.currentFen);
  renderPiecePalette();
  setStatus(state.setupMode ? "Setup mode enabled. Drag pieces from the palette or move pieces freely." : "Setup mode disabled. Legal move mode restored.");
}

function toggleEditorTurn() {
  state.editorTurn = state.editorTurn === "w" ? "b" : "w";
  writeEditorFen(parseFenState(state.currentFen).squares);
  syncTurnButton();
  el.turnBadge.textContent = state.editorTurn === "w" ? "White to move" : "Black to move";
  setStatus(`Side to move set to ${state.editorTurn === "w" ? "white" : "black"}.`);
}

function syncTurnButton() {
  el.turnToggleBtn.textContent = `Side to move: ${state.editorTurn === "w" ? "White" : "Black"}`;
}

function renderPiecePalette() {
  const palettePieces = ["K", "Q", "R", "B", "N", "P", "erase", "k", "q", "r", "b", "n", "p"];
  el.piecePalette.innerHTML = "";
  palettePieces.forEach((piece) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `palette-piece${piece === "erase" ? " eraser" : ""}`;
    btn.classList.toggle("active", state.editorPiece === piece);
    btn.draggable = true;
    btn.addEventListener("click", () => {
      state.editorPiece = state.editorPiece === piece ? null : piece;
      renderPiecePalette();
    });
    btn.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("application/x-piece", piece);
      state.editorPiece = piece;
      renderPiecePalette();
    });
    if (piece === "erase") {
      btn.textContent = "×";
      btn.title = "Remove piece";
    } else {
      const img = document.createElement("img");
      img.src = PIECE_ASSETS[piece];
      img.alt = piece;
      btn.appendChild(img);
    }
    el.piecePalette.appendChild(btn);
  });
}

function moveEditorPiece(fromSquare, toSquare) {
  const fenState = parseFenState(state.currentFen);
  const fromIndex = squareIndex(fromSquare);
  const toIndex = squareIndex(toSquare);
  fenState.squares[toIndex] = fenState.squares[fromIndex];
  fenState.squares[fromIndex] = "";
  state.lastMoveSquares = [fromSquare, toSquare];
  writeEditorFen(fenState.squares);
}

function applyEditorPiece(squareName, piece) {
  const fenState = parseFenState(state.currentFen);
  fenState.squares[squareIndex(squareName)] = piece;
  state.lastMoveSquares = [squareName];
  writeEditorFen(fenState.squares);
}

function writeEditorFen(squares) {
  state.currentFen = buildFenFromSquares(squares, state.editorTurn);
  el.fenInput.value = state.currentFen;
  el.movesInput.value = "";
  renderBoard(state.currentFen);
  el.boardMeta.textContent = "Custom setup position";
  el.turnBadge.textContent = state.editorTurn === "w" ? "White to move" : "Black to move";
}

function squareIndex(squareName) {
  const fileIndex = files.indexOf(squareName[0]);
  const rankIndex = 8 - Number(squareName[1]);
  return rankIndex * 8 + fileIndex;
}

function buildFenFromSquares(squares, turn) {
  const ranksOut = [];
  for (let rank = 0; rank < 8; rank += 1) {
    let empty = 0;
    let text = "";
    for (let file = 0; file < 8; file += 1) {
      const piece = squares[(rank * 8) + file];
      if (!piece) {
        empty += 1;
      } else {
        if (empty) text += String(empty);
        empty = 0;
        text += piece;
      }
    }
    if (empty) text += String(empty);
    ranksOut.push(text);
  }
  return `${ranksOut.join("/")} ${turn} - - 0 1`;
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
    el.detailsView.className = "details";
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
    .replaceAll("\"", "&quot;");
}

init();
