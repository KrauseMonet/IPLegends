"""The daily challenge: today's scenario, today's shared deck, and one attempt each.

Nothing here reimplements the draft. A day is expressed as (a restricted `Deck`, a seed, a
reroll budget of zero, a fallback pool) and handed to `web.session.replay`, which hands it
to `run_draft` -- so the overseas cap, the forward check, the deal-time guarantee and the
reposition rules are the same code the solo draft and the rooms already run. A second copy
of any of that would be a second place for it to drift (the standing argument this module
inherits from `web/rooms.py`'s own refusal to reimplement the loop).

Three things ARE specific to a day and live here:

* **The deck is restricted to the day's sixteen squads.** `Deck.cards_by_fs` still carries
  the whole archive -- only `fs_ids`, the ids actually drawn from, is narrowed -- because
  the fallback pool has to be able to reach squads outside the day when it fires.

* **A per-player seed.** Everyone gets the same sixteen squads and their own order through
  them, which is what makes the challenge comparable without being identical.

* **No rerolls.** `rerolls_allowed=0`, so a `Reroll` in a submitted state is refused rather
  than silently tolerated. Repositions are untouched: the batting order stays rearrangeable,
  exactly as in the solo draft.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, replace

from etl.feasibility import Deck
from game.scenarios import (
    DAILY_DECK_SIZE, Outcome, Scenario, bonuses_on_offer, choose_deck, daily_seed,
    evaluate, generate, overs_words, rank_key,
)
from game.season import Side
from game.simulator import Innings, Model, Player, play_innings
from web import session as sess

# Rerolls are the one solo affordance a daily challenge withholds: everybody is answering
# the same question off the same squads, so being able to wave a squad away would make two
# scores incomparable. Repositions are NOT withheld -- rearranging a batting order is skill
# applied to what you were dealt, not an escape from it.
DAILY_REROLLS = 0


def deck_for_day(full: Deck, fs_ids) -> Deck:
    """The day's deck: the given squads only, over the whole archive's cards.

    `cards_by_fs` is the FULL mapping deliberately. `run_draft` draws ids from `fs_ids` and
    then looks the squad up in `cards_by_fs`, so the fallback pool -- which names ids from
    outside the day -- would find nothing if this were narrowed too."""
    return Deck(cards_by_fs=full.cards_by_fs, fs_ids=list(fs_ids))


def player_seed(challenge_date, account_id: int) -> int:
    """This player's own sequence through the shared deck.

    Derived from the date and the account so it is reproducible: a reload deals the same
    order, and a stored result can be re-verified later from nothing but the row. Built
    through `random.Random(str)` rather than `hash()` for the reason `daily_seed` documents
    -- a salted hash would make two servers disagree about the same player's deal."""
    return random.Random(f"daily:{challenge_date}:{account_id}").getrandbits(31)


def replay_day(full: Deck, challenge_date, account_id: int, deck_fs_ids,
               moves) -> sess.Session:
    """One player's draft for one day, rebuilt from scratch (SPEC 11.3).

    The fallback pool is the whole archive, and it is what stops a one-shot challenge being
    lost to a dead end: measured on the real deck through `run_draft`, a restricted sixteen
    strands a careless drafter 3.2% of the time against 0% on the full deck, and the
    top-up takes that back to ~0% while firing on about 15 picks in 6,000. `Result.widened`
    counts it, so a day whose deck cannot serve its own drafters is visible rather than
    quietly papered over."""
    return sess.replay(
        deck_for_day(full, deck_fs_ids),
        player_seed(challenge_date, account_id),
        moves,
        rerolls_allowed=DAILY_REROLLS,
        fallback_fs_ids=tuple(full.fs_ids),
    )


def build_day(full: Deck, challenge_date) -> list[int]:
    """The sixteen squads everybody plays today, from the date alone."""
    return choose_deck(random.Random(daily_seed(challenge_date)), full.fs_ids,
                       DAILY_DECK_SIZE)


# --- playing a day ------------------------------------------------------------------------

def _average_attack(model: Model) -> list[Player]:
    """Five league-average bowlers -- every delta zero, the same synthetic side
    `game --validate` uses to show the tilt is the identity.

    A chase's target has to be player-independent or it is not the same target for
    everybody, which is the entire basis on which two chases are compared. So the
    opposition bats against this rather than against anyone's real attack."""
    return [Player(f"bowler {i}", 0.0, 0.0) for i in range(5)]


