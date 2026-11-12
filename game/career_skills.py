"""Skills a Build a Career season earns [A278].

A real card's skills are read off the archive (`etl.skills`): each season's balls against
par, measured at two levels, judged against cut-offs. Your player's seasons are simulated,
so they are judged by the SAME rules against cut-offs measured the same way from the engine
replaying every real season (`tools.snapshot_career_skills`) -- the engine reproduces most
skills well but not at the archive's rates (bowling ones often half as often), so archive
cut-offs would make a simulated season earn too few. A season's evidence is collected ball by
ball while it is played (`game.simulator.ball_log`, which draws nothing), its par being what an
average player would expect in that state after the league's settling curve.

Pure: reads the committed snapshot and nothing else. A skill is never handed out by a choice
(the plan's rule); choices move the engine's numbers, and the season earns the badge.
"""

from __future__ import annotations

import functools
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

from etl import skills as S

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "career_skill_cuts.json.gz"
FORMAT = 1
TAGS_COUNTED = tuple(S.COUNTS)
FIELDS = ("n", "resid", "sq", "events", "expected")
# Skills the engine does not reproduce, so a simulated season cannot be judged on them:
# their metric's simulated season value against the archive's, over the same real cards
# replayed four times (A278), is under A257's repeat bar of 0.20 -- Powerplay controller r
# 0.13, Middle-overs controller 0.12, Death-overs bowler 0.10. A badge the engine does not
# play is the contradiction A267 removed. Every other skill reproduces at r 0.29-0.68.
NOT_PLAYED = frozenset({"pp_controller", "mid_controller", "death_controller"})
CAREER_SKILLS = tuple(s for s in S.SKILLS if s.key not in NOT_PLAYED)


# --- collecting a season, ball by ball ------------------------------------------------------

def _tags(disc: str, over: int, faced: int, style: str | None) -> tuple[str, ...]:
    """etl.skills' own tags for a ball (`S._tags`), without needing an archive Ball."""
    if disc == "bat":
        t = ["all", S.phase_of(over), "set" if faced >= S.SETTLED else "new"]
        if style in ("spin", "pace"):
            t.append(style)
        return tuple(t)
    return ("all", S.phase_of(over))


class Collector:
    """A `ball_log` callback summing each player's balls into `etl.skills.Acc`s by tag.
    `who` limits it to one person (your player); None collects everybody (the tool)."""

    def __init__(self, who: str | None = None):
        self.who = who
        self.acc: dict = defaultdict(lambda: defaultdict(S.Acc))   # (person, disc) -> tag

    def __call__(self, over, wickets, striker, bowler, outcome, par) -> None:
        runs = 0 if outcome == 0 else _off(outcome)
        out = outcome == 0
        p_runs, p_out, p_dot, p_bound, p_six = par
        for disc, card in (("bat", striker), ("bowl", bowler)):
            pid = card.player.person_id
            if self.who is not None and pid != self.who:
                continue
            d = self.acc[(pid, disc)]
            r = (runs - p_runs) * (1 if disc == "bat" else -1)
            style = bowler.player.bowling_style if disc == "bat" else None
            for t in _tags(disc, over, striker.balls, style):
                a = d[t]
                a.n += 1
                a.resid += r
                a.sq += r * r
                a.events += out          # every engine dismissal is the bowler's (A116)
                a.expected += p_out
            for t, ev, ex in (("~dot", runs == 0, p_dot),
                              ("~bound", runs >= 4, p_bound), ("~six", runs >= 6, p_six)):
                a = d[t]
                a.n += 1
                a.events += ev
                a.expected += ex

    def summary(self, who: str) -> dict:
        """{disc: {tag: [n, resid, sq, events, expected]}} for one person: what a season
        record keeps, so a later season can judge him against his career so far."""
        # three decimals: a season's sums are tens to hundreds, so this moves no verdict, and
        # the token (A199) carries one of these a season
        return {disc: {t: [round(getattr(a, f), 3) for f in FIELDS] for t, a in sorted(d.items())}
                for (pid, disc), d in sorted(self.acc.items()) if pid == who}


