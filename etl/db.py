"""Database connections.

Two endpoints, deliberately separate:

- DATABASE_URL is Neon's POOLED endpoint (PgBouncer, transaction mode). For the
  application later.
- DIRECT_URL is the UNPOOLED endpoint. Migrations and every bulk load use this.
  Transaction-mode pooling interferes with session-level operations, which makes
  DDL and COPY FROM STDIN unreliable.

Anything in etl/ that writes should call connect(direct=True).
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill in both Neon "
            f"endpoints."
        )
    return value


def database_url() -> str:
    """Pooled endpoint. Application reads."""
    return _require("DATABASE_URL")


def direct_url() -> str:
    """Unpooled endpoint. Migrations and bulk loading."""
    url = _require("DIRECT_URL")
    if "-pooler." in url:
        raise RuntimeError(
            "DIRECT_URL points at the pooled endpoint (host contains '-pooler'). "
            "Migrations and COPY need the unpooled host."
        )
    return url


def connect(*, direct: bool = False) -> psycopg.Connection:
    return psycopg.connect(direct_url() if direct else database_url())


def data_dir() -> Path:
    d = REPO_ROOT / os.environ.get("DATA_DIR", "./data").lstrip("./")
    d.mkdir(parents=True, exist_ok=True)
    return d
