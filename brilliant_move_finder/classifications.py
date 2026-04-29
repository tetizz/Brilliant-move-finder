from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import chess

from .engine import EvalResult


OPENINGS_PATH = Path(__file__).resolve().parent / "resources" / "openings.json"
POINT_GRADIENT = 0.0035
ALREADY_WINNING_CP = 700
MATE_CP = 100_000

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

SEVERITY = {
    "blunder": 0,
    "mistake": 1,
    "inaccuracy": 2,
    "good": 3,
    "excellent": 4,
    "best": 5,
    "great": 5,
    "brilliant": 5,
    "forced": 5,
    "book": 5,
}

LABELS = {
    "forced": "Forced",
    "book": "Book",
    "best": "Best",
    "excellent": "Excellent",
    "good": "Good",
    "inaccuracy": "Inaccuracy",
    "mistake": "Mistake",
    "blunder": "Blunder",
    "great": "Great",
    "brilliant": "Brilliant",
}

SYMBOLS = {
    "forced": "!",
    "book": "B",
    "best": "*",
    "excellent": "!",
    "good": "+",
    "inaccuracy": "?!",
    "mistake": "?",
    "blunder": "??",
    "great": "!",
    "brilliant": "!!",
}

COLORS = {
    "forced": "#92a2d9",
    "book": "#c79b75",
    "best": "#8bc34a",
    "excellent": "#78c36a",
    "good": "#7fa16b",
    "inaccuracy": "#f6c94f",
    "mistake": "#f2994a",
    "blunder": "#eb5757",
    "great": "#68a6ff",
    "brilliant": "#44d4e8",
}

REASONS = {
    "forced": "Only move available in the position.",
    "book": "Known opening or theory move from the current position.",
    "best": "Engine top move.",
    "excellent": "Very close to the best move.",
    "good": "Solid move with only a small drop.",
    "inaccuracy": "A small but real drop from the strongest move.",
    "mistake": "A meaningful drop from the strongest move.",
    "blunder": "A large drop from the strongest move.",
    "great": "The only move that keeps the advantage.",
    "brilliant": "A hard-to-find, critical move with a sound sacrifice.",
}

_OPENINGS: dict[str, str] | None = None


@dataclass(slots=True)
class ClassificationResult:
    key: str
    label: str
    symbol: str
    color: str
    expected_points: float | None
    expected_points_loss: float | None
    reason: str
    opening_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "symbol": self.symbol,
            "color": self.color,
            "expected_points": self.expected_points,
            "expected_points_loss": self.expected_points_loss,
            "reason": self.reason,
            "opening_name": self.opening_name,
        }


@dataclass(slots=True)
class BoardPiece:
    color: chess.Color
    square: chess.Square
    piece_type: int


def _load_openings() -> dict[str, str]:
    global _OPENINGS
    if _OPENINGS is None:
        if OPENINGS_PATH.exists():
            _OPENINGS = json.loads(OPENINGS_PATH.read_text(encoding="utf-8"))
        else:
            _OPENINGS = {}
    return _OPENINGS


def get_opening_name(fen: str) -> str | None:
    piece_placement = fen.split(" ")[0] if fen else ""
    if not piece_placement:
        return None
    return _load_openings().get(piece_placement)


def _white_subjective_value(evaluation: EvalResult, move_color: chess.Color) -> int:
    return evaluation.value if move_color == chess.WHITE else -evaluation.value


def _piece_value(piece_type: int | None) -> int:
    return PIECE_VALUES.get(piece_type or 0, 0)


def _score_display(evaluation: EvalResult) -> str:
    if evaluation.score_type == "mate":
        if evaluation.value == 0:
            return "#0"
        return f"{'#' if evaluation.value > 0 else '-#'}{abs(evaluation.value)}"
    return f"{evaluation.value / 100:+.2f}"


def get_expected_points(evaluation: EvalResult) -> float:
    if evaluation.score_type == "mate":
        if evaluation.value == 0:
            return 0.5
        return 1.0 if evaluation.value > 0 else 0.0
    return 1.0 / (1.0 + math.exp(-POINT_GRADIENT * evaluation.value))


