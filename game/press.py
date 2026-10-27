"""The papers [A228]: Build a Career told as newspaper clippings.

Pure and text-only. The career loop records each season's newsworthy FACTS (`facts_*`,
small dicts of numbers and names, kept in the checkpoint), and this module turns a fact into
a clipping (`render`) every time a page asks. Storing the facts rather than the words means
a better template improves every career already played, and the checkpoint stays small.

Rules the templates keep, each a guardrail rather than a style choice:

- **The paper's name is made up** (`PAPER`). A real paper's masthead over invented copy
  would be impersonating it, the reason A144 declined a real auctioneer's voice.
- **Only your own player is ever quoted.** A real player may be NAMED for what the scorecard
  shows (A200), never given words.
- **No pronouns for your player.** The player is the person at the keyboard, so a clipping
  says the name, or "the opener", and never guesses he or she.
- **Nothing here draws from a random stream.** A template is chosen by hashing the career's
  seed with the fact, so no match result can move (A181's rule) and a reload reads the same.
- No AI call (the codebase's hard rule): every sentence is a written template.

Where a clipping says WHERE, it says what the archive can stand behind: a final's real venue
(`FINAL_VENUE`, pinned against the archive by a test), and otherwise the home city of the
side that batted first, which is what the engine calls the home side. In the seasons played
away from home grounds (`NEUTRAL_YEARS`) a league clipping carries no city at all.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

PAPER = "The Fine Leg Gazette"

# The home city of each franchise (canonical name), as the archive's most common city for
# its matches. Two declared exceptions: the archive writes Punjab's ground as "Chandigarh",
# where the stadium is in Mohali, and Bengaluru was Bangalore until the city's rename.
HOME_CITY = {
    "Chennai Super Kings": "Chennai", "Deccan Chargers": "Hyderabad",
    "Delhi Capitals": "Delhi", "Punjab Kings": "Mohali",
    "Kolkata Knight Riders": "Kolkata", "Mumbai Indians": "Mumbai",
    "Rajasthan Royals": "Jaipur", "Royal Challengers Bengaluru": "Bengaluru",
    "Kochi Tuskers Kerala": "Kochi", "Pune Warriors": "Pune",
    "Sunrisers Hyderabad": "Hyderabad", "Gujarat Lions": "Rajkot",
    "Rising Pune Supergiant": "Pune", "Gujarat Titans": "Ahmedabad",
    "Lucknow Super Giants": "Lucknow",
}
BANGALORE_UNTIL = 2014

# Every real final's ground, from the archive (the last match of each season by date).
# 2020's city is blank in the archive; the ground is in Dubai.
FINAL_VENUE = {
    2008: ("Dr DY Patil Sports Academy", "Mumbai"),
    2009: ("New Wanderers Stadium", "Johannesburg"),
    2010: ("Dr DY Patil Sports Academy", "Mumbai"),
    2011: ("MA Chidambaram Stadium", "Chennai"),
    2012: ("MA Chidambaram Stadium", "Chennai"),
    2013: ("Eden Gardens", "Kolkata"),
    2014: ("M Chinnaswamy Stadium", "Bangalore"),
    2015: ("Eden Gardens", "Kolkata"),
    2016: ("M Chinnaswamy Stadium", "Bangalore"),
    2017: ("Rajiv Gandhi International Stadium", "Hyderabad"),
    2018: ("Wankhede Stadium", "Mumbai"),
    2019: ("Rajiv Gandhi International Stadium", "Hyderabad"),
    2020: ("Dubai International Cricket Stadium", "Dubai"),
    2021: ("Dubai International Cricket Stadium", "Dubai"),
    2022: ("Narendra Modi Stadium", "Ahmedabad"),
    2023: ("Narendra Modi Stadium", "Ahmedabad"),
    2024: ("MA Chidambaram Stadium", "Chennai"),
    2025: ("Narendra Modi Stadium", "Ahmedabad"),
    2026: ("Narendra Modi Stadium", "Ahmedabad"),
}
# Seasons played away from home grounds, wholly or in part: 2009 in South Africa, 2020 in
# the UAE, 2021 moved there halfway. A league clipping from one carries no city.
NEUTRAL_YEARS = {2009: "South Africa", 2020: "the UAE", 2021: "India and the UAE"}

# What your player does to celebrate, chosen at creation. Shown only in the papers.
CELEBRATIONS = {
    "bat_raise": ("Bat raised to all four stands", "the bat raised to all four stands"),
    "salute": ("A salute to the dressing room", "the familiar salute to the dressing room"),
    "aeroplane": ("The aeroplane", "the arms-out aeroplane run"),
    "knee_slide": ("A knee slide", "a knee slide across the turf"),
    "sky": ("A point to the sky", "the point to the sky"),
    "bow": ("A bow to the crowd", "a deep bow to the crowd"),
}

EDITION_MAX = 5                 # clippings a season
# Declared marks for a "legendary" innings or spell, game design like A218's colours.
BLITZ_RUNS = 70                 # 70 or more at a strike rate of 200 or better
BIG_FINAL_RUNS = 50
BIG_FINAL_WICKETS = 3


@dataclass
class Clipping:
    key: str
    year: int
    section: str                # "FRONT PAGE" | "SPORT" | "BACK PAGE" | "AUCTION" | ...
    headline: str
    dek: str
    dateline: str               # "KOLKATA" or "" (the page writes "From our correspondent")
    body: list[str]
    quote: str = ""             # your player's own words, with the attribution
    weight: int = 0             # how newsworthy: the season's edition is ordered by it
    match: int | None = None    # index into that season's feed, for a match's clipping
    franchise: str | None = None   # whose crest is the picture
    tone: str = ""              # "gold" | "red" | "orange" | "purple" | ""


@dataclass
class Voice:
    """Who the papers are writing about."""
    name: str
    role: str                   # the career's role key
    seed: int
    number: int | None = None
    home: str = ""
    celebration: str = ""

    @property
    def sur(self) -> str:
        return self.name.split()[-1] if self.name.split() else self.name


ROLE_NOUN = {"opener": "opener", "middle": "batter", "finisher": "finisher",
             "keeper": "wicketkeeper", "pace": "fast bowler", "spin": "spinner",
             "allrounder": "all-rounder"}


def _h(*parts) -> int:
    return int(hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _pick(v: Voice, fact: dict, options: list, slot: str = ""):
    """One of `options`, the same one every time for this career and this fact."""
    return options[_h(v.seed, fact.get("y"), fact["k"], fact.get("m"), slot) % len(options)]


def city(franchise: str | None, year: int) -> str:
    from etl.franchise_map import canonical

    if not franchise:
        return ""
    c = HOME_CITY.get(canonical(franchise), "")
    return "Bangalore" if c == "Bengaluru" and year <= BANGALORE_UNTIL else c


def _club(name: str | None) -> str:
    """'Kolkata Knight Riders 2016' -> 'Kolkata Knight Riders'."""
    return re.sub(r"\s\d{4}$", "", name or "")


def _overs(balls: int) -> str:
    return f"{balls // 6}.{balls % 6}" if balls % 6 else str(balls // 6)


def _num(n: int) -> str:
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
             "ten", "eleven", "twelve"]
    return words[n] if 0 <= n < len(words) else f"{n:,}"


def _crore(lakh: int) -> str:
    from game.auction import crore

    return crore(lakh)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _ord(n: int) -> str:
    t, u = n % 100, n % 10
    return f"{n}{'th' if 11 <= t <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(u, 'th')}"


def _who(v: Voice, fact: dict) -> str:
    """The first mention: the name, sometimes dressed with the shirt number or home town."""
    noun = ROLE_NOUN.get(v.role, "player")
    dress = [v.name]
    if v.home:
        dress.append(f"{v.home}'s own {v.name}")
        dress.append(f"the {v.home} {noun} {v.name}")
    if v.number is not None:
        dress.append(f"No. {v.number} {v.name}")
    return _pick(v, fact, dress, "who")


def _celebrate(v: Voice, fact: dict) -> str:
    if v.celebration not in CELEBRATIONS:
        return ""
    how = CELEBRATIONS[v.celebration][1]
    return _pick(v, fact, [f"Out came {how}.", f"It ended, as these days tend to, with {how}.",
                           f"{v.sur} marked it with {how}."], "cel")


def _quote(v: Voice, fact: dict, lines: list[str]) -> str:
    line = _pick(v, fact, lines, "quote")
    if line.endswith("."):
        line = line[:-1] + ","
    form = _pick(v, fact, ["{q} said {s}.", "{q} {s} said.", "{q} {s} told reporters afterwards."],
                 "said")
    return form.format(q=f"“{line}”", s=v.sur)


def _dateline(fact: dict) -> str:
    if fact.get("stage") == "Final":
        venue = FINAL_VENUE.get(fact["y"])
        return venue[1].upper() if venue else ""
    if fact.get("stage", "league") != "league" or fact["y"] in NEUTRAL_YEARS:
        return ""
    return city(fact.get("host"), fact["y"]).upper()


def _bat(fact: dict) -> str:
    runs, balls = fact["runs"], fact["balls"]
    star = "" if fact.get("out") else "*"
    b = [f"{_plural(fact['fours'], 'four')}" if fact.get("fours") else "",
         f"{_plural(fact['sixes'], 'six', 'sixes')}" if fact.get("sixes") else ""]
    b = [x for x in b if x]
    return f"{runs}{star} off {_plural(balls, 'ball')}" + (f" ({' and '.join(b)})" if b else "")


def _ball(fact: dict) -> str:
    return f"{fact['wickets']} for {fact['bowl_runs']} in {_overs(fact['bowl_balls'])} overs"


def _outcome(fact: dict) -> str:
    """How the match ended, from your side."""
    club, opp = _club(fact.get("club")), _club(fact["opp"])
    margin = _margin_words(fact.get("margin", ""), fact.get("winner_is_you"))
    if fact["result"] == "W":
        return f"{club} beat {opp}{margin}."
    if fact["result"] == "L":
        return f"It was not enough: {opp} won{margin}."
    return f"The match against {opp} went all the way, and stayed level."


def _margin_words(margin: str, winner_is_you: bool | None) -> str:
    """The engine's margin names the winner ("RCB 2015 by 9 wickets"); keep the 'by ...'."""
    m = re.search(r"\bby (.+)$", margin or "")
    return f" by {m.group(1)}" if m else ""