def side_for_fs(deck: Deck, fs_id: int) -> Side | None:
    """The best legal eleven a franchise-season could field, or None if it cannot field one
    -- a property of the squad, exactly as `historical_sides` treats it, and the reason day
    generation has to be able to reject a squad and draw another."""
    from game.__main__ import opposition_twelve, viable
    from game.season import _abbrev

    squad = list(deck.cards_by_fs.get(fs_id, ()))
    if len(squad) < 11 or not viable(squad):
        return None
    xi, impact = opposition_twelve(squad)
    if len(xi) != 11:
        return None
    card = squad[0]
    return Side(name=f"{card.franchise} {card.season_year}",
                short=_abbrev(card.franchise, card.season_year), xi=xi, impact=impact)


def opposition_total(model: Model, side: Side, rng: random.Random) -> Innings:
    """What this side posts against a league-average attack. Played ONCE, when the day is
    created; only the total survives into the stored scenario."""
    from game.__main__ import lineup
    return play_innings(model, lineup(list(side.xi), model), _average_attack(model), rng)


def with_bowling_depth(side: Side) -> Side:
    """The side that actually takes the field, with the Impact Player locked in where the
    eleven alone cannot field five bowlers.

    A twelve is legal with five bowling options across all TWELVE (`order_errors` counts
    them there, not over the eleven), so a perfectly legal eleven can hold four and rely on
    the Impact Player for the fifth. `attack()` then returns four, five bowlers' worth of
    overs runs out around the seventeenth, and `choose_bowler` raises on an empty sequence
    -- a 500 on somebody's single attempt of the day, and one this only became likely
    enough to hit because every kind generated today makes the player bowl. It was
    reachable on a defence before that and simply never came up.

    `game.season` settled this already (A78): where the eleven falls short the Impact
    Player bowls, full stop -- not a situational call. Reused rather than reimplemented,
    because a second copy of a legality floor is a second place for it to drift."""
    from game.season import _apply_bowling_impact, _bowling_depth_shortfall

    forced = _bowling_depth_shortfall(side)
    if forced is None:
        return side
    return replace(side, xi=_apply_bowling_impact(list(side.xi), forced, side.impact))


def play_day(model: Model, scenario: Scenario, mine: Side, opposition: Side | None,
             rng: random.Random) -> tuple[Innings, Innings | None]:
    """The player's own innings, and the opposition's where one is really played.

    Every kind generated today is a FULL MATCH: the opposition bats against the player's
    own five bowlers and the player bats against the opposition's real attack, so both
    halves of a drafted twelve are on the field. Which innings comes first is the kind's
    (`player_bats_first`), and nothing here branches on a kind name.

    A LEGACY chase returns `(mine, None)`: its target was fixed when the day was created,
    so the opposition is not replayed per player and nobody bowls at them. That path is
    left byte-for-byte as it was -- including the order in which it draws from `rng` --
    because a stored day has to replay exactly as it was scored."""
    from game.__main__ import attack, lineup

    # Before anything is played, and for BOTH sides: whether a side can field five bowlers
    # at all is a property of the eleven, and it decides the batting order too.
    mine = with_bowling_depth(mine)
    my_batting = lineup(list(mine.xi), model, mine.impact)
    if scenario.spec.fixed_target:
        return play_innings(model, my_batting, _average_attack(model), rng,
                            target=scenario.target), None

    if opposition is None:
        raise ValueError("a full match is decided by both sides; it needs the opposition")
    opposition = with_bowling_depth(opposition)
    their_batting = lineup(list(opposition.xi), model, opposition.impact)
    my_attack = attack(list(mine.xi), model, mine.impact)
    # The one thing a legacy DEFEND_BY still does differently: its own first innings faced
    # the synthetic five. Reading that off the scenario rather than the kind keeps the old
    # day scoring as it always did without a special case here.
    their_attack = (attack(list(opposition.xi), model, opposition.impact)
                    if scenario.opposition_bowls else _average_attack(model))

    if scenario.player_bats_first:
        first = play_innings(model, my_batting, their_attack, rng)
        reply = play_innings(model, their_batting, my_attack, rng, target=first.runs)
        return first, reply

    theirs = play_innings(model, their_batting, my_attack, rng)
    return play_innings(model, my_batting, their_attack, rng, target=theirs.runs), theirs


