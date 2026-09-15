"""Configuration loading. The repo root is resolved from this file's location,
so nothing in src/ ever needs an absolute path."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "paths.yaml"


@lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve(key: str) -> Path:
    """Resolve a logical path name from configs/paths.yaml to an absolute Path."""
    cfg = load_config()
    try:
        rel = cfg["paths"][key]
    except KeyError as exc:
        raise KeyError(f"unknown path key {key!r}; known: {sorted(cfg['paths'])}") from exc
    return REPO_ROOT / rel