def get_expected_points_loss(previous: EvalResult, current: EvalResult, move_color: chess.Color) -> float:
    prev_points = get_expected_points(previous)
    current_points = get_expected_points(current)
    if move_color == chess.WHITE:
        return max(0.0, prev_points - current_points)
    return max(0.0, (1.0 - prev_points) - (1.0 - current_points))


def _point_loss_classify(previous: EvalResult, current: EvalResult, move_color: chess.Color) -> str:
    previous_subjective = _white_subjective_value(previous, move_color)
    subjective_value = _white_subjective_value(current, move_color)

    if previous.score_type == "mate" and current.score_type == "mate":
        if previous_subjective > 0 and subjective_value < 0:
            return "blunder" if subjective_value < -3 else "mistake"

        mate_loss = (current.value - previous.value) * (1 if move_color == chess.WHITE else -1)
        if mate_loss < 0 or (mate_loss == 0 and subjective_value < 0):
            return "best"
        if mate_loss < 2:
            return "excellent"
        if mate_loss < 7:
            return "good"
        return "inaccuracy"

    if previous.score_type == "mate" and current.score_type == "centipawn":
        if subjective_value >= 800:
            return "excellent"
        if subjective_value >= 400:
            return "good"
        if subjective_value >= 200:
            return "inaccuracy"
        if subjective_value >= 0:
            return "mistake"
        return "blunder"

    if previous.score_type == "centipawn" and current.score_type == "mate":
        if subjective_value > 0:
            return "best"
        if subjective_value >= -2:
            return "blunder"
        if subjective_value >= -5:
            return "mistake"
        return "inaccuracy"

    point_loss = get_expected_points_loss(previous, current, move_color)
    if point_loss < 0.01:
        return "best"
    if point_loss < 0.045:
        return "excellent"
    if point_loss < 0.08:
        return "good"
    if point_loss < 0.12:
        return "inaccuracy"
    if point_loss < 0.22:
        return "mistake"
    return "blunder"


def _board_piece(board: chess.Board, square: chess.Square) -> BoardPiece | None:
    piece = board.piece_at(square)
    if piece is None:
        return None
    return BoardPiece(color=piece.color, square=square, piece_type=piece.piece_type)


def _board_pieces(board: chess.Board) -> list[BoardPiece]:
    return [
        BoardPiece(color=piece.color, square=square, piece_type=piece.piece_type)
        for square, piece in board.piece_map().items()
    ]


def _attackers(board: chess.Board, piece: BoardPiece) -> list[BoardPiece]:
    return [
        bp
        for sq in board.attackers(not piece.color, piece.square)
        if (bp := _board_piece(board, sq)) is not None
    ]


def _defenders(board: chess.Board, piece: BoardPiece) -> list[BoardPiece]:
    return [
        bp
        for sq in board.attackers(piece.color, piece.square)
        if (bp := _board_piece(board, sq)) is not None
    ]


def is_piece_safe(board: chess.Board, piece: BoardPiece, played_move: chess.Move | None = None) -> bool:
    direct_attackers = _attackers(board, piece)
    attackers = direct_attackers
    defenders = _defenders(board, piece)

    if (
        played_move is not None
        and played_move.to_square == piece.square
        and piece.piece_type == chess.ROOK
    ):
        captured_piece = board.piece_at(piece.square)
        if (
            captured_piece is not None
            and _piece_value(captured_piece.piece_type) == _piece_value(chess.KNIGHT)
            and len(attackers) == 1
            and len(defenders) > 0
            and attackers[0].piece_type == chess.KNIGHT
        ):
            return True

    if any(_piece_value(attacker.piece_type) < _piece_value(piece.piece_type) for attacker in direct_attackers):
        return False

    if len(attackers) <= len(defenders):
        return True

    if not direct_attackers:
        return True

    lowest_attacker = min(direct_attackers, key=lambda attacker: _piece_value(attacker.piece_type))
    if (
        _piece_value(piece.piece_type) < _piece_value(lowest_attacker.piece_type)
        and any(_piece_value(defender.piece_type) < _piece_value(lowest_attacker.piece_type) for defender in defenders)
    ):
        return True

    if any(defender.piece_type == chess.PAWN for defender in defenders):
        return True

    return False


