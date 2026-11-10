"""A mid-auction room, as the two public endpoints would show it, built from the real engine
so the planner is tested against genuine catalogues and genuine prices. [A280]"""

from __future__ import annotations

import copy

import game.auction as au


def mid_auction(deck, seed: int = 7, n_sales: int = 40, mega: bool = True, me: str = "KKR"):
    """(room_json, sets_json, auction, my_retained_cards) after `n_sales` main-round sales."""
    from web.app import _catalogue_out

    # nobody is the human here: a team marked human never bids without a policy behind it,
    # and `me` is then simply a computer team whose state the planner is asked about
    a = au.run_auction(deck, seed, None, mega=mega)
    cut = copy.copy(a)
    cut.sales = [s for s in a.sales if s.round == "main"][:n_sales]
    nxt = a.lots[n_sales] if n_sales < len(a.lots) else None
    sets = _catalogue_out(cut, nxt, "bid").model_dump(mode="json")
    teams = []
    for t in a.teams:
        bought = [s for s in cut.sales if s.winner == t.index]
        kept = t.squad[:t.retained]
        teams.append({
            "short": t.short, "franchise": t.franchise, "human": False,
            "purse": au.PURSE - sum(t.paid[:t.retained]) - sum(s.price for s in bought),
            "players": t.retained + len(bought),
            "overseas": sum(c.overseas is True for c in kept)
            + sum(s.lot.card.overseas is True for s in bought),
            "retained": t.retained, "owner": "Me" if t.short == me else None})
    room = {"status": "auctioning", "game": "mega" if mega else "auction",
            "auction": {"mega": mega, "teams": teams}}
    mine = next(t for t in a.teams if t.short == me)
    return room, sets, a, mine.squad[:mine.retained]
