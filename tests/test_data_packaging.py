"""Every committed snapshot in data/ reaches git AND the deployment (A107, A268, A278).

`/data/*` is ignored by both .gitignore and .vercelignore, so each snapshot the code reads has to
be let through by name in BOTH. Forgetting one is silent: the file loads locally, never reaches
production, and the feature falls back to nothing (A107's deck, A268's daily pin, A278's career
skill cut-offs were all caught by hand). This makes the third time a test instead of a habit."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _snapshots():
    return sorted(p.name for p in (ROOT / "data").glob("*.json.gz"))


def test_every_snapshot_is_let_through_git():
    names = _snapshots()
    assert names, "no snapshots found"
    r = subprocess.run(["git", "check-ignore", *[f"data/{n}" for n in names]], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.stdout.strip() == "", f"ignored by git: {r.stdout.split()}"


def test_every_snapshot_is_let_through_vercel():
    lines = {l.strip() for l in (ROOT / ".vercelignore").read_text().splitlines()}
    missing = [n for n in _snapshots() if f"!data/{n}" not in lines]
    assert not missing, f"not let through .vercelignore: {missing}"
