"""Role per franchise-season. SPEC 6.6.

The thresholds are asymmetric because the two workloads are not comparable. A team
faces about 120 balls spread over eleven batters and bowls 120 over five or six
bowlers, so the median player here faces 6 balls a match and bowls 12. A single
symmetric cutoff either calls half the deck all-rounders or almost none of them.

They were calibrated against twelve players whose roles are not in dispute, and the
values below are the ones that classify all twelve correctly. The window is genuinely
narrow, which is what makes the numbers falsifiable rather than taste:

    BAT_MIN  must be <= 10.5 to keep Jadeja an all-rounder, and > 5.4 to keep
             Narine a bowler.
    BOWL_MIN must be <= 7.8 to keep Pollard an all-rounder, and > 0.9 to keep
             Kohli a batter.
"""

from __future__ import annotations

# Balls faced per match played. SPEC A22: balls faced excludes wides only.
BAT_MIN = 9.0

# Legal balls bowled per match played - one over a match.
BOWL_MIN = 6.0

CALIBRATION = (
    # (player, balls faced/match, legal balls bowled/match, expected role)
    ("AD Russell", 11.5, 12.9, "allrounder"),
    ("HH Pandya", 12.3, 10.8, "allrounder"),
    ("RA Jadeja", 10.5, 16.1, "allrounder"),
    ("SR Watson", 19.1, 13.7, "allrounder"),
    ("KA Pollard", 12.4, 7.8, "allrounder"),
    ("AT Rayudu", 17.7, 0.0, "batter"),
    ("MS Dhoni", 14.8, 0.0, "batter"),
    ("V Kohli", 24.7, 0.9, "batter"),
    ("B Kumar", 1.9, 22.5, "bowler"),
    ("JJ Bumrah", 0.6, 23.0, "bowler"),
    ("R Ashwin", 3.2, 21.6, "bowler"),
    ("SP Narine", 5.4, 23.1, "bowler"),
)


def role_for(
    balls_faced: int, balls_bowled: int, matches: int, *, is_keeper: bool = False
) -> str | None:
    """None when the player never batted and never bowled.

    Fielding a dismissal counts as participation but says nothing about whether
    someone is a batter or a bowler, so there is no honest role to assign and the
    caller must leave them out rather than pick one.
    """
    if matches <= 0 or (balls_faced == 0 and balls_bowled == 0):
        return None
    if is_keeper:
        return "keeper"

    bats = balls_faced / matches >= BAT_MIN
    bowls = balls_bowled / matches >= BOWL_MIN
    if bats and bowls:
        return "allrounder"
    if bowls:
        return "bowler"
    if bats:
        return "batter"

    # Below both thresholds but not idle: a tail-end batter, or someone who bowled
    # two overs all season. Decided by which workload is larger relative to its own
    # threshold, so the asymmetry above is not quietly reintroduced here.
    return (
        "batter"
        if balls_faced / BAT_MIN >= balls_bowled / BOWL_MIN
        else "bowler"
    )
