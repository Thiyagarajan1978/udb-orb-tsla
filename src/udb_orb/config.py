"""Configuration loading: merges config/config.yaml with .env secrets.

Pure, side-effect-light: `load_config()` returns a plain dict; `get_env()` reads
secrets from the process environment, falling back to the repo `.env` file so the
project runs without any global env setup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yaml"
DEFAULT_ENV = REPO_ROOT / ".env"


def _read_env_file(env_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def get_env(name: str, default: str | None = None, env_path: Path | None = None) -> str | None:
    """Env var, falling back to the repo .env file. Real process env wins."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    file_vals = _read_env_file(env_path or DEFAULT_ENV)
    v = file_vals.get(name)
    return v.strip() if v else default


def get_fmp_key(env_path: Path | None = None) -> str | None:
    return get_env("FMP_API_KEY", env_path=env_path)


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the YAML config as a nested dict."""
    p = Path(path) if path else DEFAULT_CONFIG
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def db_path(cfg: dict[str, Any]) -> Path:
    raw = cfg.get("data", {}).get("db_path", "data/udb_orb.db")
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


def cache_dir(cfg: dict[str, Any]) -> Path:
    raw = cfg.get("data", {}).get("cache_dir", "data/cache")
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p
