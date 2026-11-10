"""Who to buy in an auction room: a legal twelve, two backups for each buy, and a
walk-away price for each. [A280]

    uv run python -m tools.auction_picks ROOMCODE --me KKR --keep "V Kohli 2016, JJ Bumrah 2020"
    uv run python -m tools.auction_picks ROOMCODE --me Koustav --base http://127.0.0.1:8000

Reads only what any player in the room can see, through the two public endpoints
(`GET /api/rooms/{code}` and `/auction/sets`): the catalogue and how each lot went, every
team's purse, squad size and overseas count, and the sale prices so far. It never reads
the computer teams' hidden ceilings -- the game keeps those from every player on purpose
(A137), and in a friends' room using them would be playing with information nobody else
has. The deck snapshot supplies each card's engine value; no database is touched.

**What the room does NOT show is who you retained.** A mega auction removes retained
players from the catalogue and the public view lists only a count, so name them with
--keep. Players you have already bought are found from the sales, so do not list those.

Value is runs a match over an average player (`game.career.legend_values`, A234/A269), not
the 70-99 card, and a twelve counts the bowling of its five best bowlers only. Price is the
median paid in this room for players rated within two points, falling back to the value
curve scaled by what A232 measured an auction pays against it (open 1.29x, mega 0.71x).

The plan is a hill-climb over legal twelves, so it is not provably optimal; the tests check
it against exhaustive search on small pools.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import re
import statistics
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import game.auction as au
from etl.feasibility import BOWLERS_IN_TWELVE
from game.career import legend_values
from game.site import SITE_ORIGIN
from tools import snapshot_deck

TWELVE = 12
RESERVE = au.MIN_PRICE                    # every place not bought for the twelve costs this
SALES_MIN = 5                             # comparable sales before the room's own prices are trusted
BAND_WIDE = (2, 4)                        # rating points either side, tried in this order
# A232: how much an auction pays against `value_curve`, measured over 40 auctions each.
PRICE_PRIOR = {False: 1.29, True: 0.71}
PRICE_CAP = int(au.MAX_SHARE * au.PURSE)  # the computer teams stop here, so the price does too
RESTARTS = 8
LABEL = ("top 1-3", "middle 3-5", "finisher 5-7", "lower 8-11")


class PlanError(ValueError):
    """Something the caller can fix: a wrong code, an ambiguous name, a room not started."""


# --- reading the room -------------------------------------------------------------------


def fetch(base: str, path: str) -> dict:
    req = urllib.request.Request(base.rstrip("/") + path,
                                 headers={"User-Agent": "auction-picks/1", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.load(exc).get("detail", "")
        except Exception:
            pass
        raise PlanError(f"{path}: HTTP {exc.code} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise PlanError(f"{path}: {exc.reason}") from exc


# --- the deck ---------------------------------------------------------------------------


def card_key(c) -> tuple[str, int]:
    return (c.person_id, c.fs_id)


def index_deck(deck) -> dict[tuple[str, int | None], list]:
    out: dict[tuple[str, int | None], list] = {}
    for cards in deck.cards_by_fs.values():
        for c in cards:
            out.setdefault((c.name.lower(), c.season_year), []).append(c)
    return out


def parse_keep(spec: str, index: dict) -> list:
    """'V Kohli 2016, JJ Bumrah 2020' -> cards. A name that matches several seasons or several
    people is an error that lists them, never a guess."""
    cards = []
    for part in re.split(r"[;,]", spec or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.*?)\s+((?:19|20)\d\d)$", part)
        name, year = (m.group(1), int(m.group(2))) if m else (part, None)
        hits = [c for (n, y), cs in index.items() for c in cs
                if name.lower() in n and (year is None or y == year)]
        exact = [c for c in hits if c.name.lower() == name.lower()]
        hits = exact or hits
        if not hits:
            raise PlanError(f"--keep: nobody matches '{part}'")
        if len(hits) > 1:
            opts = ", ".join(f"{c.name} {c.season_year} ({c.display})" for c in hits[:8])
            raise PlanError(f"--keep: '{part}' is ambiguous: {opts}. Add the season.")
        cards.append(hits[0])
    return cards


# --- values and prices ------------------------------------------------------------------


@dataclass
class Val:
    bat: float
    bowl: float | None


def values_of(cards) -> dict[tuple, Val]:
    out = {}
    for c in cards:
        bat, bowl = legend_values(c)
        out[card_key(c)] = Val(bat, bowl)
    return out


def twelve_value(cards, vals: dict) -> float:
    """Runs a match the twelve adds over an average side: every batter's batting, and the
    bowling of the five best bowlers only -- a sixth bowler is a change bowler (A239)."""
    bat = sum(vals[card_key(c)].bat for c in cards)
    bowls = sorted((vals[card_key(c)].bowl for c in cards if vals[card_key(c)].bowl is not None),
                   reverse=True)
    return bat + sum(bowls[:BOWLERS_IN_TWELVE])


def price_model(sold: list[tuple[int, int]], mega: bool):
    """price(display) in lakh from this room's sales: the median of sales within two rating
    points, then four, then the value curve scaled by the room's own ratio (or A232's prior)."""
    ratios = [p / au.value_curve(r, mega) for r, p in sold if r]
    scale = statistics.median(ratios) if len(ratios) >= SALES_MIN else PRICE_PRIOR[mega]

    def price(display: int | None, base: int) -> int:
        d = display or 70
        for band in BAND_WIDE:
            comp = [p for r, p in sold if r and abs(r - d) <= band]
            if len(comp) >= SALES_MIN:
                return max(base, min(PRICE_CAP, round(statistics.median(comp))))
        return max(base, min(PRICE_CAP, round(au.value_curve(d, mega) * scale)))

    return price


# --- the plan ---------------------------------------------------------------------------


@dataclass
class Cand:
    card: object
    lot: int
    set_label: str
    price: int            # expected, lakh
    base: int
    status: str           # upcoming | current | unsold

    @property
    def key(self):
        return card_key(self.card)


@dataclass
class Backup:
    cand: Cand
    value_after: float


@dataclass
class Target:
    cand: Cand
    gain: float                           # what he adds over the best backup swap
    walk_away: int
    backups: list[Backup] = field(default_factory=list)


@dataclass
class Plan:
    me: str
    franchise: str
    purse: int
    open_places: int
    owned: list                           # cards already in the squad
    retained: list
    holes: int
    twelve: list                          # real cards in the planned twelve
    targets: list[Target]
    value: float
    spend: int
    budget: int                           # what the twelve's buys may cost above the reserve
    spare: int
    lam: float                            # runs a match per lakh at the margin
    warnings: list[str]
    sales_seen: int


def _search(owned_free: list, pool: list[Cand], forced: list, holes: int, budget: int,
            open_places: int, overseas_room: int, vals: dict, seed: int, budget_for=None):
    """Hill-climb over legal twelves. A twelve is `forced` plus owned and pool cards; only
    pool cards cost, and the cost above the reserve must fit `budget`. Returns
    (twelve, picks) or None."""
    size = TWELVE - holes
    by_key = {c.key: c for c in pool}
    free = {card_key(c): c for c in owned_free}
    forced_keys = {card_key(c) for c in forced}
    options = list(free.values()) + [c.card for c in pool if c.key not in free]

    def cost(keys) -> tuple[int, int, int]:
        picks = [by_key[k] for k in keys if k in by_key and k not in free]
        return (sum(p.price - RESERVE for p in picks), len(picks),
                sum(p.card.overseas is True for p in picks))

    def legal(keys) -> bool:
        cards = [lookup[k] for k in keys]
        spend, n, ov = cost(keys)
        return (spend <= budget and n <= open_places and ov <= overseas_room
                and au.twelve_feasible(cards, holes))

    lookup = {card_key(c): c for c in options}

    def score(keys) -> float:
        return twelve_value([lookup[k] for k in keys], vals)

    rng = random.Random(seed)
    best: tuple[float, frozenset] | None = None
    for restart in range(RESTARTS):
        lam = [0.0, 0.002, 0.004, 0.008, 0.016, 0.03][restart % 6]

        def marginal(c):
            v = vals[card_key(c)]
            p = by_key[card_key(c)].price if card_key(c) in by_key and card_key(c) not in free else 0
            return v.bat + (v.bowl or 0) - lam * p + rng.random() * 0.05

        cur = set(forced_keys)
        if not au.twelve_feasible([lookup[k] for k in cur], holes + size - len(cur)):
            return None
        ranked = sorted((c for c in options if card_key(c) not in cur), key=marginal, reverse=True)
        for c in ranked:
            if len(cur) >= size:
                break
            trial = cur | {card_key(c)}
            spend, n, ov = cost(trial)
            if spend <= budget and n <= open_places and ov <= overseas_room and au.twelve_feasible(
                    [lookup[k] for k in trial], holes + size - len(trial)):
                cur = trial
        if len(cur) != size or not legal(cur):
            continue
        cur_score = score(cur)
        improved = True
        while improved:
            improved = False
            best_swap = None
            for out in cur - forced_keys:
                rest = cur - {out}
                for c in options:
                    k = card_key(c)
                    if k in cur:
                        continue
                    trial = rest | {k}
                    s = score(trial)
                    if s <= cur_score + 1e-9 or (best_swap is not None and s <= best_swap[0]):
                        continue
                    if legal(trial):
                        best_swap = (s, trial)
            if best_swap:
                cur_score, cur = best_swap
                improved = True
        if best is None or cur_score > best[0] + 1e-9:
            best = (cur_score, frozenset(cur))
    if best is None:
        return None
    return best[1], lookup, best[0]


@dataclass
class Inputs:
    """Everything the search needs, read once from the room: kept apart from the search so
    the tests can check the search against exhaustive enumeration over the same inputs."""
    mine: dict
    owned: list              # bought so far, found from the sales
    retained: list           # named by the caller
    holes: int
    pool: list               # every lot still to come, unsold ones included
    movable: list            # the pool after dominance pruning, unsold lots left out
    vals: dict
    budget: int
    open_places: int
    ov_room: int
    warnings: list
    sales_seen: int


def prepare(room: dict, sets: dict, deck, me: str, keep: list) -> Inputs:
    a = room.get("auction")
    if not a:
        raise PlanError("this room has no auction")
    teams = a.get("teams") or []
    if room.get("status") == "lobby" or not teams:
        raise PlanError("the auction has not started yet -- run this once the bidding opens")
    mega = bool(a.get("mega"))
    mine = _find_team(teams, me)
    index = index_deck(deck)
    warnings: list[str] = []

    lots, sold = [], []
    mine_bought: list[tuple] = []
    for st in sets.get("sets", []):
        for L in st["lots"]:
            L = dict(L, set_label=st.get("label") or st["code"])
            lots.append(L)
            if L["status"] == "sold" and L["rating"]:
                sold.append((L["rating"], L["price"]))
            if L["status"] == "sold" and L["team"] == mine["short"]:
                mine_bought.append(L)
    if not lots:
        raise PlanError("the catalogue is empty: in a mega auction it fills once retentions finish")

    def join(L):
        hits = index.get((L["name"].lower(), L["season_year"]), [])
        if len(hits) > 1:
            hits = [c for c in hits if c.franchise == L["franchise"]] or hits
        return hits[0] if hits else None

    owned, owned_price = [], {}
    for L in mine_bought:
        c = join(L)
        if c is None:
            warnings.append(f"could not match your signing {L['name']} {L['season_year']} to a card")
            continue
        owned.append(c)
        owned_price[card_key(c)] = L["price"]
    retained = [c for c in keep if card_key(c) not in {card_key(o) for o in owned}]
    owned_all = owned + retained
    if len(keep) != mine.get("retained", 0) and mega:
        warnings.append(f"you listed {len(keep)} keepers; the room shows {mine.get('retained', 0)} retained")

    holes = max(0, mine["players"] - len(owned_all))
    if holes:
        warnings.append(f"{holes} of your squad members are not accounted for (a career legend, or "
                        f"a retention you did not list): treated as {holes} unknown card(s) already in "
                        f"your twelve, worth nothing to the plan")
    ov_known = sum(c.overseas is True for c in owned_all)
    ov_room = max(0, au.SQUAD_OVERSEAS_CAP - max(mine["overseas"], ov_known))
    open_places = au.SQUAD_SIZE - mine["players"]
    budget = mine["purse"] - RESERVE * open_places
    if budget < 0:
        raise PlanError("your purse cannot even cover the reserve for your open places")

    price = price_model(sold, mega)
    pool: list[Cand] = []
    unmatched = 0
    for L in lots:
        if L["status"] not in ("upcoming", "current", "unsold"):
            continue
        c = join(L)
        if c is None:
            unmatched += 1
            continue
        if c.person_id in {o.person_id for o in owned_all}:
            continue
        pool.append(Cand(c, L["lot"], L["set_label"],
                         L["base"] if L["status"] == "unsold" else price(L["rating"], L["base"]),
                         L["base"], L["status"]))
    if unmatched:
        warnings.append(f"{unmatched} lot(s) could not be matched to a card and were left out")

    vals = values_of([c.card for c in pool] + owned_all)
    movable = prune([c for c in pool if c.status != "unsold"], vals)  # unsold lots are backups only
    return Inputs(mine, owned, retained, holes, pool, movable, vals, budget, open_places,
                  ov_room, warnings, len(sold))


def plan_room(room: dict, sets: dict, deck, me: str, keep: list, seed: int = 0) -> Plan:
    return plan_from(prepare(room, sets, deck, me, keep), seed)


def plan_from(i: Inputs, seed: int = 0) -> Plan:
    owned, retained, holes, pool, movable = i.owned, i.retained, i.holes, i.pool, i.movable
    vals, budget, open_places, ov_room, mine = i.vals, i.budget, i.open_places, i.ov_room, i.mine
    res = _search(owned + retained, movable, retained, holes, budget, open_places, ov_room, vals, seed)
    if res is None:
        raise PlanError("no legal twelve fits your purse and places; the room may be too far along")
    keys, lookup, value = res
    twelve = [lookup[k] for k in keys]
    by_key = {c.key: c for c in pool}
    picks = [by_key[k] for k in keys if k in by_key and k not in {card_key(c) for c in owned}]
    picks.sort(key=lambda c: c.lot)

    lam = _lambda(owned, movable, retained, holes, budget, open_places, ov_room, vals, seed, value)
    targets = _backups(picks, twelve, pool, owned, holes, budget, open_places, ov_room, vals, lam,
                       budget)
    spend = sum(c.price for c in picks)
    return Plan(me=mine["short"], franchise=mine["franchise"], purse=mine["purse"],
                open_places=open_places, owned=owned, retained=retained, holes=holes,
                twelve=twelve, targets=targets, value=value, spend=spend, budget=budget,
                spare=budget - sum(c.price - RESERVE for c in picks), lam=lam,
                warnings=i.warnings, sales_seen=i.sales_seen)


def prune(pool: list[Cand], vals: dict) -> list[Cand]:
    """Drop every candidate that another of the same kind beats on value and price. Exact for
    the search: swapping the dominated card for its dominator keeps the twelve legal (same
    band, bowling, keeping and passport) and never lowers its value or raises its cost."""
    groups: dict[tuple, list[Cand]] = {}
    for c in pool:
        k = c.card
        v = vals[c.key]
        groups.setdefault((au.band(k), k.has_bowl, k.keeper_eligible, k.overseas is True,
                           v.bowl is None), []).append(c)
    keep = []
    for g in groups.values():
        g.sort(key=lambda c: (c.price, c.lot))
        front: list[Cand] = []
        for c in g:                       # ascending price: kept only if nothing cheaper beats it
            v = vals[c.key]
            if not any(vals[f.key].bat >= v.bat and (v.bowl is None or vals[f.key].bowl >= v.bowl)
                       for f in front):
                front.append(c)
        keep.extend(front)
    return keep


def _find_team(teams: list[dict], me: str) -> dict:
    q = me.lower()
    for key in ("short", "owner", "franchise"):
        hits = [t for t in teams if (t.get(key) or "").lower() == q]
        if hits:
            return hits[0]
    hits = [t for t in teams if q in (t.get("owner") or "").lower() or q in t["franchise"].lower()]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(f"{t['short']} ({t.get('owner') or 'computer'})" for t in teams)
    raise PlanError(f"--me '{me}' matches {len(hits)} teams. Teams: {names}")


def _lambda(owned, movable, retained, holes, budget, open_places, ov_room, vals, seed, value):
    """Runs a match per lakh at the margin: how much the twelve loses for a slightly smaller
    purse. Falls back to the plan's average density when the plan is not budget-bound."""
    cut = max(RESERVE, int(budget * 0.05))
    res = _search(owned + retained, movable, retained, holes, budget - cut, open_places, ov_room, vals, seed)
    drop = value - res[2] if res else value
    if drop > 1e-9:
        return drop / cut
    return 1e-6


