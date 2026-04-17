from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import chess

from .engine import EvalResult, StockfishSession, pv_to_san


MATE_CP = 100_000
BEST_MOVE_TOLERANCE_CP = 3
OPPONENT_GAIN_TOLERANCE_CP = 5
SURFACE_DROP_CP = 20
RECOVERY_CP = 28
REPLY_DROP_CP = 60
NOT_BAD_AFTER_CP = -40
ALREADY_WINNING_CP = 350

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


@dataclass(slots=True)
class AnalysisConfig:
    scan_depth: int = 12
    confirm_depth: int = 20
    reply_quick_depth: int = 10
    reply_depth: int = 22
    continuation_depth: int = 24
    multipv_frontier: int = 4
    max_deep_replies: int = 6


@dataclass(slots=True)
class SacrificeProfile:
    moved_piece_value: int
    captured_value: int
    is_best_move: bool
    is_real_sacrifice: bool
    is_free_capture: bool
    is_defensive_only: bool
    is_hanging_offer: bool
    looks_losing_initially: bool = False
    category: str = "none"


@dataclass(slots=True)
class BrilliantFlags:
    is_best_move: bool
    is_real_sacrifice: bool
    is_free_capture: bool
    is_defensive_only: bool
    looks_losing_initially: bool
    holds_after_best_defense: bool
    has_forcing_followup: bool
    compensation_type: str


@dataclass(slots=True)
class BrilliantResult:
    move: str
    best_pv: str
    root_eval: float
    move_eval: float
    best_defense: str
    best_acceptance: str
    best_decline: str
    compensation_type: str
    flags: BrilliantFlags
    notes: list[str] = field(default_factory=list)


def cp_to_pawns(cp: int) -> float:
    if abs(cp) >= MATE_CP - 100:
        return cp / 100.0
    return round(cp / 100.0, 2)


def board_from_input(fen: str, moves_text: str) -> chess.Board:
    if fen.strip():
        return chess.Board(fen.strip())
    board = chess.Board()
    tokens = [token for token in moves_text.split() if token.strip()]
    for token in tokens:
        board.push_san(token)
    return board


def piece_value(piece: chess.Piece | None) -> int:
    if piece is None:
        return 0
    return PIECE_VALUES.get(piece.piece_type, 0)


def list_hanging_friendly_pieces(board: chess.Board, color: chess.Color, ignored_squares: Iterable[chess.Square] = ()) -> list[chess.Square]:
    ignored = set(ignored_squares)
    out: list[chess.Square] = []
    for square, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING or square in ignored:
            continue
        if not board.is_attacked_by(not color, square):
            continue
        if not board.is_attacked_by(color, square):
            out.append(square)
    return out


def infer_sacrifice_profile(board: chess.Board, move: chess.Move, best_move: chess.Move) -> SacrificeProfile:
    mover = board.turn
    enemy = not mover
    piece = board.piece_at(move.from_square)
    captured_piece = board.piece_at(move.to_square)
    moved_piece_value = piece_value(piece)
    captured_value = piece_value(captured_piece)

    before_hanging = list_hanging_friendly_pieces(board, mover, ignored_squares=[move.from_square])
    next_board = board.copy(stack=False)
    next_board.push(move)
    after_hanging = list_hanging_friendly_pieces(next_board, mover, ignored_squares=[move.to_square])
    is_defensive_only = len(after_hanging) < len(before_hanging)

    destination_attacked_by_enemy = board.is_attacked_by(enemy, move.to_square)
    destination_defended_by_mover = next_board.is_attacked_by(mover, move.to_square)
    is_free_capture = captured_piece is not None and not destination_attacked_by_enemy
    recapture_exists = any(reply.to_square == move.to_square and next_board.is_capture(reply) for reply in next_board.legal_moves)
    is_hanging_offer = destination_attacked_by_enemy and recapture_exists and not destination_defended_by_mover
    apparent_sac = moved_piece_value > captured_value and is_hanging_offer
    quiet_sac = captured_value == 0 and is_hanging_offer
    is_real_sacrifice = moved_piece_value >= 3 and (apparent_sac or quiet_sac)

    category = "positional"
    if apparent_sac and captured_value > 0:
        category = "deflection"
    elif quiet_sac:
        category = "direct_hanging"

    return SacrificeProfile(
        moved_piece_value=moved_piece_value,
        captured_value=captured_value,
        is_best_move=move == best_move,
        is_real_sacrifice=is_real_sacrifice,
        is_free_capture=is_free_capture,
        is_defensive_only=is_defensive_only,
        is_hanging_offer=is_hanging_offer,
        category=category,
    )


def infer_compensation_type(best_acceptance_followup: str, best_defense_followup: str, best_defense_cp: int, root_cp: int, sac: SacrificeProfile) -> str:
    if best_defense_cp >= MATE_CP - 100:
        return "mate_attack"
    if sac.category == "deflection":
        return "deflection"
    if best_acceptance_followup or best_defense_followup:
        return "material_recovery"
    if best_defense_cp > root_cp + 35:
        return "positional_domination"
    return "none"


def move_from_pv(result: EvalResult) -> chess.Move | None:
    return result.pv[0] if result.pv else None


