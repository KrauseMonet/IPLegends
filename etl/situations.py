"""Everything the engine needs to play a player in a SITUATION [A260]: the league's
settling-in curve, and each player-season's measured leans -- by phase, against spin or pace,
and how slowly he settles -- plus his own settled share to take back out of his level.

One scan of the deliveries feeds all of it, so the order of operations is fixed here:

1. Par for each ball from its state (`etl.skills.add_par`), as A257/A258 measured it.
2. The settling curve from those balls (`etl.settling`), centred within each engine state.
3. Par ADJUSTED by that curve: a ball is now judged against a batter at that state with that
   many balls faced. Every lean below is measured against this par, so the engine, which
   adds the curve itself, does not count a death hitter's being set twice -- and the slow-burn
   lean becomes a player's settling relative to the league's, as it should be.
4. Each metric measured at career and season level (`etl.skills.measure`), and each
   player-season's lean read off its season value (its career where the season has no balls
   in that situation), centred over his own balls so a lean only moves WHERE his runs come,
   never how many over his real season (A248's rule, for everybody now).

Card fields it fills (runs a ball unless said otherwise): `phase_bat`/`phase_bowl` (powerplay,
middle, death; bowling is runs SAVED), `style_bat` (against spin, against pace), `burn`
(added before he is set, and once set) and `settle_offset` (his own mean curve adjustment
in runs, and its mean log dismissal multiplier, over his real balls), and [A265]
`phase_wkt` (a bowler's log dismissal multiplier by phase: where his wickets come).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from etl import settling, skills
from etl.state_model import bucket_of

PHASES = ("pp", "mid", "death")
STYLES = ("spin", "pace")

_MEMO: dict[int, dict] = {}


def apply_settling(balls: list[skills.Ball], table: dict) -> None:
    """Move each ball's par by the settling curve for its state and balls faced."""
    cells = {(o, wb, fb): (r, m) for o, wb, fb, r, m in table["cells"]}
    for b in balls:
        c = cells.get((b.over, bucket_of(b.wk), settling.faced_bucket(b.faced)))
        if c is not None:
            b.par_runs += c[0]
            b.par_out *= c[1]


def _centred(values: dict[str, float], shares: dict[str, float]) -> dict[str, float]:
    mean = sum(values[k] * shares.get(k, 0.0) for k in values)
    return {k: values[k] - mean for k in values}


def leans(balls: list[skills.Ball], measured: dict) -> dict[tuple[str, int], dict]:
    """{(person, season): {field: value}} for every player-season with balls in a discipline."""
    n_bat: dict = defaultdict(Counter)
    n_bowl: dict = defaultdict(Counter)
    x_wkt: dict = defaultdict(Counter)              # [A265] his expected wickets by phase
    for b in balls:
        nb = n_bat[(b.batter, b.season)]
        nb[skills.phase_of(b.over)] += 1
        if b.style in STYLES:
            nb[b.style] += 1
        nb["set" if b.faced >= skills.SETTLED else "new"] += 1
        n_bowl[(b.bowler, b.season)][skills.phase_of(b.over)] += 1
        x_wkt[(b.bowler, b.season)][skills.phase_of(b.over)] += b.par_wkt

    def value(metric: str, p: str, season: int) -> float:
        m = measured[metric]
        if (p, season) in m.season:
            return m.season[(p, season)]
        return m.career.get(p, 0.0)

    out: dict = defaultdict(dict)
    for (p, season), n in n_bat.items():
        tot = sum(n[ph] for ph in PHASES)
        lean = _centred({ph: value(ph, p, season) for ph in PHASES},
                        {ph: n[ph] / tot for ph in PHASES})
        out[(p, season)]["phase_bat"] = [round(lean[ph] / 6, 6) for ph in PHASES]
        st = n["spin"] + n["pace"]
        if st:
            sl = _centred({s: value(s, p, season) for s in STYLES},
                          {s: n[s] / st for s in STYLES})
            out[(p, season)]["style_bat"] = [round(sl[s] / 6, 6) for s in STYLES]
        sb = value("slow_burn", p, season) / 6           # set minus new, runs a ball
        new, set_ = n["new"], n["set"]
        if new + set_:
            # zero over his own balls: new balls lose set's share of the gap, set balls gain
            # new's share, so his season's runs are unchanged and only their timing moves
            out[(p, season)]["burn"] = [round(-sb * set_ / (new + set_), 6),
                                        round(sb * new / (new + set_), 6)]
    for (p, season), n in n_bowl.items():
        tot = sum(n[ph] for ph in PHASES)
        lean = _centred({ph: value(ph + "_econ", p, season) for ph in PHASES},
                        {ph: n[ph] / tot for ph in PHASES})
        out[(p, season)]["phase_bowl"] = [round(lean[ph] / 6, 6) for ph in PHASES]
        # [A265] where his wickets come: the death against the rest, as log dismissal
        # multipliers centred over his own EXPECTED WICKETS by phase -- not his balls, since
        # a death ball falls more wickets and a lean centred on balls would add some -- so
        # his wickets move toward (or away from) the death and his total stays his
        # dismissal multiplier's
        dl = value("death_wkt_lean", p, season)
        xw = x_wkt[(p, season)]
        xt = sum(xw.values())
        sd = xw["death"] / xt if xt > 0 else n["death"] / tot
        out[(p, season)]["phase_wkt"] = [round(-dl * sd, 6), round(-dl * sd, 6),
                                         round(dl * (1 - sd), 6)]
    return dict(out)


