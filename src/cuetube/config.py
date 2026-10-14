"""Centralized environment variable configuration and .env loading for CueTube."""

import os
from pathlib import Path


def load_dotenv(path: Path | str = ".env") -> None:
    """Load key-value pairs from a .env file into os.environ without overwriting existing vars."""
    env_path = Path(path)
    if not env_path.exists():
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str or line_str.startswith("#") or "=" not in line_str:
                    continue
                k, v = line_str.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


def get_env_str(key: str, default: str | None = None) -> str | None:
    """Retrieve string environment variable or fallback to default."""
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    return val.strip()


def get_env_int(key: str, default: int) -> int:
    """Retrieve integer environment variable or fallback to default."""
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def get_env_bool(key: str, default: bool = False) -> bool:
    """Retrieve boolean environment variable or fallback to default."""
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def get_env_list(key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Retrieve comma-separated string environment variable as tuple of strings."""
    val = os.getenv(key)
    if val is None or not val.strip():
        return default
    items = tuple(item.strip() for item in val.split(",") if item.strip())
    return items if items else default