class BrilliantAnalyzer:
    def __init__(self, session: StockfishSession, config: AnalysisConfig | None = None) -> None:
        self.session = session
        self.config = config or AnalysisConfig()

    def analyze_position(self, board: chess.Board) -> BrilliantResult | None:
        root_eval = self.session.analyse(board, self.config.confirm_depth)
        best_move = move_from_pv(root_eval)
        if best_move is None:
            return None

        sac = infer_sacrifice_profile(board, best_move, best_move)
        if not sac.is_best_move:
            return None
        if not sac.is_real_sacrifice:
            return None
        if sac.is_free_capture:
            return None
        if sac.is_defensive_only:
            return None

        after_best = board.copy(stack=False)
        san_move = after_best.san(best_move)
        after_best.push(best_move)

        shallow = self.session.analyse(after_best, self.config.scan_depth)
        deep = self.session.analyse(after_best, self.config.confirm_depth)
        sac.looks_losing_initially = (
            shallow.cp <= root_eval.cp - SURFACE_DROP_CP
            or deep.cp - shallow.cp >= RECOVERY_CP
            or sac.moved_piece_value >= 3
        )
        if not sac.looks_losing_initially:
            return None
        if deep.cp < root_eval.cp - BEST_MOVE_TOLERANCE_CP:
            return None
        if deep.cp < root_eval.cp - OPPONENT_GAIN_TOLERANCE_CP:
            return None

        replies = list(after_best.legal_moves)
        scored_replies: list[tuple[chess.Move, int, str]] = []
        best_acceptance = ""
        best_decline = ""
        best_acceptance_followup = ""
        best_defense_followup = ""
        best_defense_move = ""
        best_defense_cp = deep.cp

        for reply in replies:
            reply_board = after_best.copy(stack=False)
            reply_san = reply_board.san(reply)
            is_acceptance = reply.to_square == best_move.to_square and reply_board.is_capture(reply)
            reply_board.push(reply)
            quick = self.session.analyse(reply_board, self.config.reply_quick_depth)
            scored_replies.append((reply, quick.cp, reply_san))
            if is_acceptance and not best_acceptance:
                best_acceptance = reply_san

        scored_replies.sort(key=lambda item: item[1])
        critical = scored_replies[: self.config.max_deep_replies]

        for reply, _, reply_san in critical:
            reply_board = after_best.copy(stack=False)
            is_acceptance = reply.to_square == best_move.to_square and reply_board.is_capture(reply)
            is_decline = not is_acceptance
            reply_board.push(reply)
            reply_eval = self.session.analyse(reply_board, self.config.reply_depth)
            continuation_move = move_from_pv(reply_eval)
            continuation_san = ""
            continuation_cp = reply_eval.cp
            if continuation_move is not None:
                follow = reply_board.copy(stack=False)
                continuation_san = follow.san(continuation_move)
                follow.push(continuation_move)
                continuation_eval = self.session.analyse(follow, self.config.continuation_depth)
                continuation_cp = continuation_eval.cp

            if continuation_cp < best_defense_cp:
                best_defense_cp = continuation_cp
                best_defense_move = reply_san
                best_defense_followup = continuation_san

            if is_acceptance and not best_acceptance_followup:
                best_acceptance_followup = continuation_san
            if is_decline and not best_decline:
                best_decline = reply_san

        holds_after_best_defense = (
            best_defense_cp >= deep.cp - REPLY_DROP_CP
            and best_defense_cp >= NOT_BAD_AFTER_CP
            and best_defense_cp >= root_eval.cp - OPPONENT_GAIN_TOLERANCE_CP
        )

        compensation_type = infer_compensation_type(
            best_acceptance_followup,
            best_defense_followup,
            best_defense_cp,
            root_eval.cp,
            sac,
        )
        has_forcing_followup = bool(best_acceptance_followup or best_defense_followup or deep.pv)
        flags = BrilliantFlags(
            is_best_move=True,
            is_real_sacrifice=sac.is_real_sacrifice,
            is_free_capture=sac.is_free_capture,
            is_defensive_only=sac.is_defensive_only,
            looks_losing_initially=sac.looks_losing_initially,
            holds_after_best_defense=holds_after_best_defense,
            has_forcing_followup=has_forcing_followup,
            compensation_type=compensation_type,
        )

        if root_eval.cp >= ALREADY_WINNING_CP:
            return None
        if not (
            flags.is_best_move
            and flags.is_real_sacrifice
            and not flags.is_free_capture
            and not flags.is_defensive_only
            and flags.looks_losing_initially
            and flags.holds_after_best_defense
            and flags.has_forcing_followup
            and flags.compensation_type != "none"
        ):
            return None

        return BrilliantResult(
            move=san_move,
            best_pv=pv_to_san(board, root_eval.pv, 10),
            root_eval=cp_to_pawns(root_eval.cp),
            move_eval=cp_to_pawns(deep.cp),
            best_defense=best_defense_move,
            best_acceptance=best_acceptance,
            best_decline=best_decline,
            compensation_type=compensation_type,
            flags=flags,
            notes=[
                f"best move: {san_move}",
                f"best line: {pv_to_san(board, root_eval.pv, 12)}",
                f"best defense follow-up: {best_defense_followup or 'none'}",
                f"best acceptance follow-up: {best_acceptance_followup or 'none'}",
            ],
        )
