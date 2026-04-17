# Brilliant Move Finder

A standalone local desktop app for scanning chess positions for candidate brilliant moves using a local Stockfish engine.

## What it does

- loads a position from FEN or SAN move list
- analyzes the position with local Stockfish on your PC
- searches engine lines for brilliant best-move sacrifices
- streams brilliant hits live while the scan is running
- exports all found brilliant moves to a PGN archive
- exports the full result set to JSON for later inspection

## Brilliant pipeline

A move is only marked brilliant if all of these hold:

- it is Stockfish's best move
- it is a real non-pawn sacrifice, or a clear apparent sacrifice
- it is not just taking a hanging piece
- it is not just a defensive save for another hanging piece
- it looks wrong at first
- it survives the opponent's best defense
- the follow-up justifies it
- it does not improve the opponent's position

## Quick start

1. Install Python 3.13+
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Download a strong Stockfish binary

Recommended:
- latest official Stockfish Windows build
- `x86-64-bmi2` if your CPU supports it
- otherwise `x86-64-avx2` as a safe strong default for modern gaming PCs

4. Run the app:

```powershell
python main.py
```

## Input options

- FEN
- SAN move list from the starting position

## Features

- high-end local engine settings with editable thread/hash/depth controls
- live scan log
- result list with detailed flags and compensation breakdown
- cancel support during long searches
- PGN export for every brilliant line the app finds

## Notes

- This app is designed for local desktop use, not browser-only analysis.
- Use a strong local Stockfish binary for best results.