# --- the clippings, kind by kind ------------------------------------------------------------


def _match_clip(fact: dict, v: Voice, weight: int, headline: str, dek: str,
                body: list[str], quote: str = "", tone: str = "") -> Clipping:
    stage = fact.get("stage", "league")
    section = "BACK PAGE" if stage == "Final" else "SPORT"
    return Clipping(fact["k"], fact["y"], section, headline, dek, _dateline(fact),
                    [b for b in body if b], quote, weight, fact.get("m"),
                    fact.get("club"), tone)


def _stage_words(fact: dict) -> str:
    stage = fact.get("stage", "league")
    return "" if stage == "league" else f" in the {stage}"


def _hundred(f: dict, v: Voice) -> Clipping:
    sur, opp = v.sur, _club(f["opp"])
    chase = f.get("chasing")
    head = _pick(v, f, [
        f"{sur} hundred lights up the {f['y']} IPL",
        f"Take a bow, {sur}",
        f"{sur} {f['runs']}{'' if f['out'] else '*'} leaves {opp} shell-shocked",
        f"Century for {sur}" + (" as the chase is sealed" if chase and f["result"] == "W" else ""),
        f"Magnificent {sur} reaches three figures",
    ])
    dek = _pick(v, f, [
        f"{_bat(f)}{_stage_words(f)} against {opp}",
        f"An innings of {f['runs']} that nobody at the ground will forget",
        f"The first IPL hundred of {sur}'s career" if f.get("first") else f"Another hundred, this one against {opp}",
    ], "dek")
    body = [
        f"{_who(v, f)} made {_bat(f)} against {opp}{_stage_words(f)}.",
        _pick(v, f, [
            "There was barely a false stroke in it.",
            "The fielders spent most of the evening watching the ball go over them.",
            "Bowlers were changed, fields were moved, and nothing worked.",
            "The scoreboard operators were kept busy all night.",
        ], "colour"),
        _outcome(f),
        f"Beside {sur} the best of the rest was {f['mate']}." if f.get("mate") else "",
        _celebrate(v, f),
        "Player of the Match, and nobody argued." if f.get("pom") else "",
    ]
    quote = _quote(v, f, [
        "I just watched the ball and trusted my game.",
        "Once I got through the first ten balls I felt I could hit anything.",
        "That one is for everybody who backed me when I was not scoring.",
        "The pitch was good, so I told myself to bat through.",
    ])
    return _match_clip(f, v, 90 + (15 if f.get("stage", "league") != "league" else 0),
                       head, dek, body, quote, "gold")