def _backups(picks, twelve, pool, owned, holes, budget, open_places, ov_room, vals, lam, slack):
    owned_keys = {card_key(c) for c in owned}
    chosen = {c.key for c in picks}
    twelve_v = twelve_value(twelve, vals)
    cost_now = sum(c.price - RESERVE for c in picks)
    by_key = {c.key: c for c in pool}
    cards = {card_key(c): c for c in twelve}
    used: set = set()
    out: dict[int, Target] = {}
    # biggest buys choose their backups first, so a backup is promised to one target only
    for tgt in sorted(picks, key=lambda c: -(vals[c.key].bat + (vals[c.key].bowl or 0))):
        rest = [c for k, c in cards.items() if k != tgt.key]
        options = []
        for c in pool:
            if c.key in chosen or c.key in owned_keys or c.key in used:
                continue
            if not (c.lot > tgt.lot or c.status == "unsold"):
                continue                      # a backup is for AFTER you lose the target
            trial = rest + [c.card]
            spend = cost_now - (tgt.price - RESERVE) + (c.price - RESERVE)
            ov = (sum(by_key[k].card.overseas is True for k in chosen if k != tgt.key)
                  + (c.card.overseas is True))
            if spend > slack or ov > ov_room:
                continue
            if not au.twelve_feasible(trial, holes):
                continue
            options.append(Backup(c, twelve_value(trial, vals)))
        options.sort(key=lambda b: (-b.value_after, b.cand.price, b.cand.lot))
        top = options[:2]
        used.update(b.cand.key for b in top)
        gain = twelve_v - (top[0].value_after if top else twelve_v - 0.0)
        wa = top[0].cand.price + gain / lam if top else tgt.price
        if lam <= 1e-6:                       # money is not scarce: share the spare between the buys
            wa = tgt.price + max(0, slack - cost_now) / max(1, len(picks))
        walk = min(_snap_up(int(max(wa, tgt.price))), _snap(slack + RESERVE))
        out[tgt.lot] = Target(tgt, gain if top else 0.0, walk, top)
    return [out[c.lot] for c in picks]