def _off(outcome: int) -> int:
    from game.simulator import OFF_THE_BAT
    return OFF_THE_BAT[outcome - 1]


def accs(summary: dict | None, disc: str) -> dict:
    """A kept summary back to {tag: Acc}."""
    out: dict = defaultdict(S.Acc)
    for t, v in ((summary or {}).get(disc) or {}).items():
        out[t] = S.Acc(int(v[0]), *map(float, v[1:]))
    return out


# --- judging ---------------------------------------------------------------------------------

def read_document() -> dict | None:
    try:
        with gzip.open(SNAPSHOT, "rt") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == FORMAT else None


@functools.lru_cache(maxsize=1)
def document() -> dict:
    """The cut-offs, or none: a missing snapshot means no career skills, never guessed ones."""
    return read_document() or {"cuts": {}, "spreads": {}, "pooled_var": {}, "played_sd": {}}


def metric_values(season: dict, before: dict, career: dict, m: S.Metric,
                  spreads: dict) -> dict | None:
    """One metric for one season, as `etl.skills.measure` reads a real one: `season` shrunk
    toward his career before it (`before`), `alone` toward the league, `played` as the
    engine plays it, and his career (this season included) shrunk toward the league."""
    cv, sv = spreads[m.key]
    v = S.value(m, season)
    if v is None:
        return None
    rest = S.value(m, before) if before else None
    if rest is None or cv <= 0:
        pm, pv = 0.0, cv
    else:
        pm, pv = S._shrink(rest[0], rest[1], 0.0, cv), cv * rest[1] / (cv + rest[1])
    car = S.value(m, career)
    return {"season": S._shrink(v[0], v[1], pm, pv + sv),
            "alone": S._shrink(v[0], v[1], 0.0, cv + sv),
            "played": S.played(m, season), "balls": v[2],
            "career": S._shrink(car[0], car[1], 0.0, cv) if car else None,
            "career_balls": car[2] if car else 0}


def _combo(kind: str, a: dict, b: dict, spreads: dict, field_: str) -> float | None:
    """Anchor/aggressor (`style`) or run machine (`complete`), as `S._style`/`S._complete`."""
    if a.get(field_) is None or b.get(field_) is None:
        return None
    (c1, s1), (c2, s2) = spreads["scoring"], spreads["survival"]
    if kind == "style":
        if field_ == "career":
            return b[field_] / (math.sqrt(c2) or 1.0) - a[field_] / (math.sqrt(c1) or 1.0)
        return b[field_] / (math.sqrt(c2 + s2) or 1.0) - a[field_] / (math.sqrt(c1 + s1) or 1.0)
    return a[field_] + S.WICKET_RUNS_AN_OVER * b[field_]


def _strength(skill: S.Skill, v: float, spreads: dict) -> float:
    if skill.metric == "style":
        return v
    if skill.metric == "complete":
        (c1, s1), (c2, s2) = spreads["scoring"], spreads["survival"]
        t = c1 + s1 + S.WICKET_RUNS_AN_OVER ** 2 * (c2 + s2)
    else:
        t = sum(spreads[skill.metric])
    return v / math.sqrt(t) if t > 0 else 0.0


