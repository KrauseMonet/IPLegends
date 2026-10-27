"""The auction told as a story [A232]: the paper, the squad table, the board's brief, the
season's verdict and the floor's whispers. Hand-built auctions, so each rule is tested where
it has to act; the API tests at the end drive a real auction."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

import game.auction as au
from etl.feasibility import Card
from game import auction_report as ar
from tools.snapshot_deck import deck_from, read_document

DOC = read_document()


def card(pid, display, **kw):
    return Card(fs_id=1, person_id=pid, name=pid.upper(), display=display, season_year=2015,
                franchise=kw.pop("franchise", "Mumbai Indians"), **kw)


def lot(i, c, base=100, code="BA1"):
    return au.Lot(i, c, code, base)


def sale(lt, winner, price, n_bids=3, rnd="main", rtm=None):
    bids = [au.Bid((winner + 1) % 10, price)] * (n_bids - 1) + [au.Bid(winner, price)]
    return au.Sale(lt, rnd, winner, price, bids if winner is not None else [], rtm)


def auction(sales, lots=None, seed=1):
    teams = au.make_teams(seed)
    lots = lots if lots is not None else [s.lot for s in sales]
    a = au.Auction(seed, lots, teams, sales=list(sales))
    for s in sales:
        if s.winner is not None:
            t = teams[s.winner]
            t.squad.append(s.lot.card)
            t.paid.append(s.price)
            t.purse -= s.price
    return a


# --- the market --------------------------------------------------------------------------------


def _market(prices, displays):
    lots_ = [lot(i, card(f"p{i}", d)) for i, d in enumerate(displays)]
    return auction([sale(l, i % 10, p) for i, (l, p) in enumerate(zip(lots_, prices))])


def test_the_fair_price_is_the_median_of_comparable_sales_excluding_itself():
    a = _market([100, 200, 300, 400, 500, 900, 5000],
                [85, 86, 87, 84, 83, 85, 99])          # the 99 is outside every band
    target = a.sales[5]                                # rated 85, sold for 900
    # comparable: 85 86 87 84 83 (within 2), not itself, not the 99 -> median of 100..500
    assert ar.fair_price(a, target) == 300


def test_too_few_comparable_sales_give_no_fair_price():
    a = _market([100, 200, 300, 400], [85, 86, 87, 84])
    assert ar.fair_price(a, a.sales[0]) is None        # three others, MARKET_MIN is five


def test_a_fill_round_sale_is_not_part_of_the_market():
    a = _market([100, 200, 300, 400, 500, 900], [85, 86, 87, 84, 83, 85])
    a.sales[1].round = "fill"                          # 200 no longer counts
    assert ar.fair_price(a, a.sales[5]) is None        # only four comparable left


def test_the_steal_is_the_cheapest_star_against_the_market_and_needs_a_real_bargain():
    disp = [88] * 8
    a = _market([1000, 1000, 1000, 1000, 1000, 1000, 500, 650], disp)
    s, fair = ar.steal_of(a)
    assert s is a.sales[6] and fair == 1000             # 0.5 of the going rate
    b = _market([1000] * 7 + [700], disp)               # 0.7: not cheap enough to call it
    assert ar.steal_of(b) is None
    c = _market([1000] * 7 + [300], [80] * 8)           # cheap, but not a player worth calling
    assert ar.steal_of(c) is None


def test_the_splurge_is_the_dearest_against_the_market():
    a = _market([100] * 6 + [180, 400], [80] * 8)
    s, fair = ar.splurge_of(a)
    assert s is a.sales[7] and fair == 100
    assert ar.splurge_of(_market([100] * 7 + [170], [80] * 8)) is None   # 1.7 < 1.75


# --- the paper ---------------------------------------------------------------------------------


def test_the_paper_leads_with_the_record_and_skips_what_did_not_happen():
    a = _market([100, 200, 300, 2475, 500, 600], [80, 81, 82, 90, 83, 84])
    clips = ar.morning_after(a)
    keys = [c.key for c in clips]
    assert keys[0] == "record" and "2475" not in clips[0].headline
    assert "₹24.75 cr" in clips[0].headline and a.sales[3].lot.card.name in clips[0].headline
    assert "war" not in keys and "rtm" not in keys and "yours" not in keys
    assert [c.weight for c in clips] == sorted([c.weight for c in clips], reverse=True)


def test_the_longest_war_is_news_only_when_it_is_long_and_not_the_record():
    lots_ = [lot(i, card(f"p{i}", 85)) for i in range(3)]
    a = auction([sale(lots_[0], 0, 900, n_bids=30), sale(lots_[1], 1, 300, n_bids=25),
                 sale(lots_[2], 2, 200, n_bids=2)])
    war = next(c for c in ar.morning_after(a) if c.key == "war")
    assert "25 bids" in " ".join(war.body)              # the record lot is not counted twice
    b = auction([sale(lots_[0], 0, 900, n_bids=30), sale(lots_[1], 1, 300, n_bids=19)])
    assert "war" not in [c.key for c in ar.morning_after(b)]


def test_your_signing_is_your_dearest_hammer_buy_and_a_fill_only_side_is_said_so():
    lots_ = [lot(i, card(f"p{i}", 80 + i)) for i in range(3)]
    a = auction([sale(lots_[0], 4, 300), sale(lots_[1], 4, 700), sale(lots_[2], 4, 30, rnd="fill")])
    yours = next(c for c in ar.morning_after(a, you=4) if c.key == "yours")
    assert "P1" in yours.headline and "₹7 cr" in " ".join(yours.body)
    b = auction([sale(lots_[2], 4, 30, rnd="fill")])
    yours = next(c for c in ar.morning_after(b, you=4) if c.key == "yours")
    assert "sit on their purse" in yours.headline


def test_the_unsold_clipping_says_how_often_the_lot_was_offered():
    lots_ = [lot(0, card("a", 80)), lot(1, card("star", 90))]
    once = auction([sale(lots_[0], 0, 200), au.Sale(lots_[1], "main", None, 0, [])], lots_)
    twice = auction([sale(lots_[0], 0, 200), au.Sale(lots_[1], "main", None, 0, []),
                     au.Sale(lots_[1], "accelerated", None, 0, [])], lots_)
    body = lambda a: " ".join(next(c for c in ar.morning_after(a) if c.key == "unsold").body)
    assert "twice" not in body(once) and "twice" in body(twice)


def test_no_player_is_given_a_pronoun_and_nobody_is_quoted():
    deck = deck_from(DOC)
    for seed in (3, 8):
        a = au.run_auction(deck, seed, human_short="KKR", mega=bool(seed % 2))
        for c in ar.morning_after(a, au.human_index(a)):
            words = " ".join([c.headline, c.dek] + c.body).lower().replace(",", " ").split()
            assert not set(words) & {"he", "his", "him", "she", "her"}, c
            assert c.quote == ""


# --- the squad table ---------------------------------------------------------------------------


def test_squads_are_ranked_by_their_best_twelve_and_described_from_ranks():
    deck = deck_from(DOC)
    a = au.run_auction(deck, 4)
    tw = {t.index: au.best_twelve(t.squad) for t in a.teams}
    rows = ar.squad_report(a, tw)
    assert [r.rank for r in rows] == list(range(1, 11))
    assert [r.rating for r in rows] == sorted([r.rating for r in rows], reverse=True)
    for r in rows:
        want = (sum(c.display for c in tw[r.index]) / 12)
        assert r.rating == round(want, 1)
        if r.batting_rank <= r.bowling_rank:
            want = ("The best batting in the auction" if r.batting_rank == 1
                    else f"{ar._ord(r.batting_rank)}-best batting")
            gap = f"{ar._ord(r.bowling_rank)} of 10 for bowling"
        else:
            want = ("The best attack in the auction" if r.bowling_rank == 1
                    else f"{ar._ord(r.bowling_rank)}-best attack")
            gap = f"{ar._ord(r.batting_rank)} of 10 for batting"
        assert (r.strength, r.gap) == (want, gap)
        t = a.teams[r.index]
        assert ("one wicketkeeper" in r.notes) == (sum(c.keeper_eligible for c in t.squad) <= 1)
        assert r.spent == sum(p for _, p in ar.bought(t))


def test_spent_leaves_out_retentions_and_a_legend():
    t = au.Team(0, "MI", "Mumbai Indians")
    leg = card("legend:x", 95)
    t.squad = [card("k", 90), card("b", 85), leg, card("c", 80)]
    t.paid = [1800, 500, 0, 300]
    t.retained, t.legend = 1, leg
    assert ar.spent(t) == 800 and ar.opening_purse(t) == au.PURSE - 1800


# --- the board's brief -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deck():
    return deck_from(DOC)


def test_the_brief_is_three_targets_one_of_each_kind_and_the_same_every_time(deck):
    for seed in range(12):
        for mega in (False, True):
            b = ar.board_brief(deck, seed, "RR", mega)
            assert [t.kind for t in b] == ["squad", "money", "season"]
            assert b == ar.board_brief(deck, seed, "RR", mega)
            if mega:     # retentions take the money and the legends before the table opens
                assert all(t.key not in ("spend", "star") for t in b)


def test_a_target_the_catalogue_cannot_supply_is_never_set(deck, monkeypatch):
    monkeypatch.setattr(ar, "AVAIL", {"former": 10**6, "keepers": 0, "spin": 10**6, "star": 10**6})
    for seed in range(15):
        assert ar.board_brief(deck, seed, "LSG", False)[0].key == "keepers"


def _team_with(buys, retained=()):
    t = au.make_teams(1)[3]
    for c, p in list(retained) + list(buys):
        t.squad.append(c)
        t.paid.append(p)
        t.purse -= p
    t.retained = len(retained)
    return t


def test_former_players_count_only_hammer_buys(deck):
    a = au.Auction(1, [], au.make_teams(1))
    t = a.teams[3]
    mine = [card(f"f{i}", 80, franchise=t.franchise) for i in range(3)]
    for i, c in enumerate(mine):
        t.squad.append(c); t.paid.append(100)
        a.sales.append(au.Sale(lot(i, c), "fill" if i else "main", 3, 100, [au.Bid(3, 100)]))
    st = ar.judge([ar.Target("former", "squad", "x")], a, t)[0]
    assert st.progress == "1 of 2" and st.state == "open"
    a.sales[1].round = "main"
    assert ar.judge([ar.Target("former", "squad", "x")], a, t)[0].state == "met"


def test_money_targets_are_missed_the_moment_they_cannot_be_met():
    a = au.Auction(1, [], au.make_teams(1))
    t = a.teams[2]
    c = card("a", 85)
    t.squad.append(c); t.paid.append(1600); t.purse = 400
    st = {s.key: s for s in ar.judge([ar.Target("reserve", "money", ""),
                                      ar.Target("top_price", "money", ""),
                                      ar.Target("spend", "money", "")], a, t)}
    assert st["reserve"].state == "missed"      # ₹4 cr left: no way back up
    assert st["top_price"].state == "missed"    # ₹16 cr on one player
    assert st["spend"].state == "open"          # still time to spend


def test_the_season_target_waits_for_the_finish_and_reads_it():
    a = au.Auction(1, [], au.make_teams(1))
    t = a.teams[0]
    play, top2 = ar.Target("playoffs", "season", ""), ar.Target("top2", "season", "")
    assert ar.judge([play], a, t)[0].state == "open"
    assert ar.judge([play, top2], a, t, finish=4)[0].state == "met"
    assert ar.judge([play, top2], a, t, finish=4)[1].state == "missed"
    assert ar.judge([play], a, t, finish=5)[0].state == "missed"


# --- the whispers ------------------------------------------------------------------------------


def test_whispers_fire_once_and_only_for_the_first_few_teams():
    lots_ = [lot(i, card(f"p{i}", 80, overseas=True)) for i in range(80)]
    sales = []
    for i, l in enumerate(lots_[:70]):          # every team buys seven overseas players
        sales.append(sale(l, i % 10, 1000))
    a = auction(sales, lots_)
    w = ar.whispers(a)
    lines = [x for xs in w.values() for x in xs]
    half = [x for x in lines if "half their purse" in x]
    full = [x for x in lines if "overseas places" in x]
    assert len(half) == ar.FIRSTS_TOLD and "first" in half[0] and "third" in half[2]
    assert len(full) == ar.FIRSTS_TOLD - 1
    assert len(lines) == len(set(lines))


def test_a_quiet_run_is_told_once_per_run_and_a_few_times_at_most():
    lots_ = [lot(i, card(f"p{i}", 80)) for i in range(40)]
    sales = []
    for i, l in enumerate(lots_):
        sold = i % 8 == 0                       # runs of seven unsold lots
        sales.append(sale(l, 0, 100) if sold else au.Sale(l, "main", None, 0, []))
    a = auction(sales, lots_)
    quiet = [x for xs in ar.whispers(a).values() for x in xs if "without a bid" in x]
    assert len(quiet) == ar.UNSOLD_RUNS_TOLD
    assert all(x.startswith(f"{ar.UNSOLD_RUN} lots") for x in quiet)


def test_a_new_longest_fight_is_news_only_after_the_opening_lots():
    lots_ = [lot(i, card(f"p{i}", 80)) for i in range(14)]
    sales = [sale(l, i % 10, 100, n_bids=16 if i in (2, 12) else 4) for i, l in enumerate(lots_)]
    sales[13] = sale(lots_[13], 3, 100, n_bids=40)
    w = ar.whispers(auction(sales, lots_))
    wars = {pos: x for pos, xs in w.items() for x in xs if "longest fight" in x}
    assert list(wars) == [13]                   # lot 2 opened the record, lot 12 only tied it


# --- the season's verdict ----------------------------------------------------------------------


def _bat(pid, runs, balls=None):
    return NS(player=NS(person_id=pid), runs=runs, balls=balls if balls is not None else runs,
              faced_any=True)


def _bowl(pid, wickets, balls=24, runs=30):
    return NS(player=NS(person_id=pid), wickets=wickets, balls=balls, runs=runs)


def test_the_verdict_finds_the_bargain_best_buy_and_flop_from_what_was_done():
    teams = au.make_teams(1)
    a = au.Auction(1, [], teams)
    you, them = teams[0], teams[1]
    yours = {"dear": (card("dear", 90), 2000), "mid": (card("mid", 85), 800),
             "cheap": (card("cheap", 80), 50), "kept": (card("kept", 92), 1800),
             "bench": (card("bench", 80), 1500)}
    you.squad = [yours["kept"][0], yours["dear"][0], yours["mid"][0], yours["cheap"][0],
                 yours["bench"][0]]
    you.paid = [1800, 2000, 800, 50, 1500]
    you.retained = 1
    rival = card("rival", 85)
    them.squad, them.paid = [rival], [40]
    side_y, side_t = object(), object()
    inn_y = NS(batting=[_bat("dear", 10), _bat("mid", 300), _bat("kept", 400)], bowling=[])
    inn_t = NS(batting=[_bat("rival", 20)],
               bowling=[])
    inn_y.bowling = [_bowl("rival", 1)]
    inn_t.bowling = [_bowl("cheap", 10)]
    r = NS(home=side_y, away=side_t, home_innings=inn_y, away_innings=inn_t, winner=side_y)
    v = ar.season_verdict(a, [r], {id(side_y): 0, id(side_t): 1}, 0, 3)
    by = {ln.person_id: ln for ln in v.yours}
    assert (by["cheap"].wickets, by["mid"].runs, by["rival"] if "rival" in by else None) == (10, 300, None)
    assert not by["bench"].played
    # the league's bargain: of the biggest contributors, the cheapest buy (rival at ₹40L)
    assert (v.bargain[0].person_id, v.bargain[1]) == ("rival", them.short)
    # your best buy: of your better half (kept 400, mid 300 -- cheap's 200 is third of
    # four), the most done per rupee. Without the half, cheap's ₹50L would win it.
    assert v.best_buy.person_id == "mid"
    # the flop: of your three dearest who played (dear, kept, mid), one in the bottom half
    assert v.flop.person_id == "dear"
    assert by["kept"].pom == 1                      # the winner's top score is the match's award
    assert v.finish == 3


def test_a_retained_or_legend_player_is_never_the_bargain():
    teams = au.make_teams(1)
    a = au.Auction(1, [], teams)
    t = teams[0]
    leg = card("legend:x", 95)
    t.squad = [card("kept", 90), leg, card("buy", 80)]
    t.paid, t.retained, t.legend = [30, 0, 900], 1, leg
    side = object()
    inn = NS(batting=[_bat("kept", 500), _bat("legend:x", 400), _bat("buy", 10)], bowling=[])
    r = NS(home=side, away=object(), home_innings=inn, away_innings=NS(batting=[], bowling=[]),
           winner=side)
    v = ar.season_verdict(a, [r], {id(side): 0}, 0, 1)
    assert v.bargain[0].person_id == "buy"


# --- the API -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as c:
        yield c


def _to_twelve(c, mega):
    d = c.post("/api/auction", json={"team": "KKR", "mega": mega, "seed": 11}).json()
    if mega:
        d = c.post(f"/api/auction/{d['state']}/retain", json={"picks": []}).json()
    early = c.get(f"/api/auction/{d['state']}/report")
    assert early.status_code == 409
    assert [b["kind"] for b in d["brief"]] == ["squad", "money", "season"]
    d = c.post(f"/api/auction/{d['state']}/pass", json={"scope": "all"}).json()
    while d["phase"] == "fill":
        d = c.post(f"/api/auction/{d['state']}/fill", json={"index": 0}).json()
    return d


def test_each_sale_carries_the_whispers_that_followed_it():
    from web.app import _recent
    lots_ = [lot(i, card(f"p{i}", 80)) for i in range(40)]
    a = auction([sale(l, i % 10, 100) for i, l in enumerate(lots_)], lots_)
    whispers = {3: ["three"], 20: ["twenty"], 39: ["last"]}
    out = _recent(NS(auction=a), whispers)
    start = len(a.sales) - 25
    assert len(out) == 25 and out[0].lot == a.sales[start].lot.index
    assert {o.lot: o.whispers for o in out if o.whispers} == {20: ["twenty"], 39: ["last"]}


def test_the_api_carries_the_brief_and_the_report(client):
    d = _to_twelve(client, False)
    assert d["phase"] == "twelve"
    assert all(b["state"] != "open" or b["kind"] == "season" for b in d["brief"])
    rep = client.get(f"/api/auction/{d['state']}/report").json()
    assert rep["paper"][0]["key"] == "record"
    assert [t["rank"] for t in rep["teams"]] == list(range(1, 11))
    assert sum(t["you"] for t in rep["teams"]) == 1


def test_the_verdict_is_for_a_finished_auction_season_only(client):
    d = _to_twelve(client, True)
    s = d["suggestion"]
    d = client.post(f"/api/auction/{d['state']}/twelve",
                    json={"order": s[:11], "impact": s[11]}).json()
    assert client.get(f"/api/season/{d['state']}/verdict").status_code == 409
    done = client.post(f"/api/season/{d['state']}/skip", json={"scope": "tournament"}).json()
    v = client.get(f"/api/season/{done['state']}/verdict").json()
    me = next(r for r in done["table"] if r["you"])
    assert v["finish"] == me["pos"]
    season = next(b for b in v["brief"] if b["kind"] == "season")
    assert season["state"] in ("met", "missed") and season["progress"].startswith("finished")
    assert len(v["yours"]) == 18
    draft = client.post("/api/draft?seed=3").json()["state"]
    assert client.get(f"/api/season/{draft}/verdict").status_code == 400


# --- rooms ------------------------------------------------------------------------------------


def test_a_room_seat_sees_its_own_brief_and_the_room_report_waits_for_full_squads(deck, monkeypatch):
    from contextlib import contextmanager

    import web.app as app_mod
    from fastapi import HTTPException
    from tests.test_room_auction import Clock, two_human_room
    from tests.test_rooms import FakeConn
    from web import room_auction as ra
    from web import rooms

    c = Clock()
    monkeypatch.setattr(ra.time, "time", c)
    monkeypatch.setattr(rooms.time, "time", c)
    conn = FakeConn()
    code, host, guest = two_human_room(conn, deck, c, a="MI", b="CSK")

    @contextmanager
    def fake_db():
        yield conn
    monkeypatch.setattr(app_mod, "_db", fake_db)
    monkeypatch.setitem(app_mod.STATE, "deck", deck)

    room = rooms._load_room(conn, code, lock=False)
    out = app_mod._room_auction_out(room, deck, host)
    want = ar.board_brief(deck, room.seed, "MI", False)
    assert [b.text for b in out.brief] == [t.text for t in want]
    other = app_mod._room_auction_out(room, deck, guest)
    assert [b.text for b in other.brief] == [t.text for t in ar.board_brief(deck, room.seed, "CSK", False)]
    with pytest.raises(HTTPException) as e:
        app_mod.room_auction_report(code, host)
    assert e.value.status_code == 409

    for _ in range(400):                       # host skips to the end; both fill
        rm = rooms._load_room(conn, code, lock=False)
        r = ra.replay(rm, deck)
        if r.phase == "bid":
            ra.submit(conn, code, deck, ra.pass_lot, rm.host_id, "all")
        elif r.phase == "fill":
            ra.submit(conn, code, deck, ra.fill, r.pid_of[r.fill_team], 0)
        elif r.phase.startswith("rtm"):
            ra.submit(conn, code, deck, ra.rtm, r.pid_of[r.rtm_team], False)
        else:
            break
    rep = app_mod.room_auction_report(code, guest)
    assert [t.rank for t in rep.teams] == list(range(1, 11))
    you = [t for t in rep.teams if t.you]
    assert len(you) == 1 and you[0].short == "CSK" and you[0].owner == "Ben"
    assert next(t for t in rep.teams if t.short == "MI").owner == "Asha"
    assert next(c for c in rep.paper if c.key == "yours").headline.startswith("CSK")