def get_unsafe_pieces(board: chess.Board, color: chess.Color, played_move: chess.Move | None = None) -> list[BoardPiece]:
    captured_piece = board.piece_at(played_move.to_square) if played_move else None
    captured_piece_value = _piece_value(captured_piece.piece_type if captured_piece else None)
    return [
        piece
        for piece in _board_pieces(board)
        if piece.color == color
        and piece.piece_type not in {chess.PAWN, chess.KING}
        and _piece_value(piece.piece_type) > captured_piece_value
        and not is_piece_safe(board, piece, played_move)
    ]


def _is_free_capture(previous_board: chess.Board, move: chess.Move) -> bool:
    captured_piece = previous_board.piece_at(move.to_square)
    return captured_piece is not None and not previous_board.is_attacked_by(not previous_board.turn, move.to_square)


def _is_critical_candidate(
    previous_board: chess.Board,
    previous_eval: EvalResult,
    current_eval: EvalResult,
    second_best_eval: EvalResult | None,
    move: chess.Move,
    move_color: chess.Color,
) -> bool:
    second_subjective = _white_subjective_value(second_best_eval, move_color) if second_best_eval else _white_subjective_value(current_eval, move_color)
    if second_best_eval and second_best_eval.score_type == "centipawn" and second_subjective >= ALREADY_WINNING_CP:
        return False
    if second_best_eval is None and current_eval.score_type == "centipawn" and _white_subjective_value(current_eval, move_color) >= ALREADY_WINNING_CP:
        return False
    if _white_subjective_value(current_eval, move_color) < 0:
        return False
    if move.promotion == chess.QUEEN:
        return False
    if previous_board.is_check():
        return False
    return True


def _consider_critical(
    previous_board: chess.Board,
    current_board: chess.Board,
    previous_eval: EvalResult,
    current_eval: EvalResult,
    second_best_eval: EvalResult | None,
    move: chess.Move,
    move_color: chess.Color,
) -> bool:
    if not _is_critical_candidate(previous_board, previous_eval, current_eval, second_best_eval, move, move_color):
        return False
    if current_eval.score_type == "mate" and _white_subjective_value(current_eval, move_color) > 0:
        return False
    if move.promotion == chess.QUEEN:
        return False
    if previous_board.is_capture(move):
        captured_square = move.to_square
        captured_piece = _board_piece(previous_board, captured_square)
        if captured_piece is not None and not is_piece_safe(previous_board, captured_piece):
            return False
    if second_best_eval is None:
        return False
    second_loss = get_expected_points_loss(previous_eval, second_best_eval, move_color)
    return second_loss >= 0.1


def _move_is_real_sacrifice(previous_board: chess.Board, move: chess.Move) -> bool:
    piece = previous_board.piece_at(move.from_square)
    captured = previous_board.piece_at(move.to_square)
    if piece is None or piece.piece_type in {chess.PAWN, chess.KING}:
        return False
    moved_value = _piece_value(piece.piece_type)
    captured_value = _piece_value(captured.piece_type if captured else None)
    board_after = previous_board.copy(stack=False)
    board_after.push(move)
    recapture_exists = any(reply.to_square == move.to_square for reply in board_after.legal_moves)
    destination_attacked = previous_board.is_attacked_by(not previous_board.turn, move.to_square)
    destination_defended = board_after.is_attacked_by(previous_board.turn, move.to_square)
    apparent_sac = moved_value > captured_value and destination_attacked and recapture_exists
    quiet_sac = captured is None and destination_attacked and recapture_exists and not destination_defended
    return moved_value >= 3 and (apparent_sac or quiet_sac)