def judge(seasons: list[dict | None], doc: dict | None = None) -> list[list]:
    """The skills the LAST of `seasons` earned, [[key, tier, strength], ...] strongest first,
    each season a kept summary (None for one played before A278, which counts for nothing).
    `etl.skills.season_skills`' rule for one player, against the engine-measured cuts."""
    doc = document() if doc is None else doc
    if not seasons or seasons[-1] is None or not doc.get("cuts"):
        return []
    S.POOLED_VAR.update(doc.get("pooled_var") or {})     # what `value` reads a season's noise against
    spreads = {k: tuple(v) for k, v in doc["spreads"].items()}
    out = []
    by_disc = {}
    for disc in ("bat", "bowl"):
        this = accs(seasons[-1], disc)
        before = S._add(accs(x, disc) for x in seasons[:-1] if x)
        by_disc[disc] = (this, before, S._add([before, this]))
    vals = {}
    for m in S.METRICS:
        this, before, career = by_disc[m.disc]
        if this.get("all") is None:
            continue
        got = metric_values(this, before, career, m, spreads)
        if got is not None:
            vals[m.key] = got
    for skill in CAREER_SKILLS:
        cut = doc["cuts"].get(skill.key)
        if not cut:
            continue
        if skill.metric in ("style", "complete"):
            a, b = vals.get("scoring"), vals.get("survival")
            if a is None or b is None:
                continue
            got = {f: _combo(skill.metric, a, b, spreads, f)
                   for f in ("season", "played", "career")}
            got["alone"] = got["season"]
            got = {k: None if v is None else skill.sign * v for k, v in got.items()}
            got["balls"], got["career_balls"], floor = a["balls"], a["career_balls"], "all"
        else:
            got = vals.get(skill.metric)
            if got is None:
                continue
            got = dict(got)
            for f in ("season", "alone", "played", "career"):
                if got[f] is not None:
                    got[f] = skill.sign * got[f]
            floor = S.METRIC_BY_KEY[skill.metric].floor
        if got["balls"] < S.SEASON_FLOOR[floor]:
            continue
        season_v = got["alone"] if skill.tier == 3 else got["season"]
        backed = "season" in cut and season_v is not None and season_v >= cut["season"]
        if backed and skill.tier == 3 and not (
                (got["career"] or -1e9) >= cut["career"]
                and got["career_balls"] >= S.POOL_FLOOR[floor]):
            backed = False
        alone = "played" in cut and (got["played"] if got["played"] is not None
                                     else -1e9) >= cut["played"]
        if not (backed or alone):
            continue
        sts = [_strength(skill, season_v, spreads)] if backed else []
        sd = doc.get("played_sd", {}).get(skill.key) or 0.0
        if alone and sd > 0:
            sts.append(got["played"] / sd)
        if sts:
            out.append([skill.key, skill.tier, round(max(sts), 4)])
    return sorted(out, key=lambda x: (-x[2], x[0]))


def shown(earned: list[list], rating: int | None, role: str | None = None,
          at: dict[int, int] | None = None) -> list[list]:
    """What the season card shows: one skill per tier the Rating has opened, the clearest in
    each, in the disciplines his role shows -- `game.skills.card_skills`' rule (tiers open at
    80 / 87 / 94; a batter or keeper shows no bowling skill, a bowler shows batting ones he
    earned)."""
    from game.skills import DISC_FOR_ROLE, TIER_BLOCKS
    at = at or TIER_BLOCKS
    discs = DISC_FOR_ROLE.get(role or "", ("bat", "bowl"))
    open_ = {t for t, v in at.items() if (rating or 0) >= v}
    best: dict[int, list] = {}
    for key, tier, st in earned:
        if S.SKILL_BY_KEY[key].disc not in discs:
            continue
        if tier in open_ and (tier not in best or st > best[tier][2]):
            best[tier] = [key, tier, st]
    return [best[t] for t in sorted(best)]


def label(key: str) -> str:
    return S.SKILL_BY_KEY[key].label


def signature(seasons) -> list[list]:
    """The career's skills: every one a season card showed, [key, tier, seasons held], the
    highest tier first, then the longest held -- what the career card and the Hall of Fame
    show. `seasons` are season records (anything with `.skills`)."""
    held: dict[str, list] = {}
    for r in seasons:
        for key, tier, _st in getattr(r, "skills", None) or []:
            h = held.setdefault(key, [key, tier, 0])
            h[2] += 1
    return sorted(held.values(), key=lambda x: (-x[1], -x[2], x[0]))


# --- [A285] mastered: a skill shown often enough becomes part of the career for good --------

MASTER_SEASONS = 4            # seasons a skill must be shown in to be mastered (declared)


