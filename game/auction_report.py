"""The auction told as a story [A232]: the morning-after paper, every squad ranked, the
board's brief, the season's verdict on the buys, and the floor's whispers between lots.

Pure. Everything here is worked out from a finished (or running) `game.auction.Auction`
and, for the verdict, the season's results -- nothing is stored, nothing draws from a
random stream, and no bid or sale can change because of it. Wording is chosen by hashing
the auction's seed with the fact (`game.press._h`), so a reload reads the same.

Rules the copy keeps, each a guardrail:

- **A price is judged against THIS auction, never against a fixed curve.** The computer
  teams' value curve is not a market price: measured over 40 auctions the median sale went
  for 1.29x it in an open auction and 0.71x in a mega one, so a curve-based "steal" would
  call a fifth of every mega auction a steal. `fair_price` is the median paid in the same
  auction for players rated within `MARKET_BAND` of this one.
- **Nobody is quoted.** A franchise is a real organisation and a player a real person; the
  paper reports what happened at the table and puts words in no one's mouth.
- **No pronouns for a player.** A name, or "the opener", never a guessed he or she.
- **A squad is RANKED, never graded.** The ten best twelves of one auction sit 1.7-5.4
  rating points apart (median 3.4), so letters would give nearly every team the same one.
- **The board's brief is story only** and moves nothing in the game. Its squad and money
  targets can always be met by deliberate play; its season target cannot be promised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from etl.feasibility import Card, Deck, team_rating
from game import auction as au
from game.press import PAPER, Clipping, _h

# --- the market ------------------------------------------------------------------------------

MARKET_BAND = 2             # rating points either side counted as "the same kind of player"
MARKET_MIN = 5              # comparable sales needed before a price is judged at all
STEAL_RATIO = 0.6           # paid at most this share of the going rate: a steal...
STEAL_MIN_RATING = 85       # ...of a player worth calling one
SPLURGE_RATIO = 1.75        # paid at least this multiple of the going rate: a splurge
WAR_BIDS = 20               # a lot this contested makes the paper on its own


def _auction_sales(a: au.Auction) -> list[au.Sale]:
    """Sales under the hammer: the fill round hands players out at a fixed price, so it
    says nothing about what the room thought anyone was worth."""
    return [s for s in a.sales if s.winner is not None and s.round != "fill"]


def fair_price(a: au.Auction, sale: au.Sale) -> int | None:
    """The median paid in this auction for players rated within MARKET_BAND of this one,
    this sale excluded; None where too few comparable sales exist to say."""
    d = sale.lot.card.display or 0
    comp = [s.price for s in _auction_sales(a)
            if s is not sale and abs((s.lot.card.display or 0) - d) <= MARKET_BAND]
    return round(median(comp)) if len(comp) >= MARKET_MIN else None


def _ratios(a: au.Auction) -> list[tuple[au.Sale, int, float]]:
    out = []
    for s in _auction_sales(a):
        fair = fair_price(a, s)
        if fair:
            out.append((s, fair, s.price / fair))
    return out


def steal_of(a: au.Auction):
    """(sale, fair) for the auction's best bargain among players rated STEAL_MIN_RATING or
    more, or None when nothing went cheaply enough to call it one."""
    cands = [(r, s, f) for s, f, r in _ratios(a)
             if (s.lot.card.display or 0) >= STEAL_MIN_RATING and r <= STEAL_RATIO]
    if not cands:
        return None
    _, s, f = min(cands, key=lambda x: (x[0], x[1].lot.index))
    return s, f


def splurge_of(a: au.Auction):
    cands = [(r, s, f) for s, f, r in _ratios(a) if r >= SPLURGE_RATIO]
    if not cands:
        return None
    _, s, f = max(cands, key=lambda x: (x[0], -x[1].lot.index))
    return s, f


# --- spending ------------------------------------------------------------------------------


def opening_purse(t: au.Team) -> int:
    """What the team brought to the auction: the purse less its retentions."""
    return au.PURSE - sum(t.paid[:t.retained])


def bought(t: au.Team) -> list[tuple[Card, int]]:
    """Players the team paid for at the auction, fill round included -- not its retentions
    and not a career legend (A215), who came free."""
    return [(c, p) for i, (c, p) in enumerate(zip(t.squad, t.paid))
            if i >= t.retained and c is not t.legend]


def spent(t: au.Team) -> int:
    return sum(p for _, p in bought(t))


def _ord(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _cr(lakh: int) -> str:
    """A price as the paper prints it: ₹24.5 cr, ₹75 lakh."""
    if lakh >= 100:
        s = f"{lakh / 100:.2f}".rstrip("0").rstrip(".")
        return f"₹{s} cr"
    return f"₹{lakh} lakh"


def _pick(seed: int, key: str, options: list[str]) -> str:
    return options[_h(seed, "auction", key) % len(options)]


# --- the morning-after paper ---------------------------------------------------------------


def morning_after(a: au.Auction, you: int | None = None) -> list[Clipping]:
    """The paper the morning after: the record, the longest war, the steal and the splurge,
    your headline signing, the biggest name nobody bought, a Right to Match, and who spent.
    Each appears only when the auction actually produced one; ordered by `weight`."""
    teams = a.teams
    sales = _auction_sales(a)
    out: list[Clipping] = []
    seed = a.seed

    def clip(key, section, headline, dek, body, weight, franchise=None, tone="", year=0):
        # `year` is the season of the player in the story, for his crest; 0 for a team's.
        out.append(Clipping(key=key, year=year, section=section, headline=headline, dek=dek,
                            dateline="", body=body, weight=weight, franchise=franchise,
                            tone=tone))

    record = max(sales, key=lambda s: (s.price, -s.lot.index), default=None)
    if record is not None:
        c, t = record.lot.card, teams[record.winner]
        rivals = [teams[b.team].short for b in record.bids if b.team != record.winner]
        last_rival = rivals[-1] if rivals else None
        head = _pick(seed, "record", [
            f"{_cr(record.price)}: {c.name} goes to {t.short}",
            f"{t.short} pay {_cr(record.price)} for {c.name}",
            f"Record day: {c.name} sold for {_cr(record.price)}"])
        body = [f"{c.name}, rated {c.display} on the strength of {c.season_year}, was the "
                f"most expensive player of the auction, bought by {t.franchise} for "
                f"{_cr(record.price)} from a base of {_cr(record.lot.base)}."]
        if last_rival:
            body.append(f"{last_rival} were the last to drop out, after "
                        f"{len(record.bids)} bids.")
        if record.rtm is not None:
            body.append("A Right to Match card was played on the lot before it closed.")
        clip("record", "FRONT PAGE", head, "The top of the market", body, 100,
             c.franchise, "gold", c.season_year)

    # The longest fight that is not already the front page's story.
    war = max((s for s in sales if s is not record),
              key=lambda s: (len(s.bids), -s.lot.index), default=None)
    if war is not None and len(war.bids) >= WAR_BIDS:
        c, t = war.lot.card, teams[war.winner]
        fighters = []
        for b in war.bids:
            if teams[b.team].short not in fighters:
                fighters.append(teams[b.team].short)
        head = _pick(seed, "war", [
            f"{len(war.bids)} bids for {c.name}",
            f"The longest fight of the day: {c.name}",
            f"{' and '.join(fighters[-2:])} slug it out over {c.name}"])
        clip("war", "AUCTION", head, f"{len(fighters)} franchises in the running",
             [f"{c.name} took {len(war.bids)} bids to settle, climbing from "
              f"{_cr(war.lot.base)} to {_cr(war.price)} before {t.short} were left standing."],
             70, c.franchise, "", c.season_year)

    st = steal_of(a)
    if st is not None:
        s, fair = st
        c, t = s.lot.card, teams[s.winner]
        head = _pick(seed, "steal", [
            f"Bargain of the auction: {c.name} for {_cr(s.price)}",
            f"{t.short} land {c.name} on the cheap",
            f"How did {c.name} go for {_cr(s.price)}?"])
        clip("steal", "AUCTION", head, "The buy of the day",
             [f"Players rated around {c.display} went for a median {_cr(fair)} at this "
              f"auction. {t.franchise} paid {_cr(s.price)} for {c.name}."],
             80, c.franchise, "gold", c.season_year)

    sp = splurge_of(a)
    if sp is not None:
        s, fair = sp
        c, t = s.lot.card, teams[s.winner]
        head = _pick(seed, "splurge", [
            f"Did {t.short} pay over the odds for {c.name}?",
            f"{_cr(s.price)} for {c.name} raises eyebrows",
            f"{t.short} break the bank for {c.name}"])
        clip("splurge", "AUCTION", head, "The going rate was a lot less",
             [f"{t.franchise} paid {_cr(s.price)} for {c.name}, rated {c.display}. Players "
              f"of that rating went for a median {_cr(fair)} on the day."],
             60, c.franchise, "red", c.season_year)

    if you is not None:
        yt = teams[you]
        hammer = [(s.lot.card, s.price) for s in sales if s.winner == you]
        if hammer:
            c, p = max(hammer, key=lambda x: (x[1], x[0].display or 0))
            head = _pick(seed, f"yours:{you}", [
                f"{yt.short}'s big signing: {c.name}",
                f"{c.name} the headline buy for {yt.short}",
                f"{yt.short} build around {c.name}"])
            body = [f"{yt.franchise} came to the table with {_cr(opening_purse(yt))} and "
                    f"left with {_cr(yt.purse)}. Their biggest outlay was {_cr(p)} on "
                    f"{c.name}, rated {c.display}."]
        else:
            head = f"{yt.short} sit on their purse"
            body = [f"{yt.franchise} won nothing under the hammer and filled the squad "
                    f"at the minimum price, leaving with {_cr(yt.purse)}."]
        clip("yours", "YOUR FRANCHISE", head,
             f"{len(bought(yt))} players bought, {_cr(spent(yt))} spent", body, 90,
             yt.franchise)

    sold_ids = {s.lot.index for s in a.sales if s.winner is not None and s.round != "fill"}
    unsold = [lot for lot in a.lots if lot.index not in sold_ids]
    if unsold:
        lot = max(unsold, key=lambda l: (l.card.display or 0, -l.index))
        fill = next((s for s in a.sales if s.round == "fill" and s.winner is not None
                     and s.lot.card.person_id == lot.card.person_id), None)
        c = lot.card
        offers = sum(1 for s in a.sales if s.lot.index == lot.index and s.round != "fill")
        body = [f"{c.name}, rated {c.display}, went unsold "
                f"{'twice' if offers > 1 else 'under the hammer'} at a base of {_cr(lot.base)}."]
        if fill is not None:
            body.append(f"{teams[fill.winner].short} picked the lot up afterwards for "
                        f"{_cr(fill.price)} in the fill round.")
        clip("unsold", "AUCTION", _pick(seed, "unsold", [
            f"No takers for {c.name}", f"{c.name} goes unsold",
            f"The room passes on {c.name}"]), "The one that got away", body, 50, c.franchise, "", c.season_year)

    rtms = [s for s in sales if s.rtm is not None]
    if rtms:
        s = max(rtms, key=lambda s: (s.rtm.matched, s.price, -s.lot.index))
        c, holder = s.lot.card, teams[s.rtm.holder]
        if s.rtm.matched:
            body = [f"{holder.franchise} played a Right to Match card on {c.name} and "
                    f"matched {_cr(s.rtm.raised_to)} to keep a former player."]
            head = f"{holder.short} use the card to keep {c.name}"
        else:
            winner = teams[s.winner]
            body = [f"{holder.franchise} played a Right to Match card on {c.name}, but "
                    f"would not match {winner.short}'s {_cr(s.rtm.raised_to)}."]
            head = f"{holder.short} let {c.name} go"
        clip("rtm", "AUCTION", head, "Right to Match", body, 40, c.franchise, "",
             c.season_year)

    spend = sorted(teams, key=lambda t: (-spent(t), t.index))
    big, thrift = spend[0], min(teams, key=lambda t: (spent(t), t.index))
    if spent(big) > 0:
        clip("spend", "BACK PAGE", _pick(seed, "spend", [
            f"{big.short} spend {_cr(spent(big))}", f"Big spenders: {big.short}",
            f"{big.short} empty the purse"]), "Who spent what",
             [f"{big.franchise} spent the most at the table, {_cr(spent(big))} on "
              f"{len(bought(big))} players. {thrift.franchise} spent the least, "
              f"{_cr(spent(thrift))}, and leave with {_cr(thrift.purse)}."], 30,
             big.franchise)
    out.sort(key=lambda c: -c.weight)
    return out


# --- every squad, ranked ---------------------------------------------------------------------


@dataclass
class TeamReport:
    index: int
    short: str
    franchise: str
    human: bool
    rank: int
    rating: float | None            # the best twelve's face value per player
    batting: int | None
    bowling: int | None
    batting_rank: int
    bowling_rank: int
    top_buy: tuple[Card, int] | None
    spent: int
    purse: int
    strength: str
    gap: str
    notes: list[str] = field(default_factory=list)  # depth worth knowing: one keeper ...


def _rank(values: dict[int, float | None]) -> dict[int, int]:
    order = sorted(values, key=lambda i: (-(values[i] if values[i] is not None else -1e9), i))
    return {i: n for n, i in enumerate(order, 1)}


def squad_report(a: au.Auction, twelves: dict[int, list[Card] | None]) -> list[TeamReport]:
    """Every team ranked by its best twelve (`twelves`: team index -> the twelve cards, the
    caller's own `best_twelve`, so a room can hand over the twelves it already worked out).
    Each row says where the side is strongest and where it is thin, from facts about it --
    a rank among the ten, or a count in the squad -- never from a judgement."""
    ratings, bats, bowls = {}, {}, {}
    for t in a.teams:
        tw = twelves.get(t.index)
        ratings[t.index] = (sum((c.display or 0) for c in tw) / len(tw)) if tw else None
        tr = team_rating(tw) if tw else None
        bats[t.index] = tr.batting if tr else None
        bowls[t.index] = tr.bowling if tr else None
    rank, brank, wrank = _rank(ratings), _rank(bats), _rank(bowls)
    n = len(a.teams)
    out = []
    for t in a.teams:
        i = t.index
        buys = bought(t)
        top = max(buys, key=lambda x: (x[1], x[0].display or 0)) if buys else None
        if brank[i] <= wrank[i]:
            strength = ("The best batting in the auction" if brank[i] == 1
                        else f"{_ord(brank[i])}-best batting")
            weak = f"{_ord(wrank[i])} of {n} for bowling"
        else:
            strength = ("The best attack in the auction" if wrank[i] == 1
                        else f"{_ord(wrank[i])}-best attack")
            weak = f"{_ord(brank[i])} of {n} for batting"
        keepers = sum(c.keeper_eligible for c in t.squad)
        bowlers = sum(c.has_bowl for c in t.squad)
        gap = weak if twelves.get(i) else "No legal twelve"
        notes = (["one wicketkeeper"] if keepers <= 1 else []) + (
            ["no bowler to spare"] if bowlers <= au.BOWLERS_IN_TWELVE else [])
        out.append(TeamReport(
            index=i, short=t.short, franchise=t.franchise, human=t.human, rank=rank[i],
            rating=round(ratings[i], 1) if ratings[i] is not None else None,
            batting=bats[i], bowling=bowls[i], batting_rank=brank[i], bowling_rank=wrank[i],
            top_buy=top, spent=spent(t), purse=t.purse, strength=strength, gap=gap, notes=notes))
    out.sort(key=lambda r: r.rank)
    return out


# --- the board's brief ----------------------------------------------------------------------

FORMER_WANTED = 2           # buy back this many of the franchise's former players
KEEPERS_WANTED = 2
SPINNERS_WANTED = 3
STAR_RATING = 95
RESERVE = 500               # finish with at least ₹5 cr
TOP_PRICE = 1500            # pay no more than ₹15 cr for any one player
SPEND_AT_LEAST = 9000       # spend at least ₹90 cr (open auctions only)
# Availability: a target is only set where the catalogue holds enough players to meet it
# with room to be outbid on some. Declared, like the targets themselves.
AVAIL = {"former": 6, "keepers": 12, "spin": 20, "star": 3}


@dataclass(frozen=True)
class Target:
    key: str
    kind: str                   # "squad" | "money" | "season"
    text: str


@dataclass
class TargetStatus:
    key: str
    kind: str
    text: str
    state: str                  # "open" | "met" | "missed"
    progress: str = ""


_CATALOGUES: dict[int, list[au.Lot]] = {}


def _catalogue(deck: Deck, seed: int) -> list[au.Lot]:
    """The catalogue with nobody retained: what the brief reads to check a target can be
    met. Independent of anyone's retentions, so the brief never changes once shown."""
    if seed not in _CATALOGUES:
        if len(_CATALOGUES) > 64:
            _CATALOGUES.clear()
        _CATALOGUES[seed] = au.build_catalogue(deck, seed)
    return _CATALOGUES[seed]


def available(lots: list[au.Lot], franchise: str) -> dict[str, int]:
    cards = [l.card for l in lots]
    return {"former": sum(au.franchise_of(c) == franchise for c in cards),
            "keepers": sum(c.keeper_eligible for c in cards),
            "spin": sum(c.bowling_style == "spin" for c in cards),
            "star": sum((c.display or 0) >= STAR_RATING for c in cards)}


def board_brief(deck: Deck, seed: int, short: str, mega: bool) -> list[Target]:
    """Three targets from the franchise's board, a pure function of the auction's seed and
    the franchise, so the brief is the same on every replay and for every viewer."""
    franchise = dict(au.FRANCHISES)[short]
    avail = available(_catalogue(deck, seed), franchise)
    squad = [Target("former", "squad",
                    f"Win back {FORMER_WANTED} of {short}'s former players under the hammer"),
             Target("keepers", "squad", f"Leave with {KEEPERS_WANTED} wicketkeepers in the squad"),
             Target("spin", "squad", f"Sign {SPINNERS_WANTED} spinners")]
    if not mega:        # a mega auction's legends are mostly retained before it opens
        squad.append(Target("star", "squad", f"Win a player rated {STAR_RATING} or more under the hammer"))
    squad = [t for t in squad if avail[t.key] >= AVAIL[t.key]]
    money = [Target("reserve", "money", f"Finish with at least {_cr(RESERVE)} in the purse"),
             Target("top_price", "money", f"Pay no more than {_cr(TOP_PRICE)} for any one player")]
    if not mega:        # retentions take a mega auction's money before the table opens
        money.append(Target("spend", "money", f"Spend at least {_cr(SPEND_AT_LEAST)} at the table"))
    season = [Target("playoffs", "season", "Reach the playoffs")] * 3 + [
        Target("top2", "season", "Finish in the top two of the league")]
    h = _h(seed, "brief", short)
    return [squad[h % len(squad)], money[(h // 7) % len(money)], season[(h // 49) % len(season)]]


def hammer_buys(a: au.Auction, team: au.Team) -> list[Card]:
    """Players the team won under the hammer -- not the fill round, which hands players
    out at the minimum price and so would meet a squad target for a team that bid on
    nobody."""
    return [s.lot.card for s in a.sales
            if s.winner == team.index and s.round != "fill"
            and any(c.person_id == s.lot.card.person_id for c in team.squad)]


def judge(brief: list[Target], a: au.Auction, team: au.Team,
          finish: int | None = None) -> list[TargetStatus]:
    """Where each target stands. A target is `missed` the moment it can no longer be met,
    `met` once it is certain, and `open` in between. `finish` is the league position once
    the season is played (None before)."""
    out = []
    buys = bought(team)
    hammer = hammer_buys(a, team)
    auction_over = all(t.open_places == 0 for t in a.teams)
    for t in brief:
        state, progress = "open", ""
        if t.key in ("former", "keepers", "spin", "star"):
            have = {"former": sum(au.franchise_of(c) == team.franchise for c in hammer),
                    "keepers": sum(c.keeper_eligible for c in team.squad),
                    "spin": sum(c.bowling_style == "spin" for c in team.squad),
                    "star": sum((c.display or 0) >= STAR_RATING for c in hammer)}[t.key]
            need = {"former": FORMER_WANTED, "keepers": KEEPERS_WANTED,
                    "spin": SPINNERS_WANTED, "star": 1}[t.key]
            progress = f"{min(have, need)} of {need}"
            if have >= need:
                state = "met"
            elif auction_over or team.open_places == 0:
                state = "missed"
        elif t.key == "reserve":
            progress = f"{_cr(team.purse)} left"
            if team.purse < RESERVE:
                state = "missed"
            elif auction_over:
                state = "met"
        elif t.key == "top_price":
            top = max((p for _, p in buys), default=0)
            progress = f"top buy {_cr(top)}" if top else "nothing bought yet"
            if top > TOP_PRICE:
                state = "missed"
            elif auction_over:
                state = "met"
        elif t.key == "spend":
            progress = f"{_cr(spent(team))} spent"
            if spent(team) >= SPEND_AT_LEAST:
                state = "met"
            elif auction_over:
                state = "missed"
        else:
            cut = 4 if t.key == "playoffs" else 2
            if finish is not None:
                progress = f"finished {_ord(finish)}"
                state = "met" if finish <= cut else "missed"
            else:
                progress = "decided by the season"
        out.append(TargetStatus(t.key, t.kind, t.text, state, progress))
    return out


# --- the floor's whispers ------------------------------------------------------------------

WHISPER_KEEPERLESS_AT = 0.4     # share of the lots gone before a keeperless side is news
UNSOLD_RUN = 3
UNSOLD_RUNS_TOLD = 3        # a quiet room is news the first few times, not every time
FIRSTS_TOLD = 3             # "the first to..." is news for the first few teams, not all ten
WAR_WHISPER_BIDS = 15
WAR_WHISPER_AFTER = 10      # the first lots' fights are not news yet: there is nothing to beat


def whispers(a: au.Auction) -> dict[int, list[str]]:
    """Lines the floor would mutter between lots, keyed by the position in `a.sales` of the
    sale after which each first became true. Every line fires once an auction (per team
    where it names one), so a long auction is not a wall of repeats."""
    teams = a.teams
    out: dict[int, list[str]] = {}
    squads = {t.index: list(t.squad[:t.retained]) + ([t.legend] if t.legend else [])
              for t in teams}
    spend = {t.index: 0 for t in teams}
    opening = {t.index: opening_purse(t) for t in teams}
    fired: set = set()
    run = 0
    runs_told = 0
    half: list[str] = []        # the first teams to spend half their purse, in order
    full: list[str] = []        # the first teams to fill their overseas places
    keeperless: list[str] = []  # the first teams called out for having no keeper
    war_best = WAR_WHISPER_BIDS - 1
    buys = {t.index: 0 for t in teams}
    total = len(a.lots)

    def say(pos, key, text):
        if key not in fired:
            fired.add(key)
            out.setdefault(pos, []).append(text)

    for pos, s in enumerate(a.sales):
        if s.round == "fill":
            continue
        if s.round == "accelerated":
            say(pos, "accelerated", "The accelerated round is under way: the unsold get a "
                                    "second chance, quickly.")
        if s.winner is None:
            run += 1
            if run == UNSOLD_RUN and runs_told < UNSOLD_RUNS_TOLD:
                runs_told += 1
                say(pos, ("run", pos), f"{run} lots in a row without a bid. "
                                       f"The room has gone quiet.")
            continue
        run = 0
        t = s.winner
        squads[t].append(s.lot.card)
        spend[t] += s.price
        buys[t] += 1
        team = teams[t]
        if len(s.bids) > war_best:
            war_best = len(s.bids)
            if pos >= WAR_WHISPER_AFTER:
                fired.discard("war")
                say(pos, "war", f"{len(s.bids)} bids on that one: the longest fight so far.")
        if (opening[t] and spend[t] * 2 >= opening[t] and ("half", t) not in fired
                and len(half) < FIRSTS_TOLD):
            half.append(team.short)
            nth = ["first", "second", "third"][len(half) - 1]
            say(pos, ("half", t), f"{team.short} are the {nth} to spend half their purse: "
                                  f"{_cr(spend[t])} in {buys[t]} buys.")
        if (sum(c.overseas is True for c in squads[t]) >= au.SQUAD_OVERSEAS_CAP
                and ("overseas", t) not in fired and len(full) < FIRSTS_TOLD - 1):
            full.append(team.short)
            say(pos, ("overseas", t),
                f"{team.short} are the first to fill all {au.SQUAD_OVERSEAS_CAP} overseas "
                f"places." if len(full) == 1 else
                f"{team.short} join {full[0]} with all {au.SQUAD_OVERSEAS_CAP} overseas places "
                f"filled.")
        if (s.lot.index + 1) >= total * WHISPER_KEEPERLESS_AT and s.round == "main":
            for other in teams:
                if (not any(c.keeper_eligible for c in squads[other.index])
                        and ("keeper", other.index) not in fired
                        and len(keeperless) < FIRSTS_TOLD - 1):
                    keeperless.append(other.short)
                    say(pos, ("keeper", other.index),
                        f"Over {round(WHISPER_KEEPERLESS_AT * 100)}% of the lots gone and "
                        f"{other.short} still have no wicketkeeper.")
    return out


# --- the season's verdict on the buys --------------------------------------------------------

VERDICT_TOP = 30            # the league's biggest contributors, from which the bargain comes


@dataclass
class BuyLine:
    person_id: str
    name: str
    card: Card
    price: int
    retained: bool
    legend: bool
    played: bool
    runs: int = 0
    wickets: int = 0
    pom: int = 0

    @property
    def contribution(self) -> int:
        """Runs plus twenty a wicket: A183's prominence, one scale for bat and ball."""
        return self.runs + 20 * self.wickets


@dataclass
class Verdict:
    bargain: tuple[BuyLine, str] | None        # the league's, with the buyer's short
    best_buy: BuyLine | None
    flop: BuyLine | None
    yours: list[BuyLine] = field(default_factory=list)
    finish: int | None = None


def _lines(a: au.Auction, results, team_of_side) -> dict[int, dict[str, BuyLine]]:
    """Every team's squad with what each player did in the season. `team_of_side` maps
    id(Side) -> team index. Super overs count for nothing here, as everywhere (A135)."""
    from game.career import player_of_match
    lines: dict[int, dict[str, BuyLine]] = {}
    for t in a.teams:
        lines[t.index] = {
            c.person_id: BuyLine(c.person_id, c.name, c, p, i < t.retained, c is t.legend,
                                 False)
            for i, (c, p) in enumerate(zip(t.squad, t.paid))}
    owner = {pid: ti for ti, d in lines.items() for pid in d}
    for r in results:
        if r.home_innings is None or r.away_innings is None:
            continue
        for side, bat, bowled in ((r.home, r.home_innings, r.away_innings),
                                  (r.away, r.away_innings, r.home_innings)):
            ti = team_of_side.get(id(side))
            if ti is None:
                continue
            d = lines[ti]
            for b in bat.batting:
                ln = d.get(b.player.person_id)
                if ln is not None and b.faced_any:
                    ln.played, ln.runs = True, ln.runs + b.runs
            for w in bowled.bowling:
                ln = d.get(w.player.person_id)
                if ln is not None and w.balls > 0:
                    ln.played, ln.wickets = True, ln.wickets + w.wickets
        pom = player_of_match(r)
        if pom is not None and pom in owner:
            lines[owner[pom]][pom].pom += 1
    return lines


def season_verdict(a: au.Auction, results, team_of_side: dict[int, int], you: int,
                   finish: int | None) -> Verdict:
    """The season's verdict on the auction: the league's bargain (of the VERDICT_TOP
    biggest contributors, the cheapest buy), your best buy (of your better half, the most
    done per rupee), and your most expensive flop (of your three dearest buys who played,
    one whose contribution sits in your side's bottom half), with a Player of the Match
    tally for each of your squad."""
    lines = _lines(a, results, team_of_side)
    bought_lines = [(ln, a.teams[ti].short) for ti, d in lines.items() for ln in d.values()
                    if ln.played and not ln.legend and not ln.retained and ln.price > 0]
    top = sorted(bought_lines, key=lambda x: (-x[0].contribution, x[0].person_id))[:VERDICT_TOP]
    bargain = min(top, key=lambda x: (x[0].price, -x[0].contribution, x[0].person_id),
                  default=None)
    mine = list(lines[you].values())
    played = sorted([ln for ln in mine if ln.played],
                    key=lambda ln: (-ln.contribution, ln.person_id))
    half = played[:max(1, (len(played) + 1) // 2)]
    paid = [ln for ln in half if not ln.legend and ln.price > 0]
    best = max(paid, key=lambda ln: (ln.contribution / ln.price, ln.contribution),
               default=None)
    pos = {ln.person_id: i for i, ln in enumerate(played)}
    dear = sorted([ln for ln in played if not ln.legend and ln.price > 0],
                  key=lambda ln: (-ln.price, ln.person_id))[:3]
    flops = [ln for ln in dear if pos[ln.person_id] >= (len(played) + 1) // 2]
    flop = max(flops, key=lambda ln: (pos[ln.person_id], ln.price)) if flops else None
    if flop is best:
        flop = None
    yours = sorted(mine, key=lambda ln: (not ln.played, -ln.price, ln.person_id))
    return Verdict(bargain, best, flop, yours, finish)


__all__ = ["PAPER", "morning_after", "squad_report", "board_brief", "judge", "whispers",
           "season_verdict", "fair_price", "steal_of", "splurge_of"]