def _blitz(f: dict, v: Voice) -> Clipping:
    sur, opp = v.sur, _club(f["opp"])
    sr = round(100 * f["runs"] / max(f["balls"], 1))
    head = _pick(v, f, [f"{sur} blitz blows {opp} away", f"{f['runs']} off {f['balls']}: {sur} goes berserk",
                        f"Carnage: {sur} takes {opp} apart", f"{sur} at a strike rate of {sr}"])
    dek = f"{_bat(f)}{_stage_words(f)}"
    body = [
        f"{_who(v, f)} took {opp} for {f['runs']} off just {f['balls']} balls, a strike rate of {sr}.",
        _pick(v, f, ["It was over almost before it began.",
                     "Every bowler who came on went for plenty.",
                     "The ball spent more time in the stands than on the square."], "colour"),
        _outcome(f),
        "Player of the Match." if f.get("pom") else "",
    ]
    quote = _quote(v, f, ["When it is in the arc, it goes.", "I was not thinking, just hitting.",
                          "The plan was simple: see ball, hit ball."])
    return _match_clip(f, v, 60, head, dek, body, quote)


def _five_for(f: dict, v: Voice) -> Clipping:
    sur, opp = v.sur, _club(f["opp"])
    head = _pick(v, f, [f"{sur} rips through {opp}", f"Five for {sur}",
                        f"{sur} {f['wickets']}-{f['bowl_runs']} routs {opp}",
                        f"Unplayable {sur} takes five"])
    dek = _pick(v, f, [f"{_ball(f)}{_stage_words(f)}",
                       f"The first five-wicket haul of {sur}'s IPL career" if f.get("first")
                       else f"{opp} had no answer"], "dek")
    body = [
        f"{_who(v, f)} took {_ball(f)} against {opp}{_stage_words(f)}.",
        _pick(v, f, ["Batter after batter walked back with a shake of the head.",
                     "Every wicket seemed to bring another.",
                     "The batting side never worked out which way it was going."], "colour"),
        _outcome(f),
        _celebrate(v, f),
        "Player of the Match." if f.get("pom") else "",
    ]
    quote = _quote(v, f, ["I kept it simple and hit the top of off.",
                          "The ball was coming out of the hand beautifully.",
                          "Five is special. I will keep the ball."])
    return _match_clip(f, v, 90 + (15 if f.get("stage", "league") != "league" else 0),
                       head, dek, body, quote, "gold")


def _hattrick(f: dict, v: Voice) -> Clipping:
    sur, opp = v.sur, _club(f["opp"])
    head = _pick(v, f, [f"Hat-trick! {sur} stuns {opp}", f"Three in three for {sur}",
                        f"{sur} joins the hat-trick club"])
    body = [
        f"{_who(v, f)} took three wickets in three balls against {opp}, and finished with {_ball(f)}.",
        "The crowd were on their feet before the third had even landed in the keeper's gloves.",
        _outcome(f),
        _celebrate(v, f),
    ]
    quote = _quote(v, f, ["I did not even know it was a hat-trick ball until the third one.",
                          "I just wanted to hit the stumps. Three times it worked."])
    return _match_clip(f, v, 92, head, "Three wickets in three balls", body, quote, "gold")


def _final_star(f: dict, v: Voice) -> Clipping:
    sur, opp = v.sur, _club(f["opp"])
    won = f["result"] == "W"
    what = _bat(f) if f.get("runs", 0) >= BIG_FINAL_RUNS else _ball(f)
    head = (_pick(v, f, [f"{sur} the hero on finals night", f"Big-match {sur} delivers"])
            if won else _pick(v, f, [f"{sur} heroics in vain", f"{sur} fights, {opp} lift the trophy"]))
    body = [f"{_who(v, f)} produced {what} in the final against {opp}.", _outcome(f)]
    return _match_clip(f, v, 85 if won else 55, head, what, body,
                       tone="gold" if won else "red")


