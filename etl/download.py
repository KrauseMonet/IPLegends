"""Resolve and download the Cricsheet archives.

URLs are never hardcoded. Every target is resolved by scanning the live Cricsheet
downloads page for an exact filename match, and what was resolved is logged to
data/manifest.json alongside a SHA256 of the bytes we actually received.

    uv run python -m etl.download            resolve and download anything missing
    uv run python -m etl.download --resolve  resolve and print URLs, download nothing
    uv run python -m etl.download --force    re-download even if present

Cricsheet data is ODbL licensed. See CREDITS.md.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from etl.db import data_dir

DOWNLOADS_PAGE = "https://cricsheet.org/downloads/"
REGISTER_PAGE = "https://cricsheet.org/register/"

# Exact filenames, matched exactly. Substring matching is unsafe here: the downloads
# page carries both `odis_json.zip` (One Day Internationals) and `odisha_json.zip`
# (the Odisha domestic side), and a naive `odis` match silently grabs the wrong one.
ARCHIVES = {
    "ipl": "ipl_json.zip",
    "tests": "tests_male_json.zip",
    "odis": "odis_male_json.zip",
    "t20is": "t20s_male_json.zip",
}

# The people register lives on a different page and is linked relatively.
REGISTER_FILES = {"register_people": "people.csv"}


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


@dataclass(frozen=True)
class Target:
    key: str
    filename: str
    url: str
    source_page: str


def page_hrefs(client: httpx.Client, url: str) -> list[str]:
    response = client.get(url)
    response.raise_for_status()
    collector = LinkCollector()
    collector.feed(response.text)
    return collector.hrefs


def resolve_from(
    client: httpx.Client, page: str, wanted: dict[str, str]
) -> list[Target]:
    hrefs = page_hrefs(client, page)

    # Index by the final path segment so an exact filename match is possible
    # regardless of whether the link is absolute or relative.
    by_filename: dict[str, str] = {}
    for href in hrefs:
        segment = href.rstrip("/").rsplit("/", 1)[-1]
        by_filename.setdefault(segment, href)

    targets = []
    for key, filename in wanted.items():
        href = by_filename.get(filename)
        if href is None:
            raise RuntimeError(
                f"Could not resolve '{filename}' on {page}. Cricsheet filenames have "
                f"changed before. Inspect the page and update ARCHIVES; do not guess "
                f"a URL."
            )
        targets.append(Target(key, filename, urljoin(page, href), page))
    return targets


def resolve_all(client: httpx.Client) -> list[Target]:
    return [
        *resolve_from(client, DOWNLOADS_PAGE, ARCHIVES),
        *resolve_from(client, REGISTER_PAGE, REGISTER_FILES),
    ]


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(client: httpx.Client, target: Target, dest, force: bool) -> dict:
    if dest.exists() and not force:
        print(f"  {target.filename:<24} already present, skipping")
    else:
        with client.stream("GET", target.url, follow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            written = 0
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)
                    written += len(chunk)
                    if total:
                        pct = 100 * written / total
                        print(
                            f"  {target.filename:<24} {pct:5.1f}%  "
                            f"{written / 1e6:7.1f} MB",
                            end="\r",
                        )
            print(f"  {target.filename:<24} downloaded {written / 1e6:7.1f} MB      ")

    return {
        "key": target.key,
        "filename": target.filename,
        "resolved_url": target.url,
        "source_page": target.source_page,
        "bytes": dest.stat().st_size,
        "sha256": sha256_of(dest),
    }


def main() -> None:
    force = "--force" in sys.argv
    resolve_only = "--resolve" in sys.argv

    out = data_dir()

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        print(f"Resolving against {DOWNLOADS_PAGE} and {REGISTER_PAGE}\n")
        targets = resolve_all(client)

        for target in targets:
            print(f"  {target.key:<16} -> {target.url}")
        print()

        if resolve_only:
            return

        entries = [fetch(client, t, out / t.filename, force) for t in targets]

    # Any previous archive audit is dropped rather than carried forward: it was keyed
    # to the checksums of the files it was computed from, and those may have just
    # changed. Re-run etl.inspect_teams to regenerate it.
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps({"files": entries}, indent=2) + "\n")

    print(f"\nManifest written to {manifest_path}")
    for entry in entries:
        print(f"  {entry['filename']:<24} {entry['bytes'] / 1e6:8.1f} MB  "
              f"sha256:{entry['sha256'][:16]}")


if __name__ == "__main__":
    main()
