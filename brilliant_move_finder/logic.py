from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable
import heapq
import math
import threading

import chess
import chess.engine

from .classifications import classify_move, classify_scan_candidate
from .engine import EvalResult


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
    think_time_ms: int = 5000


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
    fen: str
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
    confidence_bucket: str = "high"
    classification_key: str = "brilliant"
    classification_label: str = "Brilliant"
    pgn_path: str = ""
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


def eval_from_info(info: dict, board: chess.Board) -> EvalResult:
    score = info["score"].white()
    pv = list(info.get("pv") or [])
    if score.is_mate():
        mate = score.mate()
        value = int(mate or 0)
        cp = 100000 if value > 0 else -100000 if value < 0 else 0
        return EvalResult(cp=cp, pv=pv, score_type="mate", value=value)
    value = int(score.score(mate_score=100000) or 0)
    return EvalResult(cp=value, pv=pv, score_type="centipawn", value=value)


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


def broad_legal_children(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    settings: SearchSettings,
    cancel_event: threading.Event,
    cache: AnalysisCache,
) -> list[chess.Move]:
    assert_active(cancel_event)
    legal = list(board.legal_moves)
    if not legal:
        return []
    infos = analyse(
        engine,
        board,
        settings.shallow_depth,
        multipv=min(len(legal), max(settings.multipv, settings.frontier_width)),
        cache=cache,
    )
    ordered: list[chess.Move] = []
    seen: set[str] = set()
    engine_rank: dict[str, int] = {}
    for entry in infos:
        pv = entry.get("pv") or []
        if pv and pv[0] in legal and pv[0].uci() not in seen:
            engine_rank[pv[0].uci()] = len(engine_rank)
            ordered.append(pv[0])
            seen.add(pv[0].uci())
    for move in legal:
        if move.uci() not in seen:
            ordered.append(move)
            seen.add(move.uci())
    return sorted(ordered, key=lambda move: child_move_priority(board, move, engine_rank))


def child_move_priority(board: chess.Board, move: chess.Move, engine_rank: dict[str, int]) -> tuple[int, int, str]:
    """Prefer shallow tactical bait before plain quiet moves.

    Brilliant move searches need to reach non-engine setup paths such as
    Nf3 g5 Nxg5 f6 e4 quickly. A pure engine frontier misses those because the
    setup moves can be objectively suspicious before the tactic appears.
    """

    key = move.uci()
    if key in engine_rank:
        return (0, engine_rank[key], key)

    moved_piece = board.piece_at(move.from_square)
    is_capture = board.is_capture(move)
    gives_check = board.gives_check(move)
    is_promotion = move.promotion is not None
    is_pawn_lunge = (
        moved_piece is not None
        and moved_piece.piece_type == chess.PAWN
        and abs(chess.square_rank(move.to_square) - chess.square_rank(move.from_square)) == 2
    )
    attacks_enemy_piece = move_attacks_enemy_piece(board, move)
    develops_piece = moved_piece is not None and moved_piece.piece_type in {chess.KNIGHT, chess.BISHOP}

    if gives_check or is_capture or is_promotion:
        bucket = 1
    elif attacks_enemy_piece:
        bucket = 2
    elif is_pawn_lunge:
        bucket = 3
    elif develops_piece:
        bucket = 4
    else:
        bucket = 5
    return (bucket, 0, key)


def move_attacks_enemy_piece(board: chess.Board, move: chess.Move) -> bool:
    moved_piece = board.piece_at(move.from_square)
    if moved_piece is None:
        return False
    temp = board.copy(stack=False)
    if move not in temp.legal_moves:
        return False
    temp.push(move)
    piece = temp.piece_at(move.to_square)
    if piece is None:
        return False
    enemy = not piece.color
    attacks = temp.attacks(move.to_square)
    return any(temp.piece_at(square) and temp.piece_at(square).color == enemy for square in attacks)


def child_search_cost(priority: tuple[int, int, str]) -> int:
    bucket, rank, _ = priority
    if bucket == 0:
        return 60 + rank * 2
    if bucket == 1:
        return 70
    if bucket == 2:
        return 72
    if bucket == 3:
        return 95
    if bucket == 4:
        return 100
    return 130


def is_forcing_or_bait_move(board: chess.Board, move: chess.Move) -> bool:
    moved_piece = board.piece_at(move.from_square)
    is_pawn_lunge = (
        moved_piece is not None
        and moved_piece.piece_type == chess.PAWN
        and abs(chess.square_rank(move.to_square) - chess.square_rank(move.from_square)) == 2
    )
    return board.is_capture(move) or board.gives_check(move) or move_attacks_enemy_piece(board, move) or is_pawn_lunge


