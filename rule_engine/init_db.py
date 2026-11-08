from __future__ import annotations

from pathlib import Path

from .config import load_config


def split_sql(text: str) -> list[str]:
    """Split schema.sql into executable statements.

    The schema is plain DDL with line comments; removing `--` comments before
    splitting avoids semicolons inside comments.
    """
    cleaned = []
    for line in text.splitlines():
        idx = line.find("--")
        if idx != -1:
            line = line[:idx]
        cleaned.append(line)
    return [part.strip() for part in "\n".join(cleaned).split(";") if part.strip()]


def main() -> int:
    try:
        import pymysql
    except ImportError:
        print("pymysql is required. Install with: pip install -r requirements.txt")
        return 2

    cfg = load_config()
    schema_path = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    if not schema_path.exists():
        print(f"schema not found: {schema_path}")
        return 2
    statements = split_sql(schema_path.read_text(encoding="utf-8-sig"))
    conn = pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
    finally:
        conn.close()
    print(f"schema ready: {schema_path} ({len(statements)} statements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
