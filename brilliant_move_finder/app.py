from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import chess

from .analyzer import board_from_input
from .engine import StockfishSession
from .logic import BrilliantResult, CancelledError, SearchSettings, find_brilliant_moves
from .report import default_export_path, export_results_to_json, export_results_to_pgn


APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_ENGINE_HINTS = [
    Path("stockfish.exe"),
    Path("stockfish") / "stockfish-windows-x86-64-avx2.exe",
    Path("stockfish") / "stockfish-windows-x86-64-bmi2.exe",
    Path(os.environ.get("STOCKFISH_PATH", "")),
]


class BrilliantMoveFinderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Brilliant Move Finder")
        self.root.geometry("1220x860")
        self.root.minsize(1040, 740)

        self._ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._results: list[BrilliantResult] = []
        self._last_board: chess.Board | None = None
        self._last_settings: SearchSettings | None = None

        saved = self._load_config()

        cpu_default = max(1, os.cpu_count() or 8)
        self.engine_path_var = tk.StringVar(value=saved.get("engine_path", self._default_engine_path()))
        self.fen_var = tk.StringVar(value=saved.get("fen", ""))
        self.moves_var = tk.StringVar(value=saved.get("moves", ""))
        self.threads_var = tk.IntVar(value=int(saved.get("threads", cpu_default)))
        self.hash_var = tk.IntVar(value=int(saved.get("hash_mb", 4096)))
        self.root_depth_var = tk.IntVar(value=int(saved.get("root_depth", 26)))
        self.shallow_depth_var = tk.IntVar(value=int(saved.get("shallow_depth", 12)))
        self.reply_depth_var = tk.IntVar(value=int(saved.get("reply_depth", 22)))
        self.continuation_depth_var = tk.IntVar(value=int(saved.get("continuation_depth", 24)))
        self.frontier_var = tk.IntVar(value=int(saved.get("frontier_width", 3)))
        self.tree_ply_var = tk.IntVar(value=int(saved.get("tree_max_ply", 36)))
        self.tree_nodes_var = tk.IntVar(value=int(saved.get("tree_node_cap", 4000)))
        self.multipv_var = tk.IntVar(value=int(saved.get("multipv", 4)))
        self.status_var = tk.StringVar(value="Ready.")
        self.summary_var = tk.StringVar(value="No scan yet.")

        self.results_list: tk.Listbox
        self.detail_text: tk.Text
        self.log_text: tk.Text

        self._build()
        self.root.after(100, self._drain_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_config(self) -> dict:
        if not CONFIG_PATH.exists():
            return {}
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_config(self) -> None:
        payload = {
            "engine_path": self.engine_path_var.get().strip(),
            "fen": self.fen_var.get().strip(),
            "moves": self.moves_var.get().strip(),
            "threads": self.threads_var.get(),
            "hash_mb": self.hash_var.get(),
            "root_depth": self.root_depth_var.get(),
            "shallow_depth": self.shallow_depth_var.get(),
            "reply_depth": self.reply_depth_var.get(),
            "continuation_depth": self.continuation_depth_var.get(),
            "frontier_width": self.frontier_var.get(),
            "tree_max_ply": self.tree_ply_var.get(),
            "tree_node_cap": self.tree_nodes_var.get(),
            "multipv": self.multipv_var.get(),
        }
        CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _default_engine_path(self) -> str:
        for candidate in DEFAULT_ENGINE_HINTS:
            candidate_str = str(candidate).strip()
            if candidate_str and candidate_str not in {".", ""} and candidate.exists():
                return str(candidate.resolve())
        return ""

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="Brilliant Move Finder", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            top,
            text="Standalone local Stockfish scanner for best-move sacrifices, with live hits, PGN export, and deeper tree search.",
        ).pack(anchor="w", pady=(4, 0))

        body = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        body.pack(fill="both", expand=True, pady=(14, 0))

        controls = ttk.Frame(body, padding=(0, 0, 14, 0))
        results = ttk.Frame(body)
        body.add(controls, weight=0)
        body.add(results, weight=1)

        self._build_controls(controls)
        self._build_results(results)

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(10, 0))
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        ttk.Label(status, textvariable=self.summary_var).pack(side="right")

    def _build_controls(self, parent: ttk.Frame) -> None:
        engine_frame = ttk.LabelFrame(parent, text="Engine")
        engine_frame.pack(fill="x")
        ttk.Label(engine_frame, text="Stockfish executable").grid(row=0, column=0, sticky="w", pady=(8, 4), padx=8)
        ttk.Entry(engine_frame, textvariable=self.engine_path_var, width=54).grid(
            row=1, column=0, sticky="ew", padx=(8, 6), pady=(0, 8)
        )
        ttk.Button(engine_frame, text="Browse", command=self._browse_engine).grid(row=1, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(
            engine_frame,
            text="Use your strongest local Stockfish build here. This app runs natively, so high thread/hash settings are fine.",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        engine_frame.columnconfigure(0, weight=1)

        input_frame = ttk.LabelFrame(parent, text="Position")
        input_frame.pack(fill="x", pady=(12, 0))
        ttk.Label(input_frame, text="FEN").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        ttk.Entry(input_frame, textvariable=self.fen_var, width=54).grid(row=1, column=0, sticky="ew", padx=8)
        ttk.Label(input_frame, text="Moves from start (SAN)").grid(row=2, column=0, sticky="w", padx=8, pady=(10, 4))
        ttk.Entry(input_frame, textvariable=self.moves_var, width=54).grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Label(
            input_frame,
            text="Enter either a FEN or a SAN move list. If both are filled, FEN wins.",
        ).grid(row=4, column=0, sticky="w", padx=8, pady=(0, 8))
        input_frame.columnconfigure(0, weight=1)

        settings_frame = ttk.LabelFrame(parent, text="Search")
        settings_frame.pack(fill="x", pady=(12, 0))
        self._spin(settings_frame, "Threads", self.threads_var, 1, max(1, os.cpu_count() or 32), 0, 0)
        self._spin(settings_frame, "Hash (MB)", self.hash_var, 128, 32768, 0, 1)
        self._spin(settings_frame, "Root depth", self.root_depth_var, 12, 60, 1, 0)
        self._spin(settings_frame, "Shallow depth", self.shallow_depth_var, 4, 30, 1, 1)
        self._spin(settings_frame, "Reply depth", self.reply_depth_var, 8, 60, 2, 0)
        self._spin(settings_frame, "Continuation depth", self.continuation_depth_var, 8, 60, 2, 1)
        self._spin(settings_frame, "Frontier width", self.frontier_var, 1, 8, 3, 0)
        self._spin(settings_frame, "Tree max ply", self.tree_ply_var, 2, 120, 3, 1)
        self._spin(settings_frame, "Tree node cap", self.tree_nodes_var, 50, 100000, 4, 0, increment=50)
        self._spin(settings_frame, "MultiPV", self.multipv_var, 1, 12, 4, 1)
        ttk.Label(
            settings_frame,
            text="Defaults are tuned for a strong desktop. Push depth/hash higher if you want a heavier search.",
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 8))

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="Find Brilliant Moves", command=self._start_scan).pack(side="left")
        ttk.Button(actions, text="Cancel", command=self._cancel_scan).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Export PGN", command=self._export_pgn).pack(side="right")
        ttk.Button(actions, text="Export JSON", command=self._export_json).pack(side="right", padx=(0, 8))

    def _build_results(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.pack(fill="both", expand=True)

        left = ttk.LabelFrame(top, text="Brilliant Moves Found")
        right = ttk.LabelFrame(top, text="Details")
        left.pack(side="left", fill="y")
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self.results_list = tk.Listbox(left, width=40, height=24, exportselection=False, font=("Consolas", 11))
        self.results_list.pack(fill="both", expand=True, padx=8, pady=8)
        self.results_list.bind("<<ListboxSelect>>", self._on_select_result)

        self.detail_text = tk.Text(right, wrap="word", font=("Consolas", 11), state="disabled")
        self.detail_text.pack(fill="both", expand=True, padx=8, pady=8)

        log_frame = ttk.LabelFrame(parent, text="Live Search Log")
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = tk.Text(log_frame, wrap="word", height=12, font=("Consolas", 10), state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    def _spin(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.IntVar,
        minimum: int,
        maximum: int,
        row: int,
        column_group: int,
        increment: int = 1,
    ) -> None:
        base_column = column_group * 2
        ttk.Label(parent, text=label).grid(row=row, column=base_column, sticky="w", padx=8, pady=4)
        ttk.Spinbox(
            parent,
            from_=minimum,
            to=maximum,
            increment=increment,
            textvariable=variable,
            width=10,
        ).grid(row=row, column=base_column + 1, sticky="w", padx=(0, 12), pady=4)

    def _browse_engine(self) -> None:
        path = filedialog.askopenfilename(title="Select Stockfish executable")
        if path:
            self.engine_path_var.set(path)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message.rstrip() + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _set_details(self, text: str) -> None:
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")

    def _build_settings(self) -> SearchSettings:
        return SearchSettings(
            threads=max(1, int(self.threads_var.get())),
            hash_mb=max(128, int(self.hash_var.get())),
            root_depth=max(1, int(self.root_depth_var.get())),
            shallow_depth=max(1, int(self.shallow_depth_var.get())),
            reply_depth=max(1, int(self.reply_depth_var.get())),
            continuation_depth=max(1, int(self.continuation_depth_var.get())),
            frontier_width=max(1, int(self.frontier_var.get())),
            tree_max_ply=max(1, int(self.tree_ply_var.get())),
            tree_node_cap=max(1, int(self.tree_nodes_var.get())),
            multipv=max(1, int(self.multipv_var.get())),
        )

    def _start_scan(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Search running", "Cancel the current scan before starting another one.")
            return

        engine_path = self.engine_path_var.get().strip()
        if not engine_path:
            messagebox.showerror("Missing engine", "Pick a local Stockfish executable first.")
            return

        try:
            board = board_from_input(self.fen_var.get(), self.moves_var.get())
        except Exception as exc:
            messagebox.showerror("Invalid position", str(exc))
            return

        settings = self._build_settings()
        self._save_config()
        self._cancel_event.clear()
        self._results.clear()
        self._last_board = board.copy(stack=False)
        self._last_settings = settings
        self.results_list.delete(0, tk.END)
        self._set_details("")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        self.status_var.set("Starting scan...")
        self.summary_var.set("0 brilliant moves found")
        self._append_log(f"Starting scan from {'FEN' if self.fen_var.get().strip() else 'SAN moves'}")
        self._append_log(f"Settings: {asdict(settings)}")

        self._worker = threading.Thread(
            target=self._scan_worker,
            args=(engine_path, board, settings),
            daemon=True,
        )
        self._worker.start()

    def _cancel_scan(self) -> None:
        if self._worker and self._worker.is_alive():
            self._cancel_event.set()
            self.status_var.set("Cancelling search...")
        else:
            self.status_var.set("No active search.")

    def _scan_worker(self, engine_path: str, board: chess.Board, settings: SearchSettings) -> None:
        try:
            with StockfishSession(engine_path, hash_mb=settings.hash_mb, threads=settings.threads) as session:
                results = find_brilliant_moves(
                    session.engine,
                    board,
                    settings,
                    self._cancel_event,
                    on_progress=lambda message: self._ui_queue.put(("progress", message)),
                    on_result=lambda result: self._ui_queue.put(("result", result)),
                )
            self._ui_queue.put(("done", results))
        except CancelledError:
            self._ui_queue.put(("cancelled", None))
        except FileNotFoundError:
            self._ui_queue.put(("error", "The Stockfish executable could not be opened."))
        except Exception as exc:
            self._ui_queue.put(("error", str(exc)))

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "progress":
                    self.status_var.set(str(payload))
                    self._append_log(str(payload))
                elif kind == "result":
                    result = payload
                    assert isinstance(result, BrilliantResult)
                    self._results.append(result)
                    label = self._format_result_label(result, len(self._results))
                    self.results_list.insert(tk.END, label)
                    self.summary_var.set(f"{len(self._results)} brilliant move(s) found")
                    self._append_log(f"Found: {label}")
                    if len(self._results) == 1:
                        self.results_list.selection_clear(0, tk.END)
                        self.results_list.selection_set(0)
                        self._show_result(0)
                elif kind == "done":
                    results = payload
                    assert isinstance(results, list)
                    self.status_var.set("Scan complete.")
                    self.summary_var.set(f"{len(self._results)} brilliant move(s) found")
                    if not results:
                        self._append_log("No brilliant moves matched the current strict pipeline.")
                elif kind == "cancelled":
                    self.status_var.set("Scan cancelled.")
                    self._append_log("Scan cancelled.")
                elif kind == "error":
                    self.status_var.set("Analysis failed.")
                    self._append_log(f"Error: {payload}")
                    messagebox.showerror("Analysis failed", str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._drain_ui_queue)

    def _format_result_label(self, result: BrilliantResult, index: int) -> str:
        path_prefix = " ".join(result.path_san[-4:]) if result.path_san else "start"
        if len(path_prefix) > 38:
            path_prefix = "..." + path_prefix[-35:]
        return f"{index}. {result.move_san} | {result.compensation_type} | {path_prefix}"

    def _on_select_result(self, _event: object) -> None:
        selection = self.results_list.curselection()
        if not selection:
            return
        self._show_result(selection[0])

    def _show_result(self, index: int) -> None:
        if index < 0 or index >= len(self._results):
            return
        result = self._results[index]
        lines = [
            f"Brilliant move: {result.move_san}",
            f"Line to reach candidate position: {' '.join(result.path_san) if result.path_san else '(starting position)'}",
            f"Candidate path including move: {' '.join(result.path_san + [result.move_san])}",
            "",
            f"Eval after move: {result.eval_cp:.1f} cp",
            f"Shallow eval after move: {result.shallow_eval_cp:.1f} cp",
            f"Sacrifice value: {result.sacrifice_value}",
            f"Sacrifice category: {result.sacrifice_category}",
            f"Compensation type: {result.compensation_type}",
            "",
            f"Best defense: {result.best_defense_san or 'none'} ({result.best_defense_eval_cp:.1f} cp)",
            f"Best acceptance: {result.best_acceptance_san or 'none'} ({result.best_acceptance_eval_cp:.1f} cp)",
            f"Best decline: {result.best_decline_san or 'none'} ({result.best_decline_eval_cp:.1f} cp)",
            f"Best continuation: {result.continuation_san or 'none'}",
            "",
            "Flags:",
        ]
        for name, value in asdict(result.flags).items():
            lines.append(f"  - {name}: {value}")
        self._set_details("\n".join(lines))

    def _export_pgn(self) -> None:
        if not self._results or self._last_board is None or self._last_settings is None:
            messagebox.showinfo("Nothing to export", "Run a scan and find at least one brilliant move first.")
            return
        suggested = default_export_path("brilliant-moves", "pgn")
        path = filedialog.asksaveasfilename(
            title="Export brilliant moves to PGN",
            defaultextension=".pgn",
            initialfile=suggested.name,
            initialdir=str(suggested.parent),
            filetypes=[("PGN files", "*.pgn"), ("All files", "*.*")],
        )
        if not path:
            return
        target = export_results_to_pgn(path, self._last_board, self._results, self._last_settings)
        self.status_var.set(f"Exported PGN to {target}")
        self._append_log(f"Exported PGN: {target}")

    def _export_json(self) -> None:
        if not self._results or self._last_board is None or self._last_settings is None:
            messagebox.showinfo("Nothing to export", "Run a scan and find at least one brilliant move first.")
            return
        suggested = default_export_path("brilliant-moves", "json")
        path = filedialog.asksaveasfilename(
            title="Export brilliant moves to JSON",
            defaultextension=".json",
            initialfile=suggested.name,
            initialdir=str(suggested.parent),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        target = export_results_to_json(path, self._last_board, self._results, self._last_settings)
        self.status_var.set(f"Exported JSON to {target}")
        self._append_log(f"Exported JSON: {target}")

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            self._cancel_event.set()
        self._save_config()
        self.root.destroy()


def run_app() -> None:
    root = tk.Tk()
    ttk.Style().theme_use("clam")
    BrilliantMoveFinderApp(root)
    root.mainloop()