def position_mix(balls: list[skills.Ball]) -> dict[int, Counter]:
    """[A268] The balls faced at each place in the order: by phase, against each style, and
    new or set -- the mix a card's batting leans are centred on, since the engine plays a
    card at its positions, not at the balls its player really faced."""
    mix: dict[int, Counter] = defaultdict(Counter)
    for b in balls:
        m = mix[b.pos]
        m[skills.phase_of(b.over)] += 1
        if b.style in STYLES:
            m[b.style] += 1
        m["set" if b.faced >= skills.SETTLED else "new"] += 1
    return {p: Counter(m) for p, m in mix.items()}


BAT_LEANS = ("phase_bat", "style_bat", "burn")


def recentre(fields: dict, positions, mix: dict[int, Counter]) -> dict:
    """[A268] `fields` with its batting leans re-centred on the balls faced at `positions`.

    A260 centred each lean over the balls the player really faced that season, so it adds
    nothing over HIS season. The engine plays the card at its positions, though, and where
    those differ -- a season too thin to place him, so his career's place is used (A76); a
    finisher carded in the middle order -- the lean stops netting to zero there: Sunil
    Narine faced 7 balls in 2012, all at the death, yet his card opens, and his
    powerplay lean was played on nearly every ball, +4.7 runs a match nobody scored. Each
    lean keeps its SHAPE (where he scores more or less) and loses only its mean over the
    mix where he is played. The mix is the positions' balls pooled, so a place that faces
    more balls weighs more. `settle_offset` is not a lean and is untouched; nor are the
    bowling leans, whose gap is the engine's phase-blind rotation (A265), not centring."""
    pooled = Counter()
    for p in positions or ():
        pooled.update(mix.get(p, Counter()))
    out = dict(fields)

    def centre(vals, keys):
        n = sum(pooled[k] for k in keys)
        if vals is None or not n:
            return vals
        mean = sum(v * pooled[k] for v, k in zip(vals, keys)) / n
        return [round(v - mean, 6) for v in vals]

    out["phase_bat"] = centre(fields.get("phase_bat"), PHASES)
    out["style_bat"] = centre(fields.get("style_bat"), STYLES)
    out["burn"] = centre(fields.get("burn"), ("new", "set"))
    return out


def build(conn) -> dict:
    """{"table": settling table, "players": {(person, season): fields}, "balls": adjusted
    balls, "measured": metrics}. Memoised per connection: a snapshot build asks twice (the
    deck and the model inputs) and the scan is the expensive part."""
    key = id(conn)
    if key in _MEMO:
        return _MEMO[key]
    balls = skills.load(conn)                       # par from the state, A257
    table = settling.build(balls)
    offsets = settling.player_offsets(balls, table)
    apply_settling(balls, table)                    # par now knows balls faced
    measured = skills.measure(skills.accumulate(balls))
    players = leans(balls, measured)
    for (p, season), (r, logm) in offsets.items():
        players.setdefault((p, season), {})["settle_offset"] = [round(r, 6), round(logm, 6)]
    doc = {"table": table, "players": players, "balls": balls, "measured": measured,
           "pos_mix": position_mix(balls)}
    _MEMO.clear()
    _MEMO[key] = doc
    return doc
