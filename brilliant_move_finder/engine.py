from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import chess
import chess.engine


MATE_CP = 100_000


@dataclass(slots=True)
class EvalResult:
    cp: int
    pv: list[chess.Move]


def score_to_cp(score: chess.engine.PovScore, turn: chess.Color) -> int:
    pov = score.pov(turn)
    if pov.is_mate():
        mate = pov.mate()
        if mate is None:
            return 0
        return MATE_CP - abs(mate) if mate > 0 else -MATE_CP + abs(mate)
    value = pov.score(mate_score=MATE_CP)
    return 0 if value is None else int(value)


class StockfishSession:
    def __init__(self, engine_path: str | Path, hash_mb: int = 256, threads: int = 1) -> None:
        self.engine_path = Path(engine_path)
        self.hash_mb = max(16, int(hash_mb))
        self.threads = max(1, int(threads))
        self._engine: chess.engine.SimpleEngine | None = None

    def __enter__(self) -> "StockfishSession":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._engine is not None:
            return
        self._engine = chess.engine.SimpleEngine.popen_uci(str(self.engine_path))
        self._engine.configure(
            {
                "Hash": self.hash_mb,
                "Threads": self.threads,
            }
        )

    def close(self) -> None:
        if self._engine is None:
            return
        self._engine.quit()
        self._engine = None

    @property
    def engine(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            self.open()
        assert self._engine is not None
        return self._engine

    def analyse(self, board: chess.Board, depth: int) -> EvalResult:
        info = self.engine.analyse(board, chess.engine.Limit(depth=max(1, int(depth))))
        score = score_to_cp(info["score"], board.turn)
        pv = list(info.get("pv", []))
        return EvalResult(cp=score, pv=pv)

    def multipv(self, board: chess.Board, depth: int, lines: int) -> list[EvalResult]:
        infos = self.engine.analyse(
            board,
            chess.engine.Limit(depth=max(1, int(depth))),
            multipv=max(1, int(lines)),
        )
        out: list[EvalResult] = []
        for info in infos:
            out.append(
                EvalResult(
                    cp=score_to_cp(info["score"], board.turn),
                    pv=list(info.get("pv", [])),
                )
            )
        return out


def pv_to_san(board: chess.Board, pv: Iterable[chess.Move], limit: int = 10) -> str:
    clone = board.copy(stack=False)
    san_moves: list[str] = []
    for move in list(pv)[:limit]:
        if move not in clone.legal_moves:
            break
        san_moves.append(clone.san(move))
        clone.push(move)
    return " ".join(san_moves)
