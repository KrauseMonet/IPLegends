"""Database connection helper. Reads DATABASE_URL from .env."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in the "
            "Neon connection string."
        )
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())


def data_dir() -> Path:
    d = REPO_ROOT / os.environ.get("DATA_DIR", "./data").lstrip("./")
    d.mkdir(parents=True, exist_ok=True)
    return d
