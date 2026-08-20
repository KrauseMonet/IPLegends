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
from dataclasses import dataclass

from etl.feasibility import Deck
from game.scenarios import (
    DAILY_DECK_SIZE, DEFEND_BY, Outcome, Scenario, choose_deck, daily_seed, evaluate,
    generate, rank_key,
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


def play_day(model: Model, scenario: Scenario, mine: Side, opposition: Side | None,
             rng: random.Random) -> tuple[Innings, Innings | None]:
    """The player's own innings, and the opposition's where one is really played.

    A chase returns `(mine, None)`: the target was fixed when the day was created, so the
    opposition is not replayed per player and nobody bowls at them. A defence returns both,
    because the opposition genuinely chases what this player set -- that reply is the
    player's own bowling and belongs to them."""
    from game.__main__ import attack, lineup

    batting = lineup(list(mine.xi), model, mine.impact)
    if scenario.kind == DEFEND_BY:
        if opposition is None:
            raise ValueError("a defence needs the side that will chase")
        first = play_innings(model, batting, _average_attack(model), rng)
        reply = play_innings(model, lineup(list(opposition.xi), model),
                             attack(list(mine.xi), model, mine.impact), rng,
                             target=first.runs)
        return first, reply
    return play_innings(model, batting, _average_attack(model), rng,
                        target=scenario.target), None


def score_day(model: Model, scenario: Scenario, mine: Side, opposition: Side | None,
              rng: random.Random) -> tuple[Outcome, Innings, Innings | None]:
    """Play it and mark it in one call, so the two can never end up done against different
    innings -- the shape A116 had to repair once already, where a card and the scorecard
    beside it were computed from different bases."""
    my_innings, their_innings = play_day(model, scenario, mine, opposition, rng)
    return evaluate(scenario, my_innings, their_innings), my_innings, their_innings


def bonuses_on_offer(scenario: Scenario) -> list[str]:
    """Which bonuses today's kind can actually award.

    Not all three are available on every day, and that is a consequence of the format
    rather than an oversight: a chase is one innings, so nobody bowls and there is no
    four-wicket haul to take; a defence is not a chase, so there is nothing to finish
    early. Listed so the page can state what is on offer rather than promising a bonus
    that cannot be earned today."""
    from game.scenarios import FINISHED_EARLY, FOUR_WICKET_HAUL, OPENER_CENTURY
    on = [OPENER_CENTURY]
    on.append(FOUR_WICKET_HAUL if scenario.kind == DEFEND_BY else FINISHED_EARLY)
    return on


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
        "opposition_wickets": opposition_wickets,
    })


def _scenario_from_row(kind: str, row: dict) -> Scenario:
    return Scenario(kind, row["opposition_fs_id"], row["opposition_name"], row["stage"],
                    target=row.get("target"),
                    wickets_required=row.get("wickets_required"),
                    runs_required=row.get("runs_required"))


def _generate_day(full: Deck, model: Model, challenge_date):
    """Today's scenario and deck, from the date alone.

    The opposition is drawn from the day's own deck rather than the whole archive, so the
    squads you are dealt and the side you are up against come from one coherent pool -- and
    it is redrawn until one can actually field a legal eleven, which `side_for_fs` decides
    and which is a property of the squad rather than a failure here."""
    rng = random.Random(daily_seed(challenge_date))
    deck_fs_ids = choose_deck(rng, full.fs_ids, DAILY_DECK_SIZE)
    for fs_id in rng.sample(deck_fs_ids, len(deck_fs_ids)):
        side = side_for_fs(full, fs_id)
        if side is None:
            continue
        posted = opposition_total(model, side, random.Random(daily_seed(challenge_date)))
        sc = generate(rng, fs_id, side.name, posted.runs)
        return Day(challenge_date, daily_seed(challenge_date), sc, deck_fs_ids,
                   posted.wickets), posted
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
        day, _posted = _generate_day(full, model, challenge_date)
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

    mine = Side(name="You", short="YOU", xi=list(session.order),
                impact=session.impact, you=True)
    opposition = (side_for_fs(full, day.scenario.opposition_fs_id)
                  if day.scenario.kind == DEFEND_BY else None)
    # Seeded from the day and the account, so the match is a property of the attempt rather
    # than of when it was submitted -- the same result comes back on a re-submission, and
    # nobody can re-roll a bad match by sending it again.
    outcome, _mine_inn, _their_inn = score_day(
        model, day.scenario, mine, opposition,
        random.Random(f"daily-match:{challenge_date}:{account_id}"))

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
