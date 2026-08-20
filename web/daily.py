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

import random

from etl.feasibility import Deck
from game.scenarios import DAILY_DECK_SIZE, choose_deck, daily_seed
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