def _snap(p: int) -> int:
    """Down to a price the ladder actually has."""
    q = au.MIN_PRICE
    while au.next_price(q) <= p:
        q = au.next_price(q)
    return q


def _snap_up(p: int) -> int:
    """Up to the next ladder price: an estimate is not on the ladder, and a walk-away rounded
    DOWN could land under it."""
    q = _snap(p)
    return q if q >= p else au.next_price(q)


# --- output -----------------------------------------------------------------------------


def _short(c) -> str:
    return f"{c.name} {c.season_year} ({c.display})"


def _tag(c, v: Val) -> str:
    roles = []
    roles.append(LABEL[au.band(c)])
    if c.keeper_eligible:
        roles.append("keeper")
    if v.bowl is not None:
        roles.append(f"bowls {c.bowling_style or ''}".strip())
    if c.overseas is True:
        roles.append("OVERSEAS")
    return ", ".join(roles)


def render(p: Plan, vals: dict) -> str:
    L = []
    L.append(f"{p.franchise} ({p.me})  purse {au.crore(p.purse)}  {p.open_places} places open  "
             f"[{p.sales_seen} sales seen]")
    for w in p.warnings:
        L.append(f"  ! {w}")
    L.append("")
    if p.retained or p.owned:
        L.append("Already yours (in the plan, no cost left):")
        for c in p.retained + p.owned:
            v = vals[card_key(c)]
            L.append(f"  {_short(c):38} {v.bat + (v.bowl or 0):+5.1f}  {_tag(c, v)}"
                     + ("   [retained]" if c in p.retained else ""))
        L.append("")
    L.append("BUY, in the order the lots come up:")
    for t in p.targets:
        c, v = t.cand, vals[t.cand.key]
        flag = "  (live now)" if c.status == "current" else ""
        L.append(f"  lot {c.lot:<3} {_short(c.card):36} "
                 f"{v.bat + (v.bowl or 0):+5.1f}  ~{au.crore(c.price):>8}  "
                 f"WALK AWAY above {au.crore(t.walk_away)}{flag}")
        L.append(f"           {c.set_label} · {_tag(c.card, v)}")
        for i, b in enumerate(t.backups, 1):
            bv = vals[b.cand.key]
            where = "accelerated round" if b.cand.status == "unsold" else f"lot {b.cand.lot}"
            L.append(f"           backup {i}: {_short(b.cand.card):36} {where:18} "
                     f"~{au.crore(b.cand.price):>8}  twelve {b.value_after - p.value:+.1f}")
        if not t.backups:
            L.append("           backup: none left that fits after this lot")
    L.append("")
    base = p.value
    L.append(f"Twelve is worth {base:+.1f} runs a match over an average side. Planned spend "
             f"{au.crore(p.spend)}, {au.crore(max(0, p.spare))} spare above the "
             f"{au.crore(RESERVE * (p.open_places - len(p.targets)))} reserve for your other places.")
    L.append("Walk-away price: above it, the next backup is the better use of the money. "
             "Prices are estimates from this room's sales; they move as purses drain.")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("code", help="the room code")
    ap.add_argument("--me", required=True, help="your franchise short code or your name in the room")
    ap.add_argument("--keep", default="", help='retained players: "V Kohli 2016, JJ Bumrah 2020"')
    ap.add_argument("--base", default=SITE_ORIGIN, help="site to read (default: production)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    doc = snapshot_deck.read_document()
    if doc is None:
        print("no deck snapshot", file=sys.stderr)
        return 2
    deck = snapshot_deck.deck_from(doc)
    try:
        keep = parse_keep(args.keep, index_deck(deck))
        code = args.code.upper()
        room = fetch(args.base, f"/api/rooms/{code}")
        sets = fetch(args.base, f"/api/rooms/{code}/auction/sets")
        plan = plan_room(room, sets, deck, args.me, keep, args.seed)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    cards = [t.cand.card for t in plan.targets] + plan.owned + plan.retained
    for t in plan.targets:
        cards += [b.cand.card for b in t.backups]
    print(render(plan, values_of(cards)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