def _playoff_hero(f: dict, v: Voice) -> Clipping:
    sur, opp = v.sur, _club(f["opp"])
    figs = [x for x in (_bat(f) if f.get("runs") else "", _ball(f) if f.get("wickets") else "") if x]
    head = _pick(v, f, [f"{sur} carries {_club(f.get('club'))} past {opp}",
                        f"{f['stage']}: {sur} the difference"])
    body = [f"{_who(v, f)} was Player of the Match in the {f['stage']} against {opp}"
            + (f", with {' and '.join(figs)}." if figs else "."), _outcome(f)]
    return _match_clip(f, v, 55, head, f"{f['stage']}: Player of the Match", body)


def _debut(f: dict, v: Voice) -> Clipping:
    club, sur = _club(f["club"]), v.sur
    noun = ROLE_NOUN.get(v.role, "player")
    age = f.get("age")
    head = _pick(v, f, [f"{club} sign {age}-year-old {sur}", f"Teenage {noun} {sur} joins {club}"
                        if age and age < 20 else f"Young {noun} {sur} joins {club}",
                        f"{club} take a punt on {sur}"])
    body = [
        f"{club} have signed {v.name}, {age}, for {_crore(f['price'])}." if age
        else f"{club} have signed {v.name} for {_crore(f['price'])}.",
        f"The {noun} will wear No. {v.number}." if v.number is not None else "",
        _pick(v, f, ["Scouts have been talking about the name for a while.",
                     "Nobody outside the domestic circuit has heard much about the youngster yet.",
                     "It is a small fee and, the franchise hopes, a big future."], "colour"),
    ]
    quote = _quote(v, f, ["I have dreamt about this since I was a kid.",
                          "I just want to learn from the senior players.",
                          f"Pulling on the {club} shirt will be special."])
    return Clipping("debut", f["y"], "SPORT", head, f"A {_crore(f['price'])} signing", city(f["club"], f["y"]).upper(),
                    [b for b in body if b], quote, 70, None, f["club"])


def _move(f: dict, v: Voice) -> Clipping:
    club, old, sur = _club(f["club"]), _club(f["from"]), v.sur
    head = _pick(v, f, [f"{sur} swaps {old} for {club}", f"{club} land {sur}",
                        f"New colours for {sur}"])
    deal = "a three-year deal" if f.get("contract") else "a one-year deal"
    body = [f"{_who(v, f)} has left {old} for {club} on {deal} worth {_crore(f['price'])} a season.",
            _pick(v, f, ["A fresh start, and a new dressing room to win over.",
                         "The move had been talked about for weeks.",
                         f"{old} supporters will be sorry to see the name go."], "colour")]
    return Clipping("move", f["y"], "SPORT", head, f"{_crore(f['price'])} a season", city(f["club"], f["y"]).upper(),
                    body, "", 40, None, f["club"])


def _mega(f: dict, v: Voice) -> Clipping:
    club, sur = _club(f["club"]), v.sur
    if f.get("retained"):
        head = _pick(v, f, [f"{club} keep {sur}", f"{sur} retained at {_crore(f['price'])}"])
        body = [f"{club} have retained {_who(v, f)} ahead of the mega auction, at {_crore(f['price'])} a season.",
                "In a year when every squad is torn up, keeping a player says plenty."]
        return Clipping("mega", f["y"], "AUCTION", head, "Retained before the mega auction", "",
                        body, "", 60, None, f["club"])
    if not f.get("sold"):
        head = f"No bids for {sur} at the auction"
        body = [f"{_who(v, f)} went unsold at a base price of {_crore(f['base'])}.",
                f"{club} picked up the {ROLE_NOUN.get(v.role, 'player')} late, at the base price."]
        return Clipping("mega", f["y"], "AUCTION", head, "A quiet day at the mega auction", "",
                        body, "", 45, None, f["club"], "red")
    rivals = f.get("rivals") or []
    big = f["price"] >= 1000
    head = _pick(v, f, [f"Bidding war: {sur} to {club} for {_crore(f['price'])}",
                        f"{club} win the race for {sur}",
                        f"{_crore(f['price'])}! {sur} goes to {club}"] if big else
                 [f"{sur} to {club} for {_crore(f['price'])}", f"{club} pick up {sur}"])
    fought = (f" after a fight with {' and '.join(rivals[:2])}" if rivals else "")
    body = [f"{club} bought {_who(v, f)} for {_crore(f['price'])} at the mega auction{fought}.",
            f"The paddles went up {_plural(f.get('bids', 0), 'time')} from a base price of {_crore(f['base'])}."
            if f.get("bids") else ""]
    return Clipping("mega", f["y"], "AUCTION", head, "The mega auction", "", [b for b in body if b],
                    "", 75 if big else 60, None, f["club"], "gold" if big else "")


def _title(f: dict, v: Voice) -> Clipping:
    club, sur, opp = _club(f["club"]), v.sur, _club(f.get("opp"))
    n = f.get("n", 1)
    venue = FINAL_VENUE.get(f["y"])
    head = (_pick(v, f, [f"{club} are champions", f"Champions! {club} win the IPL",
                         f"{club} crowned at the {venue[0]}" if venue else f"{club} crowned"])
            if n == 1 else _pick(v, f, [f"Another title for {sur}", f"{sur} wins title No. {n}",
                                        f"{club} champions, and {sur} has {_num(n)}"]))
    bat = f"made {_bat(f)}" if f.get("runs", 0) or f.get("balls", 0) else ""
    ball = f"took {_ball(f)}" if f.get("bowl_balls") else ""
    you = [x for x in ((ball, bat) if v.role in ("pace", "spin") else (bat, ball)) if x]
    body = [
        f"{club} beat {opp} in the final{_margin_words(f.get('margin', ''), True)} to win the IPL.",
        f"{_who(v, f)} {' and '.join(you)} on the night." if you else
        f"{_who(v, f)} watched the final from the dug-out." if f.get("played") is False else "",
        f"It is {sur}'s {_ord(n)} IPL title." if n > 1 else f"It is the first IPL title of {sur}'s career.",
        f"{f['star']} top-scored." if f.get("star") and (f.get("mate_runs") or 0) > (f.get("runs") or 0)
        else f"{sur} top-scored." if f.get("runs") else "",
        _celebrate(v, f) if f.get("played") is not False else "",
    ]
    quote = _quote(v, f, ["This is what you play for.", "I cannot describe it. Ask me tomorrow.",
                          "Every one of us earned this.", "We believed from the first match."])
    return Clipping("title", f["y"], "FRONT PAGE", head, f"Champions of IPL {f['y']}",
                    venue[1].upper() if venue else "", [b for b in body if b], quote, 100, f.get("m"),
                    f["club"], "gold")


