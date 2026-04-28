# Brilliant Move Finder

A standalone local GUI app for scanning chess positions for candidate brilliant moves using a local Stockfish engine and an HTML board interface in a desktop window.

## What it does

- launches a real desktop GUI window automatically
- renders the current position on an HTML chess board
- loads a position from FEN or SAN move list
- loads a full PGN game, converts it into the current search line, and snaps the board to the imported final position
- previews the current board state before you start a search
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

### Packaged app

Double-click the root-level executable:

```powershell
.\Brilliant Move Finder.exe
```

Keep the `stockfish\` folder beside the EXE so the app can find the local engine automatically.

### Source mode

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

The app starts a local server on `http://127.0.0.1:8765` and opens it inside a desktop GUI window.

## Packaging

To recreate the clean root-level desktop app:

```powershell
python package.py
```

The reproducible output is `Brilliant Move Finder.exe` directly in this folder. Old `build\`, `dist\`, and `release\` folders are removed during packaging so there is only one obvious launcher.

## Input options

- FEN
- SAN move list from the starting position

## Features

- high-end local engine settings with editable thread/hash/depth controls
- quick, balanced, and deep scan presets
- live scan log
- position preview with FEN, side to move, and board snapshot
- result list with detailed flags and compensation breakdown
- cancel support during long searches
- PGN export for every brilliant line the app finds
- JSON export for the full result set

## Notes

- This app is GUI-first and launches inside the embedded desktop window.
- All analysis still runs locally on your machine.
- Use a strong local Stockfish binary for best results.
