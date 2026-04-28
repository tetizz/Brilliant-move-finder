from __future__ import annotations

import io
import json
import os
import sys
import threading
import uuid
import ctypes
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import chess
import chess.pgn
from flask import Flask, jsonify, render_template, request, send_file
import webview

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from brilliant_move_finder.analyzer import board_from_input
    from brilliant_move_finder.classifications import classify_move, get_opening_name
    from brilliant_move_finder.engine import EvalResult, StockfishSession, pv_to_san
    from brilliant_move_finder.logic import BrilliantResult, CancelledError, SearchSettings, find_brilliant_moves
    from brilliant_move_finder.report import export_results_to_json, export_results_to_pgn
else:
    from .analyzer import board_from_input
    from .classifications import classify_move, get_opening_name
    from .engine import EvalResult, StockfishSession, pv_to_san
    from .logic import BrilliantResult, CancelledError, SearchSettings, find_brilliant_moves
    from .report import export_results_to_json, export_results_to_pgn


SOURCE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE_DIR
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
CONFIG_PATH = RUNTIME_DIR / "config.json"
EXPORT_DIR = RUNTIME_DIR / "exports"

DEFAULT_ENGINE_HINTS = [
    RUNTIME_DIR / "stockfish.exe",
    RUNTIME_DIR / "stockfish" / "stockfish-windows-x86-64-avx512icl.exe",
    RUNTIME_DIR / "stockfish" / "stockfish-windows-x86-64-bmi2.exe",
    RUNTIME_DIR / "stockfish" / "stockfish-windows-x86-64-avx2.exe",
    SOURCE_DIR / "stockfish.exe",
    SOURCE_DIR / "stockfish" / "stockfish-windows-x86-64-avx512icl.exe",
    SOURCE_DIR / "stockfish" / "stockfish-windows-x86-64-bmi2.exe",
    SOURCE_DIR / "stockfish" / "stockfish-windows-x86-64-avx2.exe",
    Path(os.environ.get("STOCKFISH_PATH", "")),
]


