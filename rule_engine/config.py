from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class DBConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "football"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_config(
    env_file: str | Path | None = None, database: str | None = None
) -> DBConfig:
    """Load DB settings from env vars, then a local .env, then defaults."""
    env_path = Path(env_file) if env_file else PROJECT_ROOT / ".env"
    file_values = _parse_env_file(env_path)

    def pick(key: str, default: str) -> str:
        return os.environ.get(key) or file_values.get(key) or default

    return DBConfig(
        host=pick("MYSQL_HOST", "127.0.0.1"),
        port=int(pick("MYSQL_PORT", "3306")),
        user=pick("MYSQL_USER", "root"),
        password=pick("MYSQL_PASSWORD", ""),
        database=database or pick("MYSQL_DB", "football"),
    )