def _final_lost(f: dict, v: Voice) -> Clipping:
    club, sur, opp = _club(f["club"]), v.sur, _club(f.get("opp"))
    venue = FINAL_VENUE.get(f["y"])
    head = _pick(v, f, [f"Heartbreak for {club}", f"{opp} edge {club} in the final",
                        f"So near for {sur} and {club}"])
    body = [f"{club} lost the final to {opp}{_margin_words(f.get('margin', ''), False)}.",
            f"{_who(v, f)} made {_bat(f)}." if f.get("runs") else
            f"{_who(v, f)} took {_ball(f)}." if f.get("bowl_balls") else ""]
    quote = _quote(v, f, ["It hurts. We will be back.", "One bad hour does not undo a good season."])
    return Clipping("final_lost", f["y"], "BACK PAGE", head, "Runners-up", venue[1].upper() if venue else "",
                    [b for b in body if b], quote, 50, f.get("m"), f["club"], "red")


def _cap(f: dict, v: Voice) -> Clipping:
    sur = v.sur
    orange = f["cap"] == "orange"
    what = f"{f['n']:,} runs" if orange else f"{f['n']} wickets"
    head = _pick(v, f, [f"{sur} wins the {'Orange' if orange else 'Purple'} Cap",
                        f"{what}: {sur} tops the {'run' if orange else 'wicket'} charts"])
    body = [f"{_who(v, f)} finished the season with {what}, more than anybody else in the league.",
            "The cap is presented at the final." if f.get("final") else ""]
    return Clipping("cap", f["y"], "SPORT", head, f"{'Orange' if orange else 'Purple'} Cap {f['y']}", "",
                    [b for b in body if b], "", 75, None, f.get("club"), "orange" if orange else "purple")


def _captain(f: dict, v: Voice) -> Clipping:
    club, sur = _club(f["club"]), v.sur
    head = _pick(v, f, [f"{club} hand the armband to {sur}", f"Captain {sur}"])
    body = [f"{_who(v, f)} will captain {club} this season.",
            _pick(v, f, ["The dressing room is said to have backed the call.",
                         "It is a big job, and a big vote of confidence."], "colour")]
    quote = _quote(v, f, ["It is an honour. Nothing changes in how I play.",
                          "I will lead the way I play: from the front."])
    return Clipping("captain", f["y"], "SPORT", head, "A new captain", city(f["club"], f["y"]).upper(),
                    body, quote, 45, None, f["club"])


def _injury(f: dict, v: Voice) -> Clipping:
    sur = v.sur
    head = _pick(v, f, [f"Injury blow for {sur}", f"{sur} faces a spell on the sidelines"])
    body = [f"{_who(v, f)} is out: {f['what'].lower()}.",
            "The physios will decide when it is safe to return."]
    return Clipping("injury", f["y"], "SPORT", head, f["what"], "", body, "", 30, None, f.get("club"), "red")


def _mate_last(f: dict, v: Voice) -> Clipping:
    name = f["name"]
    head = _pick(v, f, [f"Farewell, {name}", f"The end of an era: {name}"])
    body = [f"It would prove to be {name}'s last IPL season, after {_plural(f['seasons'], 'season')} in the league.",
            f"{v.sur} shared a dressing room with {name} for {_plural(f['together'], 'season')}."
            if f.get("together", 0) > 1 else f"{v.sur} and {name} were teammates this year."]
    return Clipping("mate_last", f["y"], "SPORT", head, "A real teammate's last IPL season", "",
                    body, "", 35, None, f.get("club"))


def _elite(f: dict, v: Voice) -> Clipping:
    sur = v.sur
    head = _pick(v, f, [f"{sur} among the very best", f"A season to remember for {sur}"])
    body = [f"{_who(v, f)} graded {f['grade']} for the season, the first 90-plus season of the career."]
    return Clipping("elite", f["y"], "SPORT", head, f"Season grade {f['grade']}", "", body, "", 50,
                    None, f.get("club"), "gold")


def _ambition(f: dict, v: Voice) -> Clipping:
    head = f"{v.sur} ticks off a dream"
    body = [f"One of the targets {_who(v, f)} set on the first day is done: {f['label']}."]
    return Clipping("ambition", f["y"], "SPORT", head, f["label"], "", body, "", 40, None, f.get("club"))


