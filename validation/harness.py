"""Pass/fail/skip reporting for the SPEC 8 checks.

SKIP exists because three of the checks target tables that later stages build, and
a check that silently passes because it had nothing to look at is worse than no
check: it reports the same green as a check that actually ran. So a skip carries a
reason, prints differently, and is counted in its own column.

A FAIL sets the exit code. A SKIP does not, but the summary says so out loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

SAMPLE = 10


@dataclass
class Result:
    number: int
    title: str
    outcome: str
    summary: str
    offenders: list[str] = field(default_factory=list)


def ok(number: int, title: str, summary: str) -> Result:
    return Result(number, title, PASS, summary)


def bad(number: int, title: str, summary: str, offenders: list[str]) -> Result:
    return Result(number, title, FAIL, summary, offenders[:SAMPLE])


def skipped(number: int, title: str, reason: str) -> Result:
    return Result(number, title, SKIP, reason)


def verdict(number: int, title: str, summary: str, offenders: list[str]) -> Result:
    """Pass when nothing was found to complain about, fail otherwise."""
    if offenders:
        return bad(number, title, summary, offenders)
    return ok(number, title, summary)


def report(results: list[Result]) -> int:
    width = max(len(r.title) for r in results)
    print()
    for r in results:
        print(f"  [{r.outcome}]  {r.number:>2}. {r.title:<{width}}  {r.summary}")
        for line in r.offenders:
            print(f"                {line}")
        if len(r.offenders) == SAMPLE:
            print("                ... (sample truncated)")

    counts = {name: sum(1 for r in results if r.outcome == name) for name in (PASS, FAIL, SKIP)}
    print(
        f"\n  {counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped"
        f"  ({len(results)} checks)"
    )
    if counts[SKIP]:
        print("  Skipped checks have NOT been verified. They are not passes.")
    return 1 if counts[FAIL] else 0
