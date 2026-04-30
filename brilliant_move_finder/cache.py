from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import chess
import chess.engine

from .engine import EvalResult


class DiskCache:
    """Small JSON cache for expensive Stockfish/database lookups."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, namespace: str, key: dict[str, Any]) -> Path:
        text = json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        safe_namespace = namespace.replace("/", "_").replace("\\", "_")
        return self.root / safe_namespace / f"{digest}.json"

    def load_json(self, namespace: str, key: dict[str, Any]) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_json(self, namespace: str, key: dict[str, Any], payload: Any) -> None:
        path = self._path(namespace, key)
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
                temp.replace(path)
        except Exception:
            # Cache writes should never break analysis.
            return


def eval_result_to_cache(result: EvalResult) -> dict[str, Any]:
    return {
        "cp": int(result.cp),
        "score_type": result.score_type,
        "value": int(result.value),
        "pv": [move.uci() for move in result.pv],
    }


def eval_result_from_cache(payload: dict[str, Any]) -> EvalResult:
    return EvalResult(
        cp=int(payload.get("cp", 0)),
        score_type=str(payload.get("score_type", "centipawn")),
        value=int(payload.get("value", payload.get("cp", 0) or 0)),
        pv=[
            chess.Move.from_uci(str(uci))
            for uci in payload.get("pv", [])
            if isinstance(uci, str)
        ],
    )


def engine_info_to_cache(info: dict[str, Any]) -> dict[str, Any]:
    score = info["score"].white()
    if score.is_mate():
        score_type = "mate"
        value = int(score.mate() or 0)
    else:
        score_type = "centipawn"
        value = int(score.score(mate_score=100000) or 0)
    return {
        "score_type": score_type,
        "value": value,
        "pv": [move.uci() for move in info.get("pv", [])],
    }


def engine_info_from_cache(payload: dict[str, Any]) -> dict[str, Any]:
    score_type = str(payload.get("score_type", "centipawn"))
    value = int(payload.get("value", 0))
    score = chess.engine.Mate(value) if score_type == "mate" else chess.engine.Cp(value)
    return {
        "score": chess.engine.PovScore(score, chess.WHITE),
        "pv": [
            chess.Move.from_uci(str(uci))
            for uci in payload.get("pv", [])
            if isinstance(uci, str)
        ],
    }
