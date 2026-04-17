from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable
import math
import threading

import chess
import chess.engine


PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


@dataclass(slots=True)
class SearchSettings:
    threads: int = 8
    hash_mb: int = 1024
    root_depth: int = 22
    shallow_depth: int = 12
    reply_depth: int = 20
    continuation_depth: int = 22
    frontier_width: int = 3
    tree_max_ply: int = 32
    tree_node_cap: int = 3000
    multipv: int = 4


@dataclass(slots=True)
class BrilliantFlags:
    is_best_move: bool = False
    is_real_sacrifice: bool = False
    is_free_capture: bool = False
    is_defensive_only: bool = False
    looks_losing_initially: bool = False
    holds_after_best_defense: bool = False
    has_forcing_followup: bool = False
    compensation_type: str = "none"


@dataclass(slots=True)
class BrilliantResult:
    move_san: str
    move_uci: str
    path_san: list[str]
    eval_cp: float
    shallow_eval_cp: float
    best_defense_san: str
    best_defense_eval_cp: float
    best_acceptance_san: str
    best_acceptance_eval_cp: float
    best_decline_san: str
    best_decline_eval_cp: float
    continuation_san: str
    sacrifice_value: int
    sacrifice_category: str
    compensation_type: str
    flags: BrilliantFlags = field(default_factory=BrilliantFlags)


@dataclass(slots=True)
class SacrificeProfile:
    moved_piece_value: int
    captured_value: int
    sacrifice_value: int
    is_real_sacrifice: bool
    is_free_capture: bool
    is_defensive_only: bool
    is_hanging_offer: bool
    category: str


class CancelledError(RuntimeError):
    pass


ProgressCallback = Callable[[str], None]
ResultCallback = Callable[[BrilliantResult], None]


@dataclass(slots=True)
class AnalysisCache:
    entries: dict[tuple[str, int, int], list[dict]] = field(default_factory=dict)

    def get(self, board: chess.Board, depth: int, multipv: int) -> list[dict] | None:
        return self.entries.get((board.fen(), depth, multipv))

    def put(self, board: chess.Board, depth: int, multipv: int, infos: list[dict]) -> None:
        # Engine info payloads are treated as immutable after storage.
        self.entries[(board.fen(), depth, multipv)] = infos


def piece_value(piece_type: int | None) -> int:
    if piece_type is None:
        return 0
    return PIECE_VALUES.get(piece_type, 0)


def cp_from_score(score: chess.engine.PovScore, turn: chess.Color) -> float:
    pov = score.pov(turn)
    if pov.is_mate():
        mate = pov.mate()
        if mate is None:
            return 0.0
        return 100000.0 if mate > 0 else -100000.0
    cp = pov.score(mate_score=100000)
    return float(cp or 0)