@dataclass
class DayPlay:
    """A finished daily, in the two shapes it is needed in: SCORED, and SHOWN.

    They are not the same thing and keeping them apart is the point of this class. A chase
    is scored with no opposition innings at all (`evaluate(theirs=None)`) because nobody
    bowled at them -- the target was fixed when the day was created. But the same match
    still has to be WATCHED and read as a scorecard, which needs two innings. So the
    opposition's is derived here for display and never handed to the evaluator: doing that
    would award a four-wicket haul for balls the player never bowled, which is A101's
    mistake in a place where it would look entirely reasonable."""

    outcome: Outcome
    first: Innings
    second: Innings
    first_label: str
    second_label: str
    # Whether each innings was bowled by REAL, named players. Decided HERE, where the two
    # innings are actually constructed and which attack bowled each is known for certain,
    # rather than re-derived from the kind by whoever renders a scorecard -- a synthetic
    # league-average five has no people in it, and putting "bowler 3" on a card credits a
    # wicket to somebody who does not exist.
    first_real_bowling: bool = True
    second_real_bowling: bool = True
    # Carried so a caller never has to ask the kind again which side is the player's.
    player_bats_first: bool = False


def play_and_score(full: Deck, model: Model, day: "Day", account_id: int,
                   state: str) -> DayPlay:
    """Rebuild one player's finished daily from what is stored -- the day, the account and
    their state -- and both play it and mark it.

    Nothing about the result is persisted beyond the state and the numbers the leaderboard
    ranks on (A19): the match is a pure function of those three, so a page reload re-derives
    exactly the same innings rather than reading a stored copy that could drift from the
    scoring beside it."""
    seed, moves = decode_own_state(state, day.challenge_date, account_id)
    session = sess.replay(deck_for_day(full, day.deck_fs_ids), seed, moves,
                          rerolls_allowed=DAILY_REROLLS,
                          fallback_fs_ids=tuple(full.fs_ids))
    if session.deal is not None:
        raise DailyError("this draft is not finished")

    mine = Side(name="You", short="YOU", xi=list(session.order),
                impact=session.impact, you=True)
    opposition = side_for_fs(full, day.scenario.opposition_fs_id)
    # The match's own rng, seeded from the day and the account: the same attempt always
    # plays out the same way, so a reload shows the innings the player already watched.
    rng = random.Random(f"daily-match:{day.challenge_date}:{account_id}")

    sc = day.scenario
    if sc.spec.fixed_target:
        # A LEGACY chase. The opposition's innings is re-derived from the DAY's own seed --
        # the same call `_generate_day` made to fix the target -- so every player watches
        # the identical first innings and the total on screen is the one they are chasing.
        # It is shown and never scored: handing it to the evaluator would award a bowling
        # bonus for balls the player never bowled.
        their_innings = opposition_total(
            model, opposition, random.Random(daily_seed(day.challenge_date)))
        outcome, my_innings, _none = score_day(model, sc, mine, None, rng)
        return DayPlay(outcome, their_innings, my_innings, opposition.name, "You",
                       first_real_bowling=False, second_real_bowling=False)

    outcome, my_innings, their_innings = score_day(model, sc, mine, opposition, rng)
    if sc.player_bats_first:
        return DayPlay(outcome, my_innings, their_innings, "You", opposition.name,
                       first_real_bowling=sc.opposition_bowls, second_real_bowling=True,
                       player_bats_first=True)
    return DayPlay(outcome, their_innings, my_innings, opposition.name, "You",
                   first_real_bowling=True, second_real_bowling=sc.opposition_bowls)


def score_day(model: Model, scenario: Scenario, mine: Side, opposition: Side | None,
              rng: random.Random) -> tuple[Outcome, Innings, Innings | None]:
    """Play it and mark it in one call, so the two can never end up done against different
    innings -- the shape A116 had to repair once already, where a card and the scorecard
    beside it were computed from different bases."""
    my_innings, their_innings = play_day(model, scenario, mine, opposition, rng)
    return evaluate(scenario, my_innings, their_innings), my_innings, their_innings


# `bonuses_on_offer` is re-exported from `game.scenarios` rather than reimplemented here.
# It used to be a hand-written `if kind == DEFEND_BY` branch, which was right for three
# kinds and would have gone quietly stale on the fourth -- availability is now derived
# from which innings each bonus reads, beside the bonuses themselves.


# --- streaks ------------------------------------------------------------------------------