def mastered(seasons) -> list[list]:
    """The skills the career has MASTERED, [key, tier, year mastered], in the order they were
    mastered. A statement about the career's history -- the seasons a skill was shown in --
    never about the next season, so it stays honest when the season-by-season badge goes:
    the badge says how you play now, mastery says what you were."""
    count: dict[str, int] = {}
    out = []
    for r in seasons:
        for key, tier, _st in getattr(r, "skills", None) or []:
            count[key] = count.get(key, 0) + 1
            if count[key] == MASTER_SEASONS:
                out.append([key, tier, getattr(r, "year", None)])
    return out


def mastered_from_signature(sig) -> list[list]:
    """Mastery as a saved card's signature ([key, tier, seasons held]) can show it: the year
    is not kept there, so it reads None."""
    return [[k, t, None] for k, t, n in sig or [] if n >= MASTER_SEASONS]


# --- [A282] how the skills are shown: slots and combinations --------------------------------

TIER_NAMES = {1: "Style", 2: "Special", 3: "Elite"}

# Two skills held in the same season, named together. Declared game design: a name for a pair
# the season card already shows, never a third skill and never a bonus. `shown` keeps one skill
# per tier, so a pair is only ever two different tiers.
COMBOS = (
    ("aggressor", "death_hitter", "Finisher"),
    ("aggressor", "pp_specialist", "Pinch-hitter"),
    ("slow_burner", "death_hitter", "Builds, then explodes"),
    ("anchor", "run_machine", "Rock of the order"),
    ("boundary_hitter", "big_hitter", "Clean striker"),
    ("mid_accumulator", "spin_basher", "Owns the middle overs"),
    ("pp_starter", "pace_basher", "Takes on the new ball"),
    ("hard_to_hit", "miser", "Impossible to get away"),
    ("miser", "new_ball", "New-ball controller"),
    ("strike_bowler", "death_bowler", "Death strike bowler"),
    ("death_wickets", "death_bowler", "Closes out innings"),
    ("strike_bowler", "mid_master", "Middle-overs wicket-taker"),
)


def combos(keys) -> list[str]:
    """The combinations a set of skill keys makes, in the table's order."""
    have = set(keys)
    return [name for a, b, name in COMBOS if a in have and b in have]


def combo_hint(keys, role: str | None = None) -> str:
    """The nearest combination: one skill held, the other not yet -- named once, so a player
    knows what would complete it. Only pairs whose missing half the role can show."""
    from game.skills import DISC_FOR_ROLE
    discs = DISC_FOR_ROLE.get(role or "", ("bat", "bowl"))
    have = set(keys)
    for a, b, name in COMBOS:
        for held, need in ((a, b), (b, a)):
            if held in have and need not in have and S.SKILL_BY_KEY[need].disc in discs:
                return f"Add {label(need)} to {label(held)} for “{name}”"
    return ""


def slots(skills_shown: list[list], rating: int | None) -> list[dict]:
    """The player card's three slots: each tier, whether the Rating has opened it, and the
    skill the last season showed in it (if any)."""
    from game.skills import TIER_BLOCKS
    held = {t: k for k, t, *_ in skills_shown or []}
    return [{"tier": t, "name": TIER_NAMES[t], "opens_at": TIER_BLOCKS[t],
             "open": (rating or 0) >= TIER_BLOCKS[t], "key": held.get(t)}
            for t in sorted(TIER_BLOCKS)]


# A228: the career player is the person at the keyboard, so a skill describing him says no
# he/his. The real cards' descriptions stay as they are; these replace the ones that need it.
CAREER_BLURBS = {
    "anchor": "Values the wicket: gets out far less than par, by more than the scoring beats it",
    "aggressor": "Plays for runs: out-scores par by more than survival beats it",
    "pp_starter": "Bats best in the powerplay: better there than in the other overs",
    "mid_accumulator": "Bats best in overs 7-15: better there than in the other overs",
    "pp_controller": "Bowls best in the powerplay: tighter there than in the other overs",
    "mid_controller": "Bowls best in overs 7-15: tighter there than in the other overs",
    "death_controller": "Bowls best at the death: tighter there than in the other overs",
    "miser": "Concedes well below par for the situations bowled in",
}


def blurb(key: str) -> str:
    return CAREER_BLURBS.get(key) or S.SKILL_BY_KEY[key].blurb
