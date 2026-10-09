"""Bingo's streak arithmetic, which lives in the page (A182). The repo has no JavaScript
harness, so this runs the one pure function under node, skipped where node is absent.

A streak is not broken until a day has been MISSED (A130), and consecutive means the next
calendar DAY, not the next day-of-month: 31 August to 1 September is one apart.
"""

import json
import pathlib
import re
import shutil
import subprocess

import pytest

JS = pathlib.Path(__file__).resolve().parent.parent / "web" / "static" / "bingo.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def streaks(days, today):
    src = JS.read_text()
    body = re.search(r"function bgDayNum[\s\S]*?(?=\nfunction bgPaintStreak)", src).group(0)
    out = subprocess.run(
        ["node", "-e", f"{body}; console.log(JSON.stringify(bgStreaks({json.dumps(days)}, "
                       f"{json.dumps(today)})))"],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("days,today,cur,best", [
    (["2026-10-07", "2026-10-08", "2026-10-09"], "2026-10-09", 3, 3),
    (["2026-10-07", "2026-10-08"], "2026-10-09", 2, 2),            # today still to play: alive
    (["2026-10-06", "2026-10-07"], "2026-10-09", 0, 2),            # a day was missed
    (["2026-08-30", "2026-08-31", "2026-09-01"], "2026-09-01", 3, 3),
    (["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"], "2026-10-07", 3, 3),
    ([], "2026-10-09", 0, 0),
    (["2026-10-09", "2026-10-09"], "2026-10-09", 1, 1),
])
def test_streaks(days, today, cur, best):
    assert streaks(days, today) == {"cur": cur, "best": best}