def streaks(played: list, today) -> tuple[int, int]:
    """(current, longest) run of consecutive days played, from the dates alone.

    Computed in Python rather than as a gaps-and-islands query, for the usual reason: this
    is a rule about evidence, and a rule is worth being able to test without a database.
    The data is tiny — one row per day per account — so there is nothing to gain by pushing
    it into SQL.

    **A streak is not broken until a day has actually been MISSED, which is the rule the
    whole feature turns on.** Playing yesterday and not yet today leaves it alive: nothing
    has been lost, it simply has not been extended, and today is still there to be played.
    Counting only runs that reach today would report a broken streak to somebody at
    breakfast and then an intact one after they played, which is both wrong and the exact
    moment the streak is supposed to be doing its work.

    So the current run is counted back from TODAY if today has been played, and from
    YESTERDAY otherwise. Anything older than that is a genuinely missed day and ends it.
    """
    import datetime

    days = sorted(set(played))
    if not days:
        return 0, 0

    longest = run = 1
    for prev, day in zip(days, days[1:]):
        run = run + 1 if (day - prev).days == 1 else 1
        longest = max(longest, run)

    one_day = datetime.timedelta(days=1)
    last = days[-1]
    if last != today and last != today - one_day:
        return 0, longest          # the most recent play is older than yesterday

    current = 1
    for prev, day in zip(reversed(days[:-1]), reversed(days[1:])):
        if (day - prev).days != 1:
            break
        current += 1
    return current, longest


def played_dates(conn, account_id: int) -> list:
    """Every day this account has finished, oldest first. One small column -- the whole
    point of computing the streak in Python is that this is all it needs."""
    return [r[0] for r in conn.execute(
        "select challenge_date from daily_results where account_id = %s "
        "order by challenge_date", (account_id,)).fetchall()]


# --- the shareable result ------------------------------------------------------------------

# Where a shared line points people. Not derived from the request's own Host header: a
# result pasted into a chat has to work for whoever reads it, and a link built from
# whatever host the submitter happened to use would carry a preview deployment's URL into
# other people's messages.
SHARE_URL = "iplegends.vercel.app/daily"


def share_text(day: "Day", result: dict, rank: int | None = None,
               players: int | None = None, streak: int = 0) -> str:
    """The one-tap line a player posts somewhere, and the rule that shapes it is that it
    must be SPOILER-FREE.

    It carries the challenge, whether they beat it and by how much -- and never a single
    player they picked. Naming the twelve would ruin the day for whoever reads it, which
    is the opposite of what sharing is for: everybody gets the same deal, so a reader can
    go and answer the same question rather than being told the answer. Wordle's grid is
    the same trick -- the pattern, never the word.

    Built here rather than in the page for the reason `Scenario.describe` is: this is
    wording that has to agree with the scoring, and a second copy in JavaScript would be a
    second place for "7 wickets in hand" and "14 short" to drift from what actually
    happened."""
    from game.scenarios import BONUS_LABELS

    sc = day.scenario
    lines = [f"Legends Almanack — {day.challenge_date.day} {day.challenge_date:%b}"]

    stage = sc.stage if sc.stage.startswith("Qualifier") else f"the {sc.stage}"
    lines.append(f"{sc.short()} · {sc.opposition_name} · {stage}")

    lines.append(("✅ " if result["objective_met"] else "❌ ")
                 + _share_outcome(sc, result))
    for b in result.get("bonuses") or []:
        lines.append(f"⭐ {BONUS_LABELS.get(b, b)}")
    if rank is not None and players:
        lines.append(f"#{rank} of {players} today")
    # Only from two days up: "1 day streak" says nothing a reader cannot already see from
    # the line above it, and every first-time player would post one.
    if streak >= 2:
        lines.append(f"🔥 {streak}-day streak")
    lines.append(SHARE_URL)
    return "\n".join(lines)


def _share_outcome(scenario: Scenario, result: dict) -> str:
    """The same two-branch rule `evaluate` uses, in the words a reader wants: a failed
    chase reports how close it came, because its margin is negative for exactly that
    reason (game/scenarios.py has the why)."""
    margin = result["margin"]
    if scenario.margin_unit == "runs":
        if margin > 0:
            return f"won by {margin} runs"
        return "tied" if margin == 0 else f"lost by {-margin} runs"
    # Both remaining units share one failure convention -- a negative margin is runs short,
    # never the unit named above (game/scenarios.py explains why ranking a failure on
    # wickets in hand would reward blocking out).
    if margin < 0:
        return f"fell {-margin} short"
    if scenario.margin_unit == "balls":
        return f"chased with {overs_words(margin)} overs to spare"
    return f"chased, {margin} wicket{'' if margin == 1 else 's'} in hand"