def material_for_color(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for piece_type in PIECE_VALUES:
        total += len(board.pieces(piece_type, color)) * PIECE_VALUES[piece_type]
    return total


def hanging_friendly_squares(board: chess.Board, color: chess.Color, ignored: Iterable[chess.Square] = ()) -> list[chess.Square]:
    ignored_set = set(ignored)
    enemy = not color
    out: list[chess.Square] = []
    for square, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING or square in ignored_set:
            continue
        if board.is_attacked_by(enemy, square) and not board.is_attacked_by(color, square):
            out.append(square)
    return out


def classify_sacrifice(before: chess.Board, after: chess.Board, move: chess.Move) -> SacrificeProfile:
    mover = before.turn
    enemy = not mover
    moved_piece = before.piece_at(move.from_square)
    captured_piece = before.piece_at(move.to_square)
    moved_value = piece_value(moved_piece.piece_type if moved_piece else None)
    captured_value = piece_value(captured_piece.piece_type if captured_piece else None)
    destination_attacked_by_enemy = before.is_attacked_by(enemy, move.to_square)
    destination_defended_by_mover = after.is_attacked_by(mover, move.to_square)
    recapture_exists = any(reply.to_square == move.to_square for reply in after.legal_moves)
    is_hanging_offer = destination_attacked_by_enemy and recapture_exists and not destination_defended_by_mover
    quiet_offer_value = moved_value if not captured_value and is_hanging_offer else 0
    apparent_sac_value = (moved_value - captured_value) if is_hanging_offer and moved_value > captured_value else 0
    sacrifice_value = max(quiet_offer_value, apparent_sac_value)
    hanging_before = hanging_friendly_squares(before, mover, [move.from_square])
    hanging_after = hanging_friendly_squares(after, mover, [move.to_square])
    is_defensive_only = len(hanging_after) < len(hanging_before)
    is_free_capture = captured_value > 0 and not destination_attacked_by_enemy
    is_real_sacrifice = is_hanging_offer and moved_value >= 3 and sacrifice_value >= 1

    category = "positional"
    if captured_value > 0 and is_hanging_offer:
        category = "deflection"
    elif quiet_offer_value > 0:
        category = "direct_hanging"
    elif apparent_sac_value > 0:
        category = "apparent"

    return SacrificeProfile(
        moved_piece_value=moved_value,
        captured_value=captured_value,
        sacrifice_value=sacrifice_value,
        is_real_sacrifice=is_real_sacrifice,
        is_free_capture=is_free_capture,
        is_defensive_only=is_defensive_only,
        is_hanging_offer=is_hanging_offer,
        category=category,
    )


def infer_compensation_type(profile: SacrificeProfile, root_cp: float, defense_cp: float, continuation_san: str) -> str:
    if defense_cp >= 99900:
        return "mate_attack"
    if profile.category == "deflection":
        return "deflection"
    if profile.category == "clearance":
        return "clearance"
    if continuation_san and defense_cp >= root_cp - 5:
        return "material_recovery"
    if defense_cp > root_cp + 35:
        return "positional_domination"
    return "none"


def assert_active(cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise CancelledError("cancelled")


def analyse(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    depth: int,
    multipv: int = 1,
    cache: AnalysisCache | None = None,
) -> list[dict]:
    cached = cache.get(board, depth, multipv) if cache else None
    if cached is not None:
        return cached
    info = engine.analyse(
        board,
        chess.engine.Limit(depth=depth),
        multipv=multipv,
        info=chess.engine.INFO_SCORE | chess.engine.INFO_PV,
    )
    infos = [info] if isinstance(info, dict) else list(info)
    if cache:
        cache.put(board, depth, multipv, infos)
    return infos


def best_line_children(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    settings: SearchSettings,
    cancel_event: threading.Event,
    cache: AnalysisCache,
) -> list[tuple[chess.Move, float]]:
    assert_active(cancel_event)
    infos = analyse(
        engine,
        board,
        settings.shallow_depth,
        multipv=max(settings.frontier_width, settings.multipv),
        cache=cache,
    )
    moves: list[tuple[chess.Move, float]] = []
    seen: set[str] = set()
    for entry in infos:
        assert_active(cancel_event)
        pv = entry.get("pv") or []
        if not pv:
            continue
        move = pv[0]
        key = move.uci()
        if key in seen:
            continue
        seen.add(key)
        score = cp_from_score(entry["score"], board.turn)
        moves.append((move, score))
    moves.sort(key=lambda item: item[1], reverse=True)
    return moves[: max(1, settings.frontier_width)]


def san_path(board: chess.Board, moves: list[chess.Move]) -> list[str]:
    temp = board.copy(stack=False)
    out: list[str] = []
    for move in moves:
        out.append(temp.san(move))
        temp.push(move)
    return out


def find_brilliant_moves(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    settings: SearchSettings,
    cancel_event: threading.Event,
    on_progress: ProgressCallback | None = None,
    on_result: ResultCallback | None = None,
) -> list[BrilliantResult]:
    results: list[BrilliantResult] = []
    visited: set[tuple[str, int]] = set()
    node_counter = 0
    cache = AnalysisCache()

    def progress(message: str) -> None:
        if on_progress:
            on_progress(message)

    def evaluate_node(node: chess.Board, path: list[chess.Move], ply: int) -> None:
        nonlocal node_counter
        assert_active(cancel_event)
        if ply > settings.tree_max_ply or node_counter >= settings.tree_node_cap:
            return
        key = (node.fen(), settings.tree_max_ply - ply)
        if key in visited:
            return
        visited.add(key)
        node_counter += 1

        progress(f"Scanning depth {ply} ({node_counter}/{settings.tree_node_cap})")
        root_infos = analyse(
            engine,
            node,
            settings.shallow_depth,
            multipv=max(settings.frontier_width, settings.multipv),
            cache=cache,
        )
        if not root_infos:
            return

        shallow_best_entry = root_infos[0]
        best_pv = shallow_best_entry.get("pv") or []
        if best_pv:
            candidate_move = best_pv[0]
            if candidate_move in node.legal_moves:
                candidate_after = node.copy(stack=False)
                candidate_after.push(candidate_move)
                quick_profile = classify_sacrifice(node, candidate_after, candidate_move)
                if quick_profile.is_real_sacrifice and not quick_profile.is_free_capture and not quick_profile.is_defensive_only:
                    root_confirm = analyse(engine, node, settings.root_depth, multipv=1, cache=cache)
                    best_entry = root_confirm[0] if root_confirm else None
                    best_confirm_pv = best_entry.get("pv") if best_entry else None
                    if best_confirm_pv:
                        best_move = best_confirm_pv[0]
                    else:
                        best_move = candidate_move

                    after = node.copy(stack=False)
                    profile_after = None
                    if best_move in node.legal_moves:
                        san = node.san(best_move)
                        after.push(best_move)
                        profile_after = classify_sacrifice(node, after, best_move)
                    else:
                        profile_after = quick_profile

                    if profile_after.is_real_sacrifice and not profile_after.is_free_capture and not profile_after.is_defensive_only:
                        shallow_infos = analyse(engine, after, settings.shallow_depth, multipv=1, cache=cache)
                        deep_infos = analyse(engine, after, settings.root_depth, multipv=1, cache=cache)
                        shallow_cp = cp_from_score(shallow_infos[0]["score"], node.turn) if shallow_infos else -math.inf
                        deep_cp = cp_from_score(deep_infos[0]["score"], node.turn) if deep_infos else -math.inf
                        root_cp = cp_from_score(best_entry["score"], node.turn) if best_entry else -math.inf

                        reply_infos = analyse(
                            engine,
                            after,
                            settings.reply_depth,
                            multipv=min(2, max(1, settings.frontier_width)),
                            cache=cache,
                        )
                        best_acceptance_san = ""
                        best_decline_san = ""
                        best_defense_san = ""
                        best_acceptance_cp = -math.inf
                        best_decline_cp = -math.inf
                        best_defense_cp = -math.inf
                        continuation_san = ""

                        for reply_info in reply_infos:
                            assert_active(cancel_event)
                            reply_pv = reply_info.get("pv") or []
                            if not reply_pv:
                                continue
                            reply_move = reply_pv[0]
                            reply_board = after.copy(stack=False)
                            if reply_move not in reply_board.legal_moves:
                                continue
                            reply_san = reply_board.san(reply_move)
                            accepting = reply_move.to_square == best_move.to_square
                            reply_board.push(reply_move)
                            cont_infos = analyse(
                                engine,
                                reply_board,
                                settings.continuation_depth,
                                multipv=1,
                                cache=cache,
                            )
                            if not cont_infos:
                                continue
                            cont_cp = cp_from_score(cont_infos[0]["score"], node.turn)
                            cont_pv = cont_infos[0].get("pv") or []
                            if cont_pv:
                                temp = reply_board.copy(stack=False)
                                if cont_pv[0] in temp.legal_moves:
                                    continuation_san = temp.san(cont_pv[0])

                            if best_defense_san == "" or cont_cp < best_defense_cp:
                                best_defense_san = reply_san
                                best_defense_cp = cont_cp
                            if accepting and (best_acceptance_san == "" or cont_cp < best_acceptance_cp):
                                best_acceptance_san = reply_san
                                best_acceptance_cp = cont_cp
                            if not accepting and (best_decline_san == "" or cont_cp < best_decline_cp):
                                best_decline_san = reply_san
                                best_decline_cp = cont_cp

                        flags = BrilliantFlags(
                            is_best_move=True,
                            is_real_sacrifice=profile_after.is_real_sacrifice,
                            is_free_capture=profile_after.is_free_capture,
                            is_defensive_only=profile_after.is_defensive_only,
                            looks_losing_initially=(
                                profile_after.sacrifice_value >= 1
                                or shallow_cp <= root_cp - 20
                                or deep_cp - shallow_cp >= 28
                            ),
                            holds_after_best_defense=best_defense_cp >= root_cp - 5,
                            has_forcing_followup=bool(continuation_san),
                            compensation_type=infer_compensation_type(profile_after, root_cp, best_defense_cp, continuation_san),
                        )
                        is_brilliant = (
                            flags.is_best_move
                            and flags.is_real_sacrifice
                            and not flags.is_free_capture
                            and not flags.is_defensive_only
                            and flags.looks_losing_initially
                            and flags.holds_after_best_defense
                            and flags.has_forcing_followup
                            and flags.compensation_type != "none"
                        )
                        if is_brilliant:
                            result = BrilliantResult(
                                move_san=san,
                                move_uci=best_move.uci(),
                                path_san=san_path(board, path),
                                eval_cp=deep_cp,
                                shallow_eval_cp=shallow_cp,
                                best_defense_san=best_defense_san,
                                best_defense_eval_cp=best_defense_cp,
                                best_acceptance_san=best_acceptance_san,
                                best_acceptance_eval_cp=best_acceptance_cp,
                                best_decline_san=best_decline_san,
                                best_decline_eval_cp=best_decline_cp,
                                continuation_san=continuation_san,
                                sacrifice_value=profile_after.sacrifice_value,
                                sacrifice_category=profile_after.category,
                                compensation_type=flags.compensation_type,
                                flags=flags,
                            )
                            results.append(result)
                            if on_result:
                                on_result(result)

        for child_move, _score in best_line_children(engine, node, settings, cancel_event, cache):
            assert_active(cancel_event)
            next_board = node.copy(stack=False)
            if child_move not in next_board.legal_moves:
                continue
            next_board.push(child_move)
            evaluate_node(next_board, path + [child_move], ply + 1)

    evaluate_node(board.copy(stack=False), [], 0)
    return results
