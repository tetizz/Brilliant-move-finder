from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import chess
import chess.pgn

from .logic import BrilliantResult, SearchSettings


def _sanitize_filename(value: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value.strip())
    out = out.strip("-_")
    return out or "scan"


def default_export_path(prefix: str, suffix: str, directory: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = directory or Path.cwd() / "exports"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{_sanitize_filename(prefix)}-{stamp}.{suffix}"


def _build_game(start_board: chess.Board, result: BrilliantResult, settings: SearchSettings) -> chess.pgn.Game:
    board = start_board.copy(stack=False)
    game = chess.pgn.Game()
    game.headers["Event"] = "Brilliant Move Finder"
    game.headers["Site"] = "Local analysis"
    game.headers["Date"] = datetime.now().strftime("%Y.%m.%d")
    game.headers["White"] = "?"
    game.headers["Black"] = "?"
    game.headers["Result"] = "*"
    game.headers["FEN"] = start_board.fen()
    game.headers["SetUp"] = "1"
    game.headers["BrilliantMove"] = result.move_san
    game.headers["SacrificeType"] = result.sacrifice_category
    game.headers["Compensation"] = result.compensation_type

    node = game
    path_moves = list(result.path_san) + [result.move_san]
    for san in path_moves:
        move = board.parse_san(san)
        node = node.add_main_variation(move)
        board.push(move)

    comment_bits = [
        f"move={result.move_san}",
        f"eval_cp={result.eval_cp:.1f}",
        f"shallow_eval_cp={result.shallow_eval_cp:.1f}",
        f"sacrifice_value={result.sacrifice_value}",
        f"best_defense={result.best_defense_san or 'none'} ({result.best_defense_eval_cp:.1f})",
        f"best_acceptance={result.best_acceptance_san or 'none'} ({result.best_acceptance_eval_cp:.1f})",
        f"best_decline={result.best_decline_san or 'none'} ({result.best_decline_eval_cp:.1f})",
        f"continuation={result.continuation_san or 'none'}",
        f"settings=root:{settings.root_depth},reply:{settings.reply_depth},cont:{settings.continuation_depth}",
    ]
    node.comment = " | ".join(comment_bits)
    return game


def export_results_to_pgn(
    path: str | Path,
    start_board: chess.Board,
    results: list[BrilliantResult],
    settings: SearchSettings,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for index, result in enumerate(results):
            game = _build_game(start_board, result, settings)
            exporter = chess.pgn.FileExporter(handle)
            game.accept(exporter)
            if index != len(results) - 1:
                handle.write("\n")
    return target


def export_results_to_json(
    path: str | Path,
    start_board: chess.Board,
    results: list[BrilliantResult],
    settings: SearchSettings,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start_fen": start_board.fen(),
        "settings": asdict(settings),
        "results": [asdict(result) for result in results],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target