def _system_total_ram_mb() -> int:
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return max(1024, int(status.ullTotalPhys // (1024 * 1024)))
    except Exception:
        pass
    return 16384


TOTAL_RAM_MB = _system_total_ram_mb()
CPU_THREADS = max(1, os.cpu_count() or 8)
SAFE_THREAD_CAP = max(1, CPU_THREADS)
SAFE_HASH_MB = max(1024, min(65536, (TOTAL_RAM_MB * 3) // 4))
BALANCED_HASH_MB = max(2048, min(8192, TOTAL_RAM_MB // 8))
DEEP_HASH_MB = max(8192, min(24576, TOTAL_RAM_MB // 3))
EXTREME_HASH_MB = max(16384, SAFE_HASH_MB)

PRESET_SETTINGS = {
        "Quick": {
        "threads": max(1, min(8, CPU_THREADS)),
        "hash_mb": 1024,
        "root_depth": 20,
        "shallow_depth": 10,
        "reply_depth": 18,
        "continuation_depth": 18,
        "frontier_width": 2,
        "tree_max_ply": 24,
        "tree_node_cap": 1600,
        "multipv": 3,
        "think_time_ms": 3000,
    },
    "Balanced": {
        "threads": CPU_THREADS,
        "hash_mb": BALANCED_HASH_MB,
        "root_depth": 26,
        "shallow_depth": 12,
        "reply_depth": 22,
        "continuation_depth": 24,
        "frontier_width": 3,
        "tree_max_ply": 36,
        "tree_node_cap": 4000,
        "multipv": 4,
        "think_time_ms": 5000,
    },
    "Deep": {
        "threads": CPU_THREADS,
        "hash_mb": DEEP_HASH_MB,
        "root_depth": 32,
        "shallow_depth": 14,
        "reply_depth": 28,
        "continuation_depth": 30,
        "frontier_width": 4,
        "tree_max_ply": 56,
        "tree_node_cap": 12000,
        "multipv": 5,
        "think_time_ms": 10000,
    },
    "Max RAM": {
        "threads": CPU_THREADS,
        "hash_mb": EXTREME_HASH_MB,
        "root_depth": 34,
        "shallow_depth": 16,
        "reply_depth": 30,
        "continuation_depth": 32,
        "frontier_width": 4,
        "tree_max_ply": 64,
        "tree_node_cap": 18000,
        "multipv": 5,
        "think_time_ms": 20000,
    },
}


def _default_engine_path() -> str:
    for candidate in DEFAULT_ENGINE_HINTS:
        candidate_str = str(candidate).strip()
        if candidate_str and candidate_str not in {".", ""} and candidate.exists():
            return str(candidate.resolve())
    return ""


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(payload: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _result_to_dict(result: BrilliantResult) -> dict[str, Any]:
    data = asdict(result)
    data["line_san"] = " ".join(result.path_san + [result.move_san])
    data["path_label"] = " ".join(result.path_san) if result.path_san else "(starting position)"
    data["pgn_path"] = data.get("pgn_path") or data["line_san"]
    return data


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, board: chess.Board, settings: SearchSettings) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "progress": [],
                "results": [],
                "error": None,
                "board_fen": board.fen(),
                "settings": asdict(settings),
                "cancel_event": threading.Event(),
                "result_count": 0,
            }
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return job

    def append_progress(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["progress"].append(message)
            job["status"] = "running"

    def append_result(self, job_id: str, result: BrilliantResult) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["results"].append(result)
            job["result_count"] = len(job["results"])
            job["status"] = "running"

    def finish(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "done"
            job["result_count"] = len(job["results"])

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job["cancel_event"].set()
            if job["status"] not in {"done", "error", "cancelled"}:
                job["status"] = "cancelling"
            return True

    def mark_cancelled(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "cancelled"

    def mark_error(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "error"
            job["error"] = error

    def public_view(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return {
                "id": job["id"],
                "status": job["status"],
                "progress": list(job["progress"][-120:]),
                "results": [_result_to_dict(result) for result in job["results"]],
                "error": job["error"],
                "board_fen": job["board_fen"],
                "settings": job["settings"],
                "result_count": job["result_count"],
            }


job_store = JobStore()
web_app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / "brilliant_move_finder" / "templates"),
    static_folder=str(RESOURCE_DIR / "brilliant_move_finder" / "static"),
)


def _build_settings(payload: dict[str, Any]) -> SearchSettings:
    return SearchSettings(
        threads=max(1, min(SAFE_THREAD_CAP, int(payload.get("threads", PRESET_SETTINGS["Balanced"]["threads"])))),
        hash_mb=max(128, min(SAFE_HASH_MB, int(payload.get("hash_mb", PRESET_SETTINGS["Balanced"]["hash_mb"])))),
        root_depth=max(1, int(payload.get("root_depth", PRESET_SETTINGS["Balanced"]["root_depth"]))),
        shallow_depth=max(1, int(payload.get("shallow_depth", PRESET_SETTINGS["Balanced"]["shallow_depth"]))),
        reply_depth=max(1, int(payload.get("reply_depth", PRESET_SETTINGS["Balanced"]["reply_depth"]))),
        continuation_depth=max(1, int(payload.get("continuation_depth", PRESET_SETTINGS["Balanced"]["continuation_depth"]))),
        frontier_width=max(1, int(payload.get("frontier_width", PRESET_SETTINGS["Balanced"]["frontier_width"]))),
        tree_max_ply=max(1, int(payload.get("tree_max_ply", PRESET_SETTINGS["Balanced"]["tree_max_ply"]))),
        tree_node_cap=max(1, int(payload.get("tree_node_cap", PRESET_SETTINGS["Balanced"]["tree_node_cap"]))),
        multipv=max(1, int(payload.get("multipv", PRESET_SETTINGS["Balanced"]["multipv"]))),
        think_time_ms=max(250, int(payload.get("think_time_ms", PRESET_SETTINGS["Balanced"]["think_time_ms"]))),
    )


def _eval_to_dict(result: EvalResult) -> dict[str, Any]:
    return {
        "cp": result.cp,
        "score_type": result.score_type,
        "value": result.value,
        "display": _format_eval(result),
    }


def _format_eval(result: EvalResult) -> str:
    if result.score_type == "mate":
        if result.value == 0:
            return "#0"
        return f"{'#' if result.value > 0 else '-#'}{abs(result.value)}"
    return f"{result.value / 100:+.2f}"


def _move_from_payload(board: chess.Board, payload: dict[str, Any]) -> chess.Move | None:
    move_uci = str(payload.get("move_uci", "")).strip()
    if move_uci:
        try:
            move = chess.Move.from_uci(move_uci)
            if move in board.legal_moves:
                return move
        except ValueError:
            return None
    from_square = payload.get("from")
    to_square = payload.get("to")
    if not from_square or not to_square:
        return None
    promo_map = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}
    base_move = chess.Move.from_uci(f"{from_square}{to_square}")
    promotion = promo_map.get(str(payload.get("promotion", "q")).lower(), chess.QUEEN)
    for move in board.legal_moves:
        if move.from_square == base_move.from_square and move.to_square == base_move.to_square:
            if move.promotion is None or move.promotion == promotion:
                return move
    return None


def _line_to_dict(board: chess.Board, result: EvalResult, index: int, classification: dict[str, Any] | None = None) -> dict[str, Any]:
    move = result.pv[0] if result.pv else None
    san = ""
    uci = ""
    if move and move in board.legal_moves:
        san = board.san(move)
        uci = move.uci()
    return {
        "rank": index,
        "move_san": san,
        "move_uci": uci,
        "eval": _eval_to_dict(result),
        "pv_san": pv_to_san(board, result.pv, 14),
        "classification": classification,
    }


def _legal_moves_to_dict(board: chess.Board) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for move in board.legal_moves:
        moves.append(
            {
                "uci": move.uci(),
                "from": chess.square_name(move.from_square),
                "to": chess.square_name(move.to_square),
                "san": board.san(move),
                "promotion": chess.piece_symbol(move.promotion).lower() if move.promotion else "",
                "capture": board.is_capture(move),
                "check": board.gives_check(move),
            }
        )
    return moves


def _classify_line_candidate(board: chess.Board, lines: list[EvalResult], result: EvalResult) -> dict[str, Any] | None:
    if not result.pv:
        return None
    # MultiPV scores are root-position scores for the PV. For candidate labels this is
    # the same comparison Chess.com-style lines use: how much the candidate drops from
    # the best root line.
    current_eval = EvalResult(
        cp=result.cp,
        pv=result.pv[1:],
        score_type=result.score_type,
        value=result.value,
    )
    return classify_move(board, result.pv[0], lines, current_eval).to_dict()


def _database_moves(fen: str, limit: int = 8) -> dict[str, Any]:
    params = urlencode({"fen": fen, "moves": limit})
    endpoints = [
        ("masters", f"https://explorer.lichess.ovh/masters?{params}"),
        ("lichess", f"https://explorer.lichess.ovh/lichess?{params}&speeds=rapid,classical,blitz"),
    ]
    last_error = ""
    for source, url in endpoints:
        try:
            with urlopen(url, timeout=3.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            moves = []
            for move in payload.get("moves", [])[:limit]:
                white = int(move.get("white", 0) or 0)
                draws = int(move.get("draws", 0) or 0)
                black = int(move.get("black", 0) or 0)
                games = white + draws + black
                moves.append(
                    {
                        "uci": move.get("uci", ""),
                        "san": move.get("san", ""),
                        "games": games,
                        "white": white,
                        "draws": draws,
                        "black": black,
                    }
                )
            if moves:
                return {"source": source, "moves": moves, "error": ""}
        except Exception as exc:
            last_error = str(exc)
    return {"source": "fallback", "moves": [], "error": last_error or "No database moves found."}


def _build_analysis_payload(board: chess.Board, session: StockfishSession, settings: SearchSettings) -> dict[str, Any]:
    lines = session.multipv(
        board,
        depth=settings.root_depth,
        lines=settings.multipv,
        movetime_ms=settings.think_time_ms,
    )
    classified_lines = [
        _line_to_dict(board, line, index + 1, _classify_line_candidate(board, lines, line))
        for index, line in enumerate(lines)
    ]
    best_eval = lines[0] if lines else session.analyse(board, settings.root_depth, movetime_ms=settings.think_time_ms)
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "legal_moves": _legal_moves_to_dict(board),
        "legal_move_count": board.legal_moves.count(),
        "is_check": board.is_check(),
        "opening_name": get_opening_name(board.fen()),
        "eval": _eval_to_dict(best_eval),
        "engine_lines": classified_lines,
        "database": _database_moves(board.fen()),
    }


def _scan_worker(job_id: str, engine_path: str, board: chess.Board, settings: SearchSettings) -> None:
    job = job_store.get(job_id)
    assert job is not None
    cancel_event: threading.Event = job["cancel_event"]
    try:
        with StockfishSession(engine_path, hash_mb=settings.hash_mb, threads=settings.threads) as session:
            find_brilliant_moves(
                session.engine,
                board,
                settings,
                cancel_event,
                on_progress=lambda message: job_store.append_progress(job_id, message),
                on_result=lambda result: job_store.append_result(job_id, result),
            )
        if cancel_event.is_set():
            job_store.mark_cancelled(job_id)
        else:
            job_store.finish(job_id)
    except CancelledError:
        job_store.mark_cancelled(job_id)
    except FileNotFoundError:
        job_store.mark_error(job_id, "The Stockfish executable could not be opened.")
    except Exception as exc:
        job_store.mark_error(job_id, str(exc))


@web_app.get("/")
def index() -> str:
    config = _load_config()
    defaults = {
        "engine_path": config.get("engine_path", _default_engine_path()),
        "fen": config.get("fen", ""),
        "moves": config.get("moves", ""),
        "preset": config.get("preset", "Balanced"),
        "settings": {
            key: int(config.get(key, value))
            for key, value in PRESET_SETTINGS["Balanced"].items()
        },
        "presets": PRESET_SETTINGS,
        "hardware": {
            "threads": CPU_THREADS,
            "safe_thread_cap": SAFE_THREAD_CAP,
            "ram_mb": TOTAL_RAM_MB,
            "safe_hash_mb": SAFE_HASH_MB,
        },
    }
    return render_template("index.html", defaults=defaults)


@web_app.post("/api/preview")
def preview() -> Any:
    payload = request.get_json(force=True)
    board = board_from_input(payload.get("fen", ""), payload.get("moves", ""))
    return jsonify(
        {
            "fen": board.fen(),
            "turn": "white" if board.turn == chess.WHITE else "black",
            "san_history": payload.get("moves", "").split(),
            "legal_moves": _legal_moves_to_dict(board),
            "legal_move_count": board.legal_moves.count(),
            "is_check": board.is_check(),
            "opening_name": get_opening_name(board.fen()),
        }
    )


@web_app.post("/api/move")
def apply_move() -> Any:
    payload = request.get_json(force=True)
    board = chess.Board(payload.get("fen", ""))
    from_square = payload.get("from")
    to_square = payload.get("to")
    promotion = payload.get("promotion", "q")
    if not from_square or not to_square:
        return jsonify({"error": "Both from and to squares are required."}), 400

    promo_map = {
        "q": chess.QUEEN,
        "r": chess.ROOK,
        "b": chess.BISHOP,
        "n": chess.KNIGHT,
    }
    base_move = chess.Move.from_uci(f"{from_square}{to_square}")
    legal_move = None
    for move in board.legal_moves:
        if move.from_square == base_move.from_square and move.to_square == base_move.to_square:
            if move.promotion is None:
                legal_move = move
                break
            if move.promotion == promo_map.get(str(promotion).lower(), chess.QUEEN):
                legal_move = move
                break

    if legal_move is None:
        return jsonify({"error": "Illegal move for the current position."}), 400

    san = board.san(legal_move)
    board.push(legal_move)
    return jsonify(
        {
            "fen": board.fen(),
            "san": san,
            "turn": "white" if board.turn == chess.WHITE else "black",
            "legal_moves": _legal_moves_to_dict(board),
            "legal_move_count": board.legal_moves.count(),
            "is_check": board.is_check(),
        }
    )


@web_app.post("/api/analyze-position")
def analyze_position() -> Any:
    payload = request.get_json(force=True)
    engine_path = str(payload.get("engine_path", "")).strip()
    if not engine_path:
        return jsonify({"error": "Stockfish path is required for live analysis."}), 400

    try:
        board = chess.Board(payload.get("fen", ""))
    except ValueError:
        return jsonify({"error": "Invalid FEN for analysis."}), 400

    settings = _build_settings(payload.get("settings", {}))
    move_payload = payload.get("move") or {}
    played_review = None
    played_san = ""
    previous_fen = board.fen()

    try:
        with StockfishSession(engine_path, hash_mb=settings.hash_mb, threads=settings.threads) as session:
            if move_payload:
                move = _move_from_payload(board, move_payload)
                if move is None:
                    return jsonify({"error": "Illegal move for the current position."}), 400

                previous_lines = session.multipv(
                    board,
                    depth=settings.root_depth,
                    lines=settings.multipv,
                    movetime_ms=settings.think_time_ms,
                )
                played_san = board.san(move)
                matching_line = next((line for line in previous_lines if line.pv and line.pv[0] == move), None)
                if matching_line is not None:
                    current_eval = EvalResult(
                        cp=matching_line.cp,
                        pv=matching_line.pv[1:],
                        score_type=matching_line.score_type,
                        value=matching_line.value,
                    )
                else:
                    after = board.copy(stack=False)
                    after.push(move)
                    current_eval = session.analyse(
                        after,
                        settings.shallow_depth,
                        movetime_ms=max(500, min(settings.think_time_ms, 3000)),
                    )

                played_review = classify_move(board, move, previous_lines, current_eval).to_dict()
                board.push(move)

            analysis = _build_analysis_payload(board, session, settings)
            analysis["previous_fen"] = previous_fen
            analysis["played_san"] = played_san
            analysis["played_classification"] = played_review
            analysis["pgn_path"] = payload.get("pgn_path", "")
            return jsonify(analysis)
    except FileNotFoundError:
        return jsonify({"error": "The Stockfish executable could not be opened."}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@web_app.post("/api/parse-pgn")
def parse_pgn() -> Any:
    payload = request.get_json(force=True)
    text = payload.get("text", "")
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None:
        return jsonify({"error": "No PGN game found."}), 400
    board = game.board()
    sans: list[str] = []
    for move in game.mainline_moves():
        sans.append(board.san(move))
        board.push(move)
    return jsonify(
        {
            "moves": " ".join(sans),
            "fen": board.fen(),
            "ply_count": len(sans),
            "turn": "white" if board.turn == chess.WHITE else "black",
            "legal_move_count": board.legal_moves.count(),
            "is_check": board.is_check(),
        }
    )


@web_app.post("/api/scan")
def start_scan() -> Any:
    payload = request.get_json(force=True)
    engine_path = str(payload.get("engine_path", "")).strip()
    if not engine_path:
        return jsonify({"error": "Stockfish path is required."}), 400

    board = board_from_input(payload.get("fen", ""), payload.get("moves", ""))
    settings = _build_settings(payload.get("settings", {}))

    _save_config(
        {
            "engine_path": engine_path,
            "fen": payload.get("fen", ""),
            "moves": payload.get("moves", ""),
            "preset": payload.get("preset", "Balanced"),
            **asdict(settings),
        }
    )

    job_id = job_store.create(board, settings)
    worker = threading.Thread(target=_scan_worker, args=(job_id, engine_path, board, settings), daemon=True)
    worker.start()
    return jsonify({"job_id": job_id})


@web_app.get("/api/jobs/<job_id>")
def get_job(job_id: str) -> Any:
    job = job_store.public_view(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@web_app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id: str) -> Any:
    ok = job_store.cancel(job_id)
    if not ok:
        return jsonify({"error": "Job not found."}), 404
    return jsonify({"ok": True})


@web_app.get("/api/jobs/<job_id>/export/pgn")
def export_job_pgn(job_id: str) -> Any:
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    results: list[BrilliantResult] = job["results"]
    if not results:
        return jsonify({"error": "No brilliant moves to export."}), 400
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"brilliant-moves-{job_id}.pgn"
    export_results_to_pgn(path, chess.Board(job["board_fen"]), results, SearchSettings(**job["settings"]))
    return send_file(path, as_attachment=True, download_name=path.name)


@web_app.get("/api/jobs/<job_id>/export/json")
def export_job_json(job_id: str) -> Any:
    job = job_store.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    results: list[BrilliantResult] = job["results"]
    if not results:
        return jsonify({"error": "No brilliant moves to export."}), 400
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"brilliant-moves-{job_id}.json"
    export_results_to_json(path, chess.Board(job["board_fen"]), results, SearchSettings(**job["settings"]))
    return send_file(path, as_attachment=True, download_name=path.name)


def run_app() -> None:
    port = 8765
    server = threading.Thread(
        target=lambda: web_app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False),
        daemon=True,
    )
    server.start()

    url = f"http://127.0.0.1:{port}/"
    for _ in range(80):
        try:
            with urlopen(url, timeout=0.25) as response:
                if response.status == 200:
                    break
        except URLError:
            pass
        except Exception:
            pass
        threading.Event().wait(0.1)
    else:
        raise RuntimeError("The local GUI server did not start.")

    webview.create_window(
        "Brilliant Move Finder",
        url,
        width=1480,
        height=980,
        min_size=(1120, 760),
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    run_app()
