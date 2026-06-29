"""Load YAML configuration (assumptions + branding) once and share it."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import os

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    env_path = path or (ROOT_DIR / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# Load .env as soon as the package config is imported.
load_dotenv()


def _load(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def assumptions() -> Dict[str, Any]:
    """Engineering & economic assumptions (config/assumptions.yaml)."""
    return _load("assumptions.yaml")


@lru_cache(maxsize=1)
def branding() -> Dict[str, Any]:
    """Brand identity & client-facing copy (config/branding.yaml)."""
    return _load("branding.yaml")


def reload() -> None:
    """Clear caches so edited YAML is picked up (used by the Streamlit app)."""
    assumptions.cache_clear()
    branding.cache_clear()
