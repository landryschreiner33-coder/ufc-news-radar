"""Project paths. Everything else imports these instead of guessing."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SEED_DIR = DATA_DIR / "seed"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = PROJECT_ROOT / "logs"


def ensure_dirs() -> None:
    """Create the folders the app writes to. Safe to call repeatedly."""
    for folder in (DATA_DIR, CACHE_DIR, LOG_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def resolve(path_like: str | Path) -> Path:
    """Resolve a possibly-relative path against the project root."""
    path = Path(path_like)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path
