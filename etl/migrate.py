"""Apply numbered SQL migrations in order.

Each migration runs in its own transaction. Applied migrations are recorded with a
checksum, so editing a file that has already run is caught rather than silently ignored.

    uv run python -m etl.migrate          apply pending migrations
    uv run python -m etl.migrate --status show what is applied and what is pending
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from etl.db import REPO_ROOT, connect

MIGRATIONS_DIR = REPO_ROOT / "migrations"

TRACKING_TABLE = """
create table if not exists schema_migrations (
    filename   text primary key,
    checksum   text not null,
    applied_at timestamptz not null default now()
)
"""


def discover() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError(f"No .sql files found in {MIGRATIONS_DIR}")
    return files


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_map(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("select filename, checksum from schema_migrations")
        return dict(cur.fetchall())


def verify_unchanged(files: list[Path], applied: dict[str, str]) -> None:
    drifted = [
        f.name
        for f in files
        if f.name in applied and checksum(f) != applied[f.name]
    ]
    if drifted:
        raise RuntimeError(
            "These migrations were edited after being applied: "
            + ", ".join(drifted)
            + ". Write a new numbered migration instead of editing an applied one."
        )


def status() -> None:
    files = discover()
    with connect() as conn:
        conn.execute(TRACKING_TABLE)
        conn.commit()
        applied = applied_map(conn)
    for f in files:
        mark = "applied" if f.name in applied else "PENDING"
        print(f"  [{mark:>7}] {f.name}")


def migrate() -> None:
    files = discover()
    with connect() as conn:
        conn.execute(TRACKING_TABLE)
        conn.commit()

        applied = applied_map(conn)
        verify_unchanged(files, applied)

        pending = [f for f in files if f.name not in applied]
        if not pending:
            print("Nothing to apply. Schema is up to date.")
            return

        for path in pending:
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "insert into schema_migrations (filename, checksum) values (%s, %s)",
                    (path.name, checksum(path)),
                )
            conn.commit()
            print(f"applied {path.name}")

        print(f"\n{len(pending)} migration(s) applied.")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        migrate()
