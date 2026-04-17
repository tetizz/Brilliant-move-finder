from __future__ import annotations

import io
import json
import os
import sys
import threading
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import chess
import chess.pgn
from flask import Flask, jsonify, render_template, request, send_file
import webview

from .analyzer import board_from_input
from .engine import StockfishSession
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

PRESET_SETTINGS = {
    "Quick": {
        "threads": max(1, min(8, os.cpu_count() or 8)),
        "hash_mb": 1024,
        "root_depth": 20,
        "shallow_depth": 10,
        "reply_depth": 18,
        "continuation_depth": 18,
        "frontier_width": 2,
        "tree_max_ply": 24,
        "tree_node_cap": 1600,
        "multipv": 3,
    },
    "Balanced": {
        "threads": max(1, os.cpu_count() or 8),
        "hash_mb": 4096,
        "root_depth": 26,
        "shallow_depth": 12,
        "reply_depth": 22,
        "continuation_depth": 24,
        "frontier_width": 3,
        "tree_max_ply": 36,
        "tree_node_cap": 4000,
        "multipv": 4,
    },
    "Deep": {
        "threads": max(1, os.cpu_count() or 8),
        "hash_mb": 8192,
        "root_depth": 32,
        "shallow_depth": 14,
        "reply_depth": 28,
        "continuation_depth": 30,
        "frontier_width": 4,
        "tree_max_ply": 56,
        "tree_node_cap": 12000,
        "multipv": 5,
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
        threads=max(1, int(payload.get("threads", PRESET_SETTINGS["Balanced"]["threads"]))),
        hash_mb=max(128, int(payload.get("hash_mb", PRESET_SETTINGS["Balanced"]["hash_mb"]))),
        root_depth=max(1, int(payload.get("root_depth", PRESET_SETTINGS["Balanced"]["root_depth"]))),
        shallow_depth=max(1, int(payload.get("shallow_depth", PRESET_SETTINGS["Balanced"]["shallow_depth"]))),
        reply_depth=max(1, int(payload.get("reply_depth", PRESET_SETTINGS["Balanced"]["reply_depth"]))),
        continuation_depth=max(1, int(payload.get("continuation_depth", PRESET_SETTINGS["Balanced"]["continuation_depth"]))),
        frontier_width=max(1, int(payload.get("frontier_width", PRESET_SETTINGS["Balanced"]["frontier_width"]))),
        tree_max_ply=max(1, int(payload.get("tree_max_ply", PRESET_SETTINGS["Balanced"]["tree_max_ply"]))),
        tree_node_cap=max(1, int(payload.get("tree_node_cap", PRESET_SETTINGS["Balanced"]["tree_node_cap"]))),
        multipv=max(1, int(payload.get("multipv", PRESET_SETTINGS["Balanced"]["multipv"]))),
    )


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
            "legal_move_count": board.legal_moves.count(),
            "is_check": board.is_check(),
        }
    )


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
    return jsonify({"moves": " ".join(sans), "fen": "", "ply_count": len(sans)})


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
        webbrowser.open(url)
        return

    try:
        window = webview.create_window(
            "Brilliant Move Finder",
            url,
            width=1480,
            height=980,
            min_size=(1120, 760),
            text_select=True,
        )
        webview.start()
    except Exception:
        webbrowser.open(url)