def _nt_debut(f: dict, v: Voice) -> Clipping:
    sur, country = v.sur, f["country"]
    noun = ROLE_NOUN.get(v.role, "player")
    head = _pick(v, f, [f"{sur} called up by {country}", f"{country} come calling for {sur}",
                        f"International honours for {sur}"])
    body = [f"{_who(v, f)} has been picked for {country} after an IPL season graded {f['grade']}.",
            f"The {noun} played {_plural(f['caps'], 'international')} in the months that followed.",
            _pick(v, f, ["The selectors had been watching all season.",
                         "Few doubted the call once the season was over.",
                         "It is the reward for a season nobody could ignore."], "colour")]
    quote = _quote(v, f, [f"Pulling on the {country} shirt is all I ever wanted.",
                          "I could not speak when the call came.",
                          "This one is for my family."])
    return Clipping("nt_debut", f["y"], "FRONT PAGE", head, f"{country} cap", "", body, quote, 88,
                    None, None, "gold")


def _nt_recalled(f: dict, v: Voice) -> Clipping:
    sur, country = v.sur, f["country"]
    head = _pick(v, f, [f"{sur} back in the {country} side", f"Recall for {sur}"])
    body = [f"{_who(v, f)} has been recalled by {country} after an IPL season graded {f['grade']}.",
            f"{_plural(f['caps'], 'international')} followed, taking the total to {f['total']}."]
    quote = _quote(v, f, ["I never stopped believing I would get another chance.",
                          "It is good to be back."])
    return Clipping("nt_recalled", f["y"], "SPORT", head, f"{country} recall", "", body, quote, 55)


def _nt_dropped(f: dict, v: Voice) -> Clipping:
    sur, country = v.sur, f["country"]
    head = _pick(v, f, [f"{country} leave out {sur}", f"Selectors axe {sur}"])
    body = [f"{_who(v, f)} has lost a place in the {country} side after an IPL season graded {f['grade']}.",
            f"The international career stands at {_plural(f['total'], 'match', 'matches')} for now."]
    return Clipping("nt_dropped", f["y"], "SPORT", head, "Dropped", "", body, "", 45, tone="red")


def _nt_caps(f: dict, v: Voice) -> Clipping:
    sur, country = v.sur, f["country"]
    head = f"{f['mark']} not out: {sur}'s {country} milestone"
    body = [f"{_who(v, f)} has now played {f['mark']} internationals for {country}."]
    return Clipping("nt_caps", f["y"], "SPORT", head, f"{f['mark']} internationals", "", body, "", 50,
                    None, None, "gold")


# [A230] The choices a career makes, as the papers would tell them.
def _trade(f: dict, v: Voice) -> Clipping:
    club, old, sur = _club(f["club"]), _club(f["from"]), v.sur
    head = _pick(v, f, [f"{sur} traded to {club}", f"{old} let {sur} go in trade"])
    body = [f"{_who(v, f)} has been traded from {old} to {club} before the season.",
            _pick(v, f, ["The deal needed the player's agreement, and got it.",
                         f"{club} had wanted {sur} for some time."], "colour")]
    quote = _quote(v, f, ["They wanted me. That matters.", "It was time for something new."])
    return Clipping("trade", f["y"], "SPORT", head, "A trade before the season",
                    city(f["club"], f["y"]).upper(), body, quote, 42, None, f["club"])


def _endorse(f: dict, v: Voice) -> Clipping:
    sur = v.sur
    head = _pick(v, f, [f"{sur} the new face of a sportswear brand", f"{sur} signs a sponsorship deal"])
    body = [f"{_who(v, f)} skipped part of {_club(f['club'])}'s pre-season camp for a campaign shoot.",
            _pick(v, f, ["Not everyone at the club was impressed.",
                         "The team management declined to comment."], "colour")]
    return Clipping("endorse", f["y"], "GOSSIP", head, "A campaign shoot", "", body, "", 22,
                    None, f.get("club"))


def _hit_back(f: dict, v: Voice) -> Clipping:
    sur = v.sur
    head = _pick(v, f, [f"{sur} hits back at critics", f"\"Write what you like\": {sur} fires back"])
    body = [f"{_who(v, f)} answered the critics at a heated press conference.",
            _pick(v, f, ["It was not the answer the room expected.",
                         "The columnist in question was sitting in the second row."], "colour")]
    quote = _quote(v, f, ["Some of these people have never held a bat.",
                          "I know what I am worth. Watch the next match."])
    return Clipping("hit_back", f["y"], "SPORT", head, "A heated press conference", "", body,
                    quote, 32, None, f.get("club"), "red")


RENDER = {"hundred": _hundred, "blitz": _blitz, "five_for": _five_for, "hattrick": _hattrick,
          "final_star": _final_star, "playoff_hero": _playoff_hero, "debut": _debut,
          "move": _move, "mega": _mega, "title": _title, "final_lost": _final_lost, "cap": _cap,
          "captain": _captain, "injury": _injury, "mate_last": _mate_last, "elite": _elite,
          "ambition": _ambition, "nt_debut": _nt_debut, "nt_recalled": _nt_recalled,
          "nt_dropped": _nt_dropped, "nt_caps": _nt_caps,
          "trade": _trade, "endorse": _endorse, "hit_back": _hit_back}


def render(fact: dict, v: Voice) -> Clipping | None:
    """A fact as a clipping, or None for a kind this version does not know (a token from a
    newer build read by an older one)."""
    fn = RENDER.get(fact.get("k"))
    return _tidy(fn(fact, v)) if fn else None


def _tidy(c: Clipping) -> Clipping:
    """Every sentence starts with a capital, whichever way the name was dressed."""
    c.body = [b[:1].upper() + b[1:] for b in c.body if b]
    c.headline = c.headline[:1].upper() + c.headline[1:]
    return c


def edition(facts: list[dict], v: Voice) -> list[Clipping]:
    """A season's clippings, the most newsworthy first, at most `EDITION_MAX`."""
    clips = [c for c in (render(f, v) for f in facts) if c is not None]
    clips.sort(key=lambda c: -c.weight)
    return clips[:EDITION_MAX]


# --- facts from a season ----------------------------------------------------------------------