def _move_preserves_active_piece_sacrifice(
    previous_board: chess.Board,
    current_board: chess.Board,
    move: chess.Move,
    move_color: chess.Color,
) -> bool:
    moved_piece = previous_board.piece_at(move.from_square)
    if moved_piece is None or moved_piece.piece_type != chess.PAWN:
        return False
    previous_unsafe = get_unsafe_pieces(previous_board, move_color)
    current_unsafe = get_unsafe_pieces(current_board, move_color, move)
    if not current_unsafe:
        return False
    previous_unsafe_squares = {piece.square for piece in previous_unsafe}
    for piece in current_unsafe:
        if piece.piece_type in {chess.PAWN, chess.KING}:
            continue
        if _piece_value(piece.piece_type) < 3:
            continue
        if piece.square in previous_unsafe_squares or previous_unsafe:
            return True
    return False


def _consider_brilliant(
    previous_board: chess.Board,
    current_board: chess.Board,
    previous_eval: EvalResult,
    current_eval: EvalResult,
    second_best_eval: EvalResult | None,
    move: chess.Move,
    move_color: chess.Color,
) -> bool:
    if not _is_critical_candidate(previous_board, previous_eval, current_eval, second_best_eval, move, move_color):
        return False
    is_direct_sacrifice = _move_is_real_sacrifice(previous_board, move)
    is_sacrificial_followup = _move_preserves_active_piece_sacrifice(
        previous_board,
        current_board,
        move,
        move_color,
    )
    if not (is_direct_sacrifice or is_sacrificial_followup):
        return False

    previous_unsafe = get_unsafe_pieces(previous_board, move_color)
    current_unsafe = get_unsafe_pieces(current_board, move_color, move)

    if not current_board.is_check() and len(current_unsafe) < len(previous_unsafe):
        return False
    if not current_unsafe:
        return False
    if _white_subjective_value(previous_eval, move_color) >= ALREADY_WINNING_CP:
        return False
    return True


def classify_move(
    previous_board: chess.Board,
    move: chess.Move,
    previous_lines: list[EvalResult],
    current_eval: EvalResult,
) -> ClassificationResult:
    move_color = previous_board.turn
    current_board = previous_board.copy(stack=False)
    current_board.push(move)

    previous_eval = previous_lines[0] if previous_lines else current_eval
    second_best_eval = previous_lines[1] if len(previous_lines) > 1 else None
    top_move = previous_lines[0].pv[0] if previous_lines and previous_lines[0].pv else None
    top_move_played = top_move == move if top_move is not None else False

    opening_name = get_opening_name(current_board.fen())
    expected_points = get_expected_points(current_eval)
    expected_points_loss = get_expected_points_loss(previous_eval, current_eval, move_color)

    if previous_board.legal_moves.count() <= 1:
        key = "forced"
    elif current_board.is_checkmate():
        key = "best"
    else:
        key = "best" if top_move_played else _point_loss_classify(previous_eval, current_eval, move_color)
        if opening_name and key in {"best", "excellent", "good"}:
            key = "book"
        elif top_move_played and _consider_critical(
            previous_board,
            current_board,
            previous_eval,
            current_eval,
            second_best_eval,
            move,
            move_color,
        ):
            key = "great"
        if SEVERITY[key] >= SEVERITY["best"] and _consider_brilliant(
            previous_board,
            current_board,
            previous_eval,
            current_eval,
            second_best_eval,
            move,
            move_color,
        ):
            key = "brilliant"

    reason = REASONS[key]
    if key == "book" and opening_name:
        reason = f"Known theory move in {opening_name}."
    elif key in {"best", "excellent", "good", "inaccuracy", "mistake", "blunder"}:
        reason = f"{REASONS[key]} Eval after the move: {_score_display(current_eval)}."

    return ClassificationResult(
        key=key,
        label=LABELS[key],
        symbol=SYMBOLS[key],
        color=COLORS[key],
        expected_points=round(expected_points, 4),
        expected_points_loss=round(expected_points_loss, 4),
        reason=reason,
        opening_name=opening_name,
    )


def classify_scan_candidate(
    previous_board: chess.Board,
    move: chess.Move,
    previous_lines: list[EvalResult],
    current_eval: EvalResult,
    sacrifice_like: bool,
) -> tuple[str, str]:
    classification = classify_move(previous_board, move, previous_lines, current_eval)
    if classification.key == "brilliant":
        return "high", classification.key
    if sacrifice_like and classification.key in {"great", "best", "excellent", "good"}:
        return "low", classification.key
    return "", classification.key