def is_sacrifice_probe_move(board: chess.Board, move: chess.Move) -> bool:
    moved_piece = board.piece_at(move.from_square)
    return (
        board.is_capture(move)
        or board.gives_check(move)
        or move_attacks_enemy_piece(board, move)
        or (moved_piece is not None and moved_piece.piece_type in {chess.KNIGHT, chess.BISHOP})
    )


def quick_tactic_seed_nodes(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    settings: SearchSettings,
    cancel_event: threading.Event,
    cache: AnalysisCache,
) -> list[tuple[chess.Board, list[chess.Move]]]:
    """Seed short non-engine tactic paths before the general tree search.

    This catches motifs like Nf3 g5 Nxg5 f6 e4, where the route into the
    tactic is not itself an engine main line, but the quiet follow-up can be a
    genuine brilliant move once the piece sacrifice is live.
    """

    seeds: list[tuple[chess.Board, list[chess.Move]]] = []
    max_seeds = max(160, settings.frontier_width * 80)
    first_moves = broad_legal_children(engine, board, settings, cancel_event, cache)[: max(10, settings.frontier_width * 3)]
    for first in first_moves:
        first_seed_count = 0
        first_board = board.copy(stack=False)
        if first not in first_board.legal_moves:
            continue
        first_board.push(first)
        second_moves = [
            move
            for move in broad_legal_children(engine, first_board, settings, cancel_event, cache)[: max(12, settings.frontier_width * 4)]
            if is_forcing_or_bait_move(first_board, move)
        ]
        for second in second_moves:
            second_seed_count = 0
            second_board = first_board.copy(stack=False)
            if second not in second_board.legal_moves:
                continue
            second_board.push(second)
            third_moves = [
                move
                for move in broad_legal_children(engine, second_board, settings, cancel_event, cache)[: max(12, settings.frontier_width * 4)]
                if is_sacrifice_probe_move(second_board, move)
            ]
            for third in third_moves:
                third_board = second_board.copy(stack=False)
                if third not in third_board.legal_moves:
                    continue
                third_board.push(third)
                fourth_moves = [
                    move
                    for move in broad_legal_children(engine, third_board, settings, cancel_event, cache)[: max(12, settings.frontier_width * 4)]
                    if is_forcing_or_bait_move(third_board, move)
                ]
                for fourth in fourth_moves:
                    fourth_board = third_board.copy(stack=False)
                    if fourth not in fourth_board.legal_moves:
                        continue
                    fourth_board.push(fourth)
                    seeds.append((fourth_board, [first, second, third, fourth]))
                    first_seed_count += 1
                    second_seed_count += 1
                    if len(seeds) >= max_seeds:
                        return seeds
                    if second_seed_count >= 8:
                        break
                if first_seed_count >= 128:
                    break
            if first_seed_count >= 128:
                break
    return seeds


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
            multipv=min(node.legal_moves.count(), max(settings.frontier_width, settings.multipv)),
            cache=cache,
        )
        if not root_infos:
            return
        previous_lines = [eval_from_info(entry, node) for entry in root_infos]
        root_cp = cp_from_score(root_infos[0]["score"], node.turn)
        root_top = previous_lines[0].pv[0] if previous_lines and previous_lines[0].pv else None

        for candidate_move in broad_legal_children(engine, node, settings, cancel_event, cache):
            assert_active(cancel_event)
            candidate_after = node.copy(stack=False)
            if candidate_move not in candidate_after.legal_moves:
                continue
            san = node.san(candidate_move)
            candidate_after.push(candidate_move)
            quick_profile = classify_sacrifice(node, candidate_after, candidate_move)

            matching_line = next((line for line in previous_lines if line.pv and line.pv[0] == candidate_move), None)
            if matching_line is not None:
                current_eval = EvalResult(
                    cp=matching_line.cp,
                    pv=matching_line.pv[1:],
                    score_type=matching_line.score_type,
                    value=matching_line.value,
                )
                shallow_cp = cp_from_score(root_infos[0]["score"], node.turn) if candidate_move == root_top else cp_from_score(root_infos[-1]["score"], node.turn)
            else:
                shallow_infos = analyse(engine, candidate_after, settings.shallow_depth, multipv=1, cache=cache)
                current_eval = eval_from_info(shallow_infos[0], candidate_after) if shallow_infos else EvalResult(cp=0, pv=[])
                shallow_cp = cp_from_score(shallow_infos[0]["score"], node.turn) if shallow_infos else -math.inf

            confidence_bucket, classification_key = classify_scan_candidate(
                node,
                candidate_move,
                previous_lines,
                current_eval,
                quick_profile.is_real_sacrifice or quick_profile.is_hanging_offer,
            )
            if confidence_bucket:
                deep_infos = analyse(engine, candidate_after, settings.root_depth, multipv=1, cache=cache)
                deep_eval = eval_from_info(deep_infos[0], candidate_after) if deep_infos else current_eval
                classification = classify_move(node, candidate_move, previous_lines, deep_eval)

                reply_infos = analyse(
                    engine,
                    candidate_after,
                    settings.reply_depth,
                    multipv=min(2, max(1, settings.frontier_width)),
                    cache=cache,
                )
                best_defense_san = ""
                best_defense_cp = -math.inf
                continuation_san = ""
                for reply_info in reply_infos:
                    reply_pv = reply_info.get("pv") or []
                    if not reply_pv:
                        continue
                    reply_board = candidate_after.copy(stack=False)
                    reply_move = reply_pv[0]
                    if reply_move not in reply_board.legal_moves:
                        continue
                    reply_san = reply_board.san(reply_move)
                    reply_board.push(reply_move)
                    cont_infos = analyse(engine, reply_board, settings.continuation_depth, multipv=1, cache=cache)
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

                flags = BrilliantFlags(
                    is_best_move=root_top == candidate_move,
                    is_real_sacrifice=quick_profile.is_real_sacrifice,
                    is_free_capture=quick_profile.is_free_capture,
                    is_defensive_only=quick_profile.is_defensive_only,
                    looks_losing_initially=quick_profile.sacrifice_value >= 1 or shallow_cp <= root_cp - 20,
                    holds_after_best_defense=best_defense_cp >= root_cp - 5 if best_defense_san else True,
                    has_forcing_followup=bool(continuation_san),
                    compensation_type=infer_compensation_type(quick_profile, root_cp, best_defense_cp, continuation_san),
                )
                result = BrilliantResult(
                    move_san=san,
                    move_uci=candidate_move.uci(),
                    fen=node.fen(),
                    path_san=san_path(board, path),
                    eval_cp=float(deep_eval.cp),
                    shallow_eval_cp=float(shallow_cp),
                    best_defense_san=best_defense_san,
                    best_defense_eval_cp=best_defense_cp,
                    best_acceptance_san="",
                    best_acceptance_eval_cp=0.0,
                    best_decline_san="",
                    best_decline_eval_cp=0.0,
                    continuation_san=continuation_san,
                    sacrifice_value=quick_profile.sacrifice_value,
                    sacrifice_category=quick_profile.category,
                    compensation_type=flags.compensation_type,
                    confidence_bucket=confidence_bucket,
                    classification_key=classification.key,
                    classification_label=classification.label,
                    pgn_path=" ".join(san_path(board, path) + [san]),
                    flags=flags,
                )
                results.append(result)
                if on_result:
                    on_result(result)

    queue: list[tuple[int, int, chess.Board, list[chess.Move], int]] = []
    queue_sequence = 0
    heapq.heappush(queue, (0, queue_sequence, board.copy(stack=False), [], 0))
    for seed_board, seed_path in quick_tactic_seed_nodes(engine, board, settings, cancel_event, cache):
        queue_sequence += 1
        heapq.heappush(queue, (-500 + len(seed_path) * 10, queue_sequence, seed_board, seed_path, len(seed_path)))
    while queue and node_counter < settings.tree_node_cap:
        search_score, _, node, path, ply = heapq.heappop(queue)
        before_count = node_counter
        evaluate_node(node, path, ply)
        if node_counter == before_count:
            continue
        if ply >= settings.tree_max_ply or node_counter >= settings.tree_node_cap:
            continue
        for child_index, child_move in enumerate(broad_legal_children(engine, node, settings, cancel_event, cache)):
            assert_active(cancel_event)
            next_board = node.copy(stack=False)
            if child_move not in next_board.legal_moves:
                continue
            next_board.push(child_move)
            queue_sequence += 1
            if child_index < max(settings.multipv, settings.frontier_width):
                priority = (0, child_index, child_move.uci())
            else:
                priority = child_move_priority(node, child_move, {})
            heapq.heappush(
                queue,
                (
                    search_score + child_search_cost(priority),
                    queue_sequence,
                    next_board,
                    path + [child_move],
                    ply + 1,
                ),
            )

    return results