MATCH_FIELDS = ("stage", "result", "margin", "runs", "balls", "out", "fours", "sixes", "wickets",
                "bowl_runs", "bowl_balls", "pom", "chasing", "mate", "mate_runs", "batted")


def match_fact(line, i: int, year: int, club: str) -> dict | None:
    """The one story a match of yours makes, if any: the biggest of a hundred, a hat-trick, a
    five-wicket haul, a blitz, a big final, or a playoff won as Player of the Match."""
    if not line.played:
        return None
    lm = set(line.landmarks)
    final = line.stage == "Final"
    kind = None
    if "hattrick" in lm:
        kind = "hattrick"
    elif line.batted and line.runs >= 100:
        kind = "hundred"
    elif line.wickets >= 5:
        kind = "five_for"
    elif final and (line.runs >= BIG_FINAL_RUNS or line.wickets >= BIG_FINAL_WICKETS):
        kind = "final_star"
    elif line.batted and line.runs >= BLITZ_RUNS and line.runs >= 2 * line.balls:
        kind = "blitz"
    elif line.stage not in ("league", "Final") and line.pom and line.result == "W":
        kind = "playoff_hero"
    if kind is None:
        return None
    f = {"k": kind, "y": year, "m": i, "club": club, "opp": line.opp_franchise or line.opponent,
         "host": club if not line.chasing else line.opp_franchise}
    for name in MATCH_FIELDS:
        f[name] = getattr(line, name, None)
    return f


def match_facts(feed, year: int, club: str) -> list[dict]:
    out = []
    for i, line in enumerate(feed or []):
        f = match_fact(line, i, year, club)
        if f is not None:
            out.append(f)
    return out


@dataclass
class SeasonFacts:
    """What the career loop knows about a season beyond its feed, gathered in one place so
    `season_facts` stays a pure function of it."""
    k: int
    year: int
    club: str                          # display name
    price: int
    age: int
    moved_from: str | None = None      # last season's club, when this is a move
    contract: bool = False
    mega: dict | None = None           # {"retained", "sold", "price", "base", "bids", "rivals"}
    champion: bool = False
    finalist: bool = False
    title_number: int = 0
    orange: int | None = None          # the cap's figure, when you won it
    purple: int | None = None
    first_captaincy: bool = False
    injuries: list[str] = field(default_factory=list)
    mate: dict | None = None           # {"name", "seasons", "together"}
    first_elite: int | None = None     # the grade, when it is the career's first 90+
    ambitions: list[str] = field(default_factory=list)   # labels achieved this season
    first_hundred: bool = False        # the season's first hundred is the career's first
    first_five_for: bool = False
    national: dict | None = None       # [A229] {"country", "event", "caps", "total", "grade"}
    caps_marks: list[int] = field(default_factory=list)   # every 50 internationals crossed
    traded: bool = False               # [A230] the move was a trade
    stories: list[str] = field(default_factory=list)      # [A230] "endorse", "hit_back"


def season_facts(s: SeasonFacts, feed) -> list[dict]:
    y, club = s.year, s.club
    out = match_facts(feed, y, club)
    for f in out:
        if f["k"] == "hundred" and s.first_hundred:
            f["first"] = True
            s.first_hundred = False
        if f["k"] == "five_for" and s.first_five_for:
            f["first"] = True
            s.first_five_for = False
    final = next((i for i in range(len(feed or []) - 1, -1, -1) if feed[i].stage == "Final"), None)
    if s.champion or s.finalist:
        # The final is told by the title or runners-up clipping: no second one about it.
        out = [f for f in out if not (f["m"] == final and f["k"] == "final_star")]
        f = {"k": "title" if s.champion else "final_lost", "y": y, "club": club,
             "n": s.title_number, "m": final}
        if final is not None:
            line = feed[final]
            f.update({name: getattr(line, name, None) for name in MATCH_FIELDS})
            f.update({"opp": line.opp_franchise or line.opponent, "star": line.mate,
                      "played": line.played})
        out.append(f)
    if s.k == 0:
        out.append({"k": "debut", "y": y, "club": club, "price": s.price, "age": s.age})
    elif s.mega is not None:
        out.append({"k": "mega", "y": y, "club": club, **s.mega})
    elif s.moved_from:
        out.append({"k": "trade" if s.traded else "move", "y": y, "club": club,
                    "from": s.moved_from, "price": s.price, "contract": s.contract})
    for kind in s.stories:
        out.append({"k": kind, "y": y, "club": club})
    if s.orange is not None:
        out.append({"k": "cap", "y": y, "club": club, "cap": "orange", "n": s.orange})
    if s.purple is not None:
        out.append({"k": "cap", "y": y, "club": club, "cap": "purple", "n": s.purple})
    if s.first_captaincy:
        out.append({"k": "captain", "y": y, "club": club})
    for i, what in enumerate(s.injuries):
        out.append({"k": "injury", "y": y, "club": club, "what": what, "m": None, "i": i})
    if s.mate:
        out.append({"k": "mate_last", "y": y, "club": club, **s.mate})
    if s.first_elite is not None:
        out.append({"k": "elite", "y": y, "club": club, "grade": s.first_elite})
    for label in s.ambitions:
        out.append({"k": "ambition", "y": y, "club": club, "label": label})
    nt = s.national
    if nt and nt.get("event") in ("debut", "recalled", "dropped"):
        out.append({"k": f"nt_{nt['event']}", "y": y, "country": nt["country"], "caps": nt["caps"],
                    "total": nt["total"], "grade": nt["grade"]})
    for mark in s.caps_marks:
        out.append({"k": "nt_caps", "y": y, "country": nt["country"] if nt else "", "mark": mark})
    return out