# --- the day, in the database ---------------------------------------------------------------

class DailyError(ValueError):
    """A daily request that cannot be honoured -- refused with a 4xx, never a 500."""


@dataclass
class Day:
    challenge_date: object
    seed: int
    scenario: Scenario
    deck_fs_ids: list[int]
    opposition_wickets: int = 0


def _scenario_row(sc: Scenario, opposition_wickets: int) -> str:
    return json.dumps({
        "opposition_fs_id": sc.opposition_fs_id, "opposition_name": sc.opposition_name,
        "stage": sc.stage, "target": sc.target,
        "wickets_required": sc.wickets_required, "runs_required": sc.runs_required,
        "overs_required": sc.overs_required, "bonus": sc.bonus,
        "opposition_wickets": opposition_wickets,
    })


def _scenario_from_row(kind: str, row: dict) -> Scenario:
    return Scenario(kind, row["opposition_fs_id"], row["opposition_name"], row["stage"],
                    target=row.get("target"),
                    wickets_required=row.get("wickets_required"),
                    runs_required=row.get("runs_required"),
                    overs_required=row.get("overs_required"),
                    bonus=row.get("bonus"))


def _generate_day(full: Deck, model: Model, challenge_date):
    """Today's scenario and deck, from the date alone.

    The opposition is drawn from the day's own deck rather than the whole archive, so the
    squads you are dealt and the side you are up against come from one coherent pool -- and
    it is redrawn until one can actually field a legal eleven, which `side_for_fs` decides
    and which is a property of the squad rather than a failure here.

    Nothing is simulated here any more. A live kind has no target to fix in advance -- the
    opposition makes whatever the player's own bowlers allow them -- so the day is decided
    from the date alone. `model` is kept in the signature because `ensure_day` has it and
    a future kind may want it, and because the legacy path below still reads one."""
    rng = random.Random(daily_seed(challenge_date))
    deck_fs_ids = choose_deck(rng, full.fs_ids, DAILY_DECK_SIZE)
    for fs_id in rng.sample(deck_fs_ids, len(deck_fs_ids)):
        side = side_for_fs(full, fs_id)
        if side is None:
            continue
        sc = generate(rng, fs_id, side.name, challenge_date)
        return Day(challenge_date, daily_seed(challenge_date), sc, deck_fs_ids)
    raise DailyError("no squad in today's deck can field a legal eleven")


def ensure_day(conn, challenge_date, full: Deck, model: Model) -> Day:
    """Today's challenge, created on first sight and read from then on.

    `on conflict do nothing` then re-select, rather than a check-then-insert: two players
    arriving in the same second would otherwise both generate a day and one would overwrite
    the other's -- and since a leaderboard is keyed to the stored scenario, the loser's
    result would be marked against a challenge nobody else played."""
    row = conn.execute(
        "select seed, scenario_kind, scenario, deck_fs_ids from daily_challenges "
        "where challenge_date = %s", (challenge_date,)).fetchone()
    if row is None:
        day = _generate_day(full, model, challenge_date)
        conn.execute(
            """
            insert into daily_challenges
                   (challenge_date, seed, scenario_kind, scenario, deck_fs_ids)
            values (%s, %s, %s, %s, %s)
            on conflict (challenge_date) do nothing
            """,
            (challenge_date, day.seed, day.scenario.kind,
             _scenario_row(day.scenario, day.opposition_wickets),
             json.dumps(day.deck_fs_ids)))
        row = conn.execute(
            "select seed, scenario_kind, scenario, deck_fs_ids from daily_challenges "
            "where challenge_date = %s", (challenge_date,)).fetchone()
    seed, kind, scenario, deck_fs_ids = row
    return Day(challenge_date, seed, _scenario_from_row(kind, scenario),
               list(deck_fs_ids), scenario.get("opposition_wickets", 0))


# --- submitting a result, and the board -------------------------------------------------

def decode_own_state(state: str, challenge_date, account_id: int):
    """Decode a submitted state, refusing one whose seed this player was never dealt.

    Lifted out of `submit` so it can be tested without a database, because it is the one
    integrity rule specific to a daily: elsewhere the seed IS the player's (they asked for
    a draft and got one), but here it is derived from the date and the account. A state
    carrying any other seed describes a deal nobody offered -- without this check a player
    could shop for a favourable draw by editing a single number, and every other check in
    `submit` would happily pass, because the resulting draft is perfectly legal. It is just
    not theirs."""
    seed, moves = sess.decode(state)
    if seed != player_seed(challenge_date, account_id):
        raise DailyError("this is not today's deal for this account")
    return seed, moves


