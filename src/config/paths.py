import sys
from pathlib import Path

from config.constants import ASSETS_DIR_NAME


def get_asset_dir() -> Path:
    # Source runs keep assets in the project-level assets folder, while
    # PyInstaller exposes bundled data under sys._MEIPASS. Centralizing this
    # path rule keeps tray icons, status sounds, and the listening indicator in
    # agreement after the folder restructure.
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / ASSETS_DIR_NAME
    return Path(__file__).resolve().parents[2] / ASSETS_DIR_NAME