# --- the end of a career ------------------------------------------------------------------------

EPILOGUES = {
    "coach": "Coaching",
    "commentary": "The commentary box",
    "academy": "An academy back home",
    "quiet": "A quiet life",
}


def farewell(v: Voice, ending: str, seasons: int, runs: int, wickets: int, titles: int,
             main_club: str | None, last_year: int, runs_rank: int | None,
             wickets_rank: int | None) -> Clipping:
    sur = v.sur
    fact = {"k": "farewell", "y": last_year}
    figs = [f"{runs:,} runs" if runs else "", f"{wickets} wickets" if wickets else "",
            _plural(titles, "IPL title") if titles else ""]
    figs = [x for x in figs if x]
    ranks = []
    if runs_rank is not None and runs_rank <= 25:
        ranks.append(f"{_ord(runs_rank)} on the all-time run list")
    if wickets_rank is not None and wickets_rank <= 25:
        ranks.append(f"{_ord(wickets_rank)} on the all-time wicket list")
    if ending == "released":
        head = _pick(v, fact, [f"No takers for {sur}", f"The phone stops ringing for {sur}"])
        first = f"Two poor seasons running, and no franchise has come in for {_who(v, fact)}."
        quote = _quote(v, fact, ["That is cricket. I gave it everything.",
                                 "I am not finished with the game, even if the game is finished with me."])
    elif ending == "retired":
        head = _pick(v, fact, [f"{sur} calls time", f"Thank you, {sur}", f"{sur} bows out"])
        first = f"{_who(v, fact)} has retired from the IPL after {_plural(seasons, 'season')}."
        quote = _quote(v, fact, ["It is the right time. The body has been telling me for a while.",
                                 "I want to go while people still ask why, not when.",
                                 "I am grateful for every single match."])
    else:
        head = _pick(v, fact, [f"{sur}: still going", f"Nineteen seasons on, {sur} is still here"
                               if seasons >= 19 else f"{sur} plays on"])
        first = f"{_who(v, fact)} has played every season up to {last_year}, and the story is not over."
        quote = ""
    body = [first,
            f"The career reads {', '.join(figs)}." if figs else "",
            f"That puts {sur} {' and '.join(ranks)}." if ranks else "",
            f"{_club(main_club)} supporters will remember the name longest." if main_club else ""]
    return _tidy(Clipping("farewell", last_year, "FRONT PAGE" if ranks or titles else "SPORT", head,
                    f"{_plural(seasons, 'season')}", "", [b for b in body if b], quote, 100, None,
                    main_club, "gold" if titles or ranks else ""))


def epilogue(key: str, v: Voice, main_club: str | None, last_year: int) -> Clipping:
    sur, club = v.sur, _club(main_club) or "a franchise"
    fact = {"k": "epilogue", "y": last_year}
    home = v.home or "home"
    text = {
        "coach": [f"{_who(v, fact)} is now on the coaching staff at {club}, passing on what the "
                  f"years in the middle taught.",
                  "Young players say the nets are tougher than any match."],
        "commentary": [f"These days {_who(v, fact)} calls the games from the commentary box.",
                       "Viewers say the analysis is as sharp as the batting or bowling ever was."],
        "academy": [f"{_who(v, fact)} has opened a cricket academy in {home}.",
                    "The first intake has already sent two youngsters to state trials."],
        "quiet": [f"{_who(v, fact)} has stepped away from the game entirely.",
                  "The kit bag is in the attic. The memories are not."],
    }[key]
    head = {"coach": f"Where are they now? Coach {sur}",
            "commentary": f"Where are they now? {sur} behind the microphone",
            "academy": f"Where are they now? {sur} gives back",
            "quiet": f"Where are they now? {sur}, away from it all"}[key]
    return _tidy(Clipping("epilogue", last_year, "FEATURES", head, EPILOGUES[key], "", text, "",
                          0, None, main_club if key == "coach" else None))


# --- the league around you, from the archive --------------------------------------------------


def league_news(year: int, clubs_by_year: dict[int, dict[str, str]],
                mega_years: frozenset[int]) -> list[str]:
    """Real things that happened to the league in `year`, read off which franchises the
    archive has that year (canonical name -> the name it played under). Nothing here says
    who won: the season you play is simulated, so a real champion would be a contradiction."""
    now = clubs_by_year.get(year, {})
    before = clubs_by_year.get(year - 1, {})
    out: list[str] = []
    if year == min(clubs_by_year):
        out.append(f"The first season of the Indian Premier League: {_num(len(now))} franchises.")
    else:
        earlier = {c for y, cs in clubs_by_year.items() if y < year for c in cs}
        later = {c for y, cs in clubs_by_year.items() if y > year for c in cs}
        for c in sorted(set(now) - set(before)):
            if c in earlier:
                gap = year - max(y for y, cs in clubs_by_year.items() if y < year and c in cs) - 1
                out.append(f"{now[c]} are back after {_plural(gap, 'season')} away.")
            else:
                out.append(f"{now[c]} play their first IPL season.")
        for c in sorted(set(before) - set(now)):
            if c in later:
                out.append(f"{before[c]} are absent this season.")
            else:
                out.append(f"{before[c]} are gone: {year - 1} was their last season.")
        for c in sorted(set(now) & set(before)):
            if now[c] != before[c]:
                out.append(f"{before[c]} are now {now[c]}.")
        if len(now) != len(before):
            out.append(f"{_num(len(now)).capitalize()} teams this year.")
    if year in NEUTRAL_YEARS:
        out.append(f"The season is played in {NEUTRAL_YEARS[year]}.")
    if year in mega_years:
        out.append("A mega auction tears up every squad.")
    return out