def submit(conn, challenge_date, account_id: int, state: str,
           full: Deck, model: Model) -> tuple[Outcome, bool]:
    """Mark one player's attempt and record it. Returns (outcome, was_new).

    The client sends only a STATE -- never a score. Everything below is recomputed here, so
    a fabricated submission either fails to replay or replays into a real, played result
    (A102's own argument for why the save routes need no signature).

    Three things are verified rather than trusted, and the first is specific to a daily:
    the state's SEED is not the player's to choose. It is derived from the date and the
    account, so a state carrying any other seed is a deal this player was never offered --
    without this check somebody could shop for a favourable draw by editing one number.
    """
    day = ensure_day(conn, challenge_date, full, model)
    seed, moves = decode_own_state(state, challenge_date, account_id)

    session = sess.replay(deck_for_day(full, day.deck_fs_ids), seed, moves,
                          rerolls_allowed=DAILY_REROLLS,
                          fallback_fs_ids=tuple(full.fs_ids))
    if session.deal is not None:
        raise DailyError("this draft is not finished")
    if session.errors:
        raise DailyError(f"this twelve is not legal: {'; '.join(session.errors)}")

    outcome = play_and_score(full, model, day, account_id, state).outcome

    written = conn.execute(
        """
        insert into daily_results (challenge_date, account_id, state, objective_met,
                                   margin, bonus_points, bonuses)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (challenge_date, account_id) do nothing
        returning 1
        """,
        (challenge_date, account_id, state, outcome.objective_met, outcome.margin,
         outcome.bonus_points, json.dumps(list(outcome.bonuses_met)))).fetchone()
    return outcome, written is not None


# The board's order, written once. `game.scenarios.rank_key` is the same rule in Python,
# and the two must not drift -- a leaderboard that disagrees with the score it showed you
# is worse than no leaderboard, so the SQL is kept adjacent to the function it mirrors and
# a test asserts they agree on real rows.
_BOARD_ORDER = "objective_met desc, margin desc, bonus_points desc, completed_at asc"


def leaderboard(conn, challenge_date, limit: int = 50) -> list[dict]:
    """Today's board, best first. `completed_at` breaks a full tie so the order is total
    and stable -- without it two identical scores could swap places between requests, which
    reads as the board being wrong."""
    rows = conn.execute(
        f"""
        select a.username, r.objective_met, r.margin, r.bonus_points, r.bonuses
          from daily_results r join accounts a using (account_id)
         where r.challenge_date = %s
         order by {_BOARD_ORDER}
         limit %s
        """, (challenge_date, limit)).fetchall()
    return [{"username": u, "objective_met": met, "margin": m,
             "bonus_points": bp, "bonuses": list(bs or [])}
            for u, met, m, bp, bs in rows]


def rank_of(conn, challenge_date, account_id: int) -> int | None:
    """Where this player stands today, or None if they have not played.

    Counted with the same ordering the board uses rather than by scanning it, so a player
    outside the top `limit` still gets a true position."""
    row = conn.execute(
        f"""
        select pos from (
            select account_id, row_number() over (order by {_BOARD_ORDER}) as pos
              from daily_results where challenge_date = %s
        ) ranked where account_id = %s
        """, (challenge_date, account_id)).fetchone()
    return row[0] if row else None


def players_today(conn, challenge_date) -> int:
    """How many have finished today -- the denominator in "#3 of 12", which is what makes
    a rank mean anything to somebody reading it in a chat."""
    return conn.execute(
        "select count(*) from daily_results where challenge_date = %s",
        (challenge_date,)).fetchone()[0]


def result_for(conn, challenge_date, account_id: int) -> dict | None:
    """This player's own recorded attempt, if they have made one -- what the page shows
    instead of a draft once the day is spent."""
    row = conn.execute(
        "select state, objective_met, margin, bonus_points, bonuses from daily_results "
        "where challenge_date = %s and account_id = %s",
        (challenge_date, account_id)).fetchone()
    if row is None:
        return None
    state, met, margin, bonus, bonuses = row
    return {"state": state, "objective_met": met, "margin": margin,
            "bonus_points": bonus, "bonuses": list(bonuses or [])}
