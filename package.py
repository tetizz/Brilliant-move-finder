from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_NAME = "Brilliant Move Finder"


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def main() -> None:
    for generated in [ROOT / "build", ROOT / "dist", ROOT / "release", ROOT / "__pycache__"]:
        remove_path(generated)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--distpath",
        str(ROOT),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT),
        "--add-data",
        f"{ROOT / 'brilliant_move_finder' / 'templates'};brilliant_move_finder/templates",
        "--add-data",
        f"{ROOT / 'brilliant_move_finder' / 'static'};brilliant_move_finder/static",
        "--add-data",
        f"{ROOT / 'brilliant_move_finder' / 'resources'};brilliant_move_finder/resources",
        str(ROOT / "main.py"),
    ]
    subprocess.run(command, check=True)

    remove_path(ROOT / "build")
    remove_path(ROOT / f"{APP_NAME}.spec")
    print(f"Packaged {ROOT / f'{APP_NAME}.exe'}")


if __name__ == "__main__":
    main()
