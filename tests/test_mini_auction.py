"""[A297] The 2027 mini auction: our provisional list, the projected 2027 card, the preset
built from the real retained squads, the engine's per-team limits and mini bidding, and the
`AM` session state. No network and no database: the list's league rules run on hand-built
matches, and everything else on hand-built cards or the committed snapshots."""
import math
import re
import statistics

import pytest

import etl.list2027 as L
import etl.mini2027 as M27
import game.auction as au
import game.mini2027 as M
from etl.feasibility import Card, Deck


# ---------------------------------------------------------------- the list's league rules

def _match(deliveries, date="2026-01-10", players=("A", "B", "C", "D")):
    reg = {p: p.lower() * 8 for p in players}
    return {"info": {"dates": [date], "registry": {"people": reg},
                     "players": {"X": list(players[:2]), "Y": list(players[2:])}},
            "innings": [{"overs": [{"deliveries": deliveries}]}]}


def _d(batter, bowler, bat=0, total=None, extras=None, wicket=None):
    d = {"batter": batter, "bowler": bowler, "non_striker": "B",
         "runs": {"batter": bat, "total": bat if total is None else total}}
    if extras:
        d["extras"] = extras
    if wicket:
        d["wickets"] = [{"player_out": wicket[0], "kind": wicket[1]}]
    return d


def test_a_wide_is_no_ball_faced_byes_are_not_charged_and_a_run_out_is_not_the_bowlers():
    lines = {}
    L.add_match(lines, _match([
        _d("A", "C", 4),
        _d("A", "C", 0, total=1, extras={"wides": 1}),
        _d("A", "C", 0, total=4, extras={"byes": 4}),
        _d("A", "C", 1, total=2, extras={"noballs": 1}),
        _d("A", "C", 0, wicket=("A", "run out")),
        _d("A", "C", 0, wicket=("A", "bowled")),
    ]), "bbl")
    a, c = lines["a" * 8], lines["c" * 8]
    assert (a.runs, a.balls, a.outs) == (5, 5, 2)      # the wide is not faced; the no-ball is
    assert c.legal == 4                                 # neither the wide nor the no-ball
    assert c.conceded == 4 + 1 + 0 + 2 + 0 + 0          # the byes are not his
    assert c.wickets == 1                               # the run out is not his
    assert a.leagues == {"bbl"} and a.matches == 1


def test_newcomers_need_a_known_eligible_passport_a_recent_cap_volume_and_no_ipl_record():
    good = L.Line(runs=300, balls=200, matches=10)
    lines = {"p1": good, "p2": good, "p3": good, "p4": good, "p5": good, "p6": good,
             "p7": L.Line(runs=60, balls=40, matches=3), "p8": L.Line(runs=200, balls=200, matches=10)}
    nat = {"p1": "Australia", "p2": "Pakistan", "p3": "India", "p5": "England", "p6": "England",
           "p7": "England", "p8": "England"}
    picks = L.pick_newcomers(lines, nat, ipl={"p5"}, taken=set(), n=10,
                             recent={"p1", "p2", "p3", "p4", "p5", "p7", "p8"})
    # p2 Pakistani, p3 Indian, p4 no passport, p5 has an IPL record, p6 no recent cap,
    # p7 too little cricket -- and p8 made his runs at par, so ranks below p1
    assert picks == ["p1", "p8"]


def test_newcomers_rank_on_impact_and_stop_at_n():
    lines = {"a": L.Line(runs=300, balls=200), "b": L.Line(runs=260, balls=200),
             "c": L.Line(runs=200, balls=200)}
    nat = dict.fromkeys(lines, "Australia")
    assert L.pick_newcomers(lines, nat, set(), set(), 2, set(lines)) == ["a", "b"]


def test_a_surname_link_needs_a_unique_match_and_drops_a_leading_md():
    pool = {"x1": "Shoriful Islam", "x2": "Tanvir Islam", "x3": "Taskin Ahmed", "x4": "Tanzim Ahmed"}
    assert L.surname_link("Md Shoriful Islam", pool) == "x1"
    assert L.surname_link("Shoriful Islam", pool) == "x1"
    assert L.surname_link("Taskin Ahmed", pool) == ""          # two T. Ahmeds: not settled
    assert L.surname_link("Nobody Else", pool) == ""
    assert L.name_key("Md Shoriful Islam") == L.name_key("Shoriful Islam")


def test_last_years_list_reads_capped_base_and_overseas():
    rows = L.parse_unsold("# a comment\nTim David|Batter|Y|2|O\nVansh Bedi|Wicketkeeper||0.3|\n")
    assert rows == [
        dict(name="Tim David", role="Batter", capped="yes", base_2026_lakh=200, overseas="yes"),
        dict(name="Vansh Bedi", role="Wicketkeeper", capped="no", base_2026_lakh=30, overseas="no")]


def test_the_retired_table_and_the_smat_tables_are_read_by_section():
    page = """{| class="wikitable"
|+ Retired players
|-
! scope="row" | {{sortname|Ajinkya|Rahane}}
|}
{| class="wikitable"
|+ Traded players
|-
! scope="row" | {{sortname|Rishabh|Pant}}
|}"""
    assert L.parse_retired(page) == {"Ajinkya Rahane"}
    smat = """=== Individual statistics ===
==== Most Runs ====
| style="text-align: left;" |[[Ishan Kishan]] <small>(x)</small>
==== Most Wickets ====
| style="text-align: left;" |Ashok Sharma <small>(x)</small>
| style="text-align: left;" |[[Ishan Kishan]] <small>(x)</small>
==== Most dismissals for a wicket-keeper ====
| style="text-align: left;" |[[Pragnay Reddy]] <small>(x)</small>
=== Team statistics ===
| style="text-align: left;" |[[Not Me]] <small>(x)</small>"""
    assert dict(L.smat_leaders(smat)) == {"Ishan Kishan": "Bowler", "Ashok Sharma": "Bowler",
                                          "Pragnay Reddy": "Wicketkeeper"}


def _smat_match(t1, t2, runs1="", wickets1="", runs2="", wickets2="", motm=""):
    return f"""{{{{Single-innings cricket match collapsible
| team1 = {{{{cr-IND|{t1}}}}}
| team2 = {{{{cr-IND|{t2}}}}}
| runs1 = {runs1}
| wickets1 = {wickets1}
| runs2 = {runs2}
| wickets2 = {wickets2}
| motm = {motm}
}}}}"""


def test_smat_form_counts_showings_from_the_match_summaries():
    """[A299] A showing: top-scoring with 40+ at 130+, leading the attack with 3+ at 8 an
    over or less, or Player of the Match. A bowler is credited to the side he bowled for."""
    page = "\n".join([
        _smat_match("mum", "del", runs1="[[Big Hitter|B Hitter]] 61 (30)",
                    wickets1="[[Seamer One]] 3/24 (4 overs)",       # bowled for Delhi
                    runs2="Slow Coach 45 (40)",                     # 112.5: too slow
                    wickets2="Leaky Quick 3/33 (4 overs)",          # 8.25: too dear
                    motm="[[Big Hitter|B Hitter]] <small>(Mumbai)</small>"),
        _smat_match("del", "kar", runs1="Slow Coach 40 (30)",       # exactly 40 at 133
                    wickets2="[[Seamer One]] 3/32 (4 overs)"),       # exactly 8.0: counts
        _smat_match("man", "miz", runs1="[[Big Hitter|B Hitter]] 99 (40)",   # Plate: skipped
                    motm="[[Big Hitter|B Hitter]] <small>(x)</small>"),
    ])
    f = L.smat_form(page)
    assert (f["B Hitter"].knocks, f["B Hitter"].poms, f["B Hitter"].showings) == (1, 1, 2)
    assert f["B Hitter"].title == "Big Hitter" and f["B Hitter"].team == "mum"
    assert (f["Seamer One"].hauls, f["Seamer One"].team, f["Seamer One"].wickets) == (2, "del", 6)
    assert f["Seamer One"].overs_balls == 48 and f["Seamer One"].conceded == 56
    assert (f["Slow Coach"].knocks, f["Slow Coach"].tops, f["Slow Coach"].runs) == (1, 2, 85)
    assert f["Leaky Quick"].hauls == 0 and f["Leaky Quick"].leads == 1
    assert f["Seamer One"].role() == "Bowler" and f["B Hitter"].role() == "Batter"
    assert f["Seamer One"].evidence() == ("SMAT 2025-26 for Delhi: led the attack twice, "
                                          "6 wkts at 7.00 an over")
    assert L._overs_to_balls("3.4") == 22


def _row(overseas, source="unsold", pid="", evidence="", base="30"):
    return dict(source=source, overseas=overseas, person_id=pid, evidence=evidence,
                base_2026_lakh=base)


def test_the_balance_gives_each_passport_the_same_lots_a_place():
    """[A299] 41 Indian places and 25 overseas: 10 overseas lots keep round(10 * 41/25) = 16
    Indian ones, and the weakest evidence goes first."""
    # each stronger row sits LAST on the register, so only its own rule can keep it
    rows = [_row("yes") for _ in range(10)]
    rows += [_row("no") for _ in range(16)]                                  # nothing to go on
    rows += [_row("no", base="75")]                                          # a higher base
    rows += [_row("no", evidence="SMAT 2025-26 for Goa: x")]                 # SMAT form
    rows += [_row("no", pid="p")]                                            # an IPL record
    rows += [_row("no", source="released", pid="r", base="")]                 # kept first
    L.balance(rows, 66, 25)
    assert L.balance_cut(rows, 66, 25) == 16
    indian = [r for r in rows if r["overseas"] == "no"]
    assert sum(r["balance"] != "out" for r in indian) == 16
    assert all(r["balance"] == "" for r in indian[-4:])
    # among equals, the later row on the register goes first
    plain = indian[:16]
    assert [r["balance"] for r in plain] == [""] * 12 + ["out"] * 4
    assert all(r["balance"] == "" for r in rows if r["overseas"] == "yes")


def test_a_hand_include_overrides_the_balance_either_way():
    assert M.listed({"balance": "out", "include": "y"})
    assert not M.listed({"balance": "", "include": "n"})
    assert not M.listed({"balance": "out", "include": ""})
    assert M.listed({"balance": "", "include": ""})


def test_a_hand_decision_follows_its_player_when_the_source_changes():
    key = ("short", "source", "name")
    rows = [dict(short="RR", source="wikipedia", name="Aman Rao", person_id="", person_id_checked="", note=""),
            dict(short="RR", source="wikipedia", name="Varun Chakravarthy", person_id="v1",
                 person_id_checked="", note=""),
            dict(short="RR", source="list", name="Rao Aman", person_id="", person_id_checked="debutant",
                 note="checked"),
            dict(short="RR", source="list", name="Varun Chakaravarthy", person_id="",
                 person_id_checked="v1", note=""),
            dict(short="RR", source="list", name="Somebody Gone", person_id="", person_id_checked="x9",
                 note="")]
    current = {("RR", "wikipedia", "Aman Rao"), ("RR", "wikipedia", "Varun Chakravarthy")}
    out, notes = M27.carry_decisions(rows, current, key, ["person_id_checked", "note"])
    by = {r["name"]: r for r in out}
    assert by["Aman Rao"]["person_id_checked"] == "debutant" and by["Aman Rao"]["note"] == "checked"
    assert by["Varun Chakravarthy"]["person_id_checked"] == "v1"       # matched on the id
    assert "Somebody Gone" in by and len(notes) == 1                    # kept, and named
    assert "Rao Aman" not in by and "Varun Chakaravarthy" not in by


# ---------------------------------------------------------------- the 2027 card

def _card(pid, year, display, bat_balls=100, bowl_balls=None, **kw):
    base = dict(fs_id=year, person_id=pid, name=pid, season_year=year, display=display,
                role="batter", positions=frozenset({1, 2, 3}), overseas=False,
                bat=0.0, bat_scoring=0.0, bat_dismissal=1.0, bat_balls=bat_balls, bat_runs=bat_balls)
    if bowl_balls:
        base.update(bowl=0.0, bowl_scoring=0.0, bowl_dismissal=1.0, bowl_balls=bowl_balls,
                    bowl_runs=bowl_balls, bowl_wickets=1)
    base.update(kw)
    return Card(**base)


def test_the_projection_is_a_decayed_volume_weighted_mean_shrunk_by_600_balls():
    hist = {2026: _card("a", 2026, 90, bat_balls=300), 2025: _card("a", 2025, 80, bat_balls=300)}
    w26, w25 = 301, 0.7 * 301
    want = (w26 * 90 + w25 * 80 + 600 * 78) / (w26 + w25 + 600)
    assert M.project_display(hist, 78) == pytest.approx(want)
    assert (M.DECAY_DISPLAY, M.K_DISPLAY) == (0.7, 600)


def test_an_engine_half_shrinks_toward_zero_and_a_multiplier_toward_one_on_the_log_scale():
    hist = {2026: _card("a", 2026, 80, bat_balls=300, bat_scoring=0.2, bat_dismissal=0.5)}
    assert M.project_half(hist, "bat_scoring", "bat_balls") == pytest.approx(0.2 * 300 / 600)
    assert M.project_half(hist, "bat_dismissal", "bat_balls", log=True) == pytest.approx(
        math.exp(math.log(0.5) * 300 / 600))
    assert M.project_half(hist, "bowl_scoring", "bowl_balls") is None
    assert (M.DECAY_HALF, M.K_HALF) == (0.7, 300)


def test_the_stretch_is_linear_onto_a_real_seasons_mean_and_spread_and_keeps_the_scale():
    raw = {"a": 79.0, "b": 80.0, "c": 81.0, "d": 90.0, "e": 70.0}
    out = M.stretch(raw, 80.0, 10.0)
    mu, sd = statistics.fmean(raw.values()), statistics.pstdev(raw.values())
    assert out["b"] == round(80 + (80 - mu) * 10 / sd)
    assert out["d"] == round(80 + (90 - mu) * 10 / sd) == 96
    wide = M.stretch(raw, 80.0, 20.0)
    assert wide["d"] == 99 and wide["e"] == 72                   # held to 72-99
    assert out["a"] < out["b"] < out["c"]                        # rank kept


def test_a_projected_card_keeps_the_last_real_box_score_and_drops_a_discipline_he_lacked():
    hist = {2025: _card("a", 2025, 80, bowl_balls=120),
            2026: _card("a", 2026, 85, bat_balls=200, bowl=None, bowl_scoring=None)}
    c = M.projected_card(hist, 88, "KKR")
    assert (c.display, c.season_year, c.franchise) == (88, 2027, "KKR")
    assert (c.bat_runs, c.bat_balls) == (200, 200)               # 2026's figures, untouched
    assert c.bowl is None and c.bowl_scoring is None             # no bowling last season


def _deck(cards):
    by: dict = {}
    for c in cards:
        by.setdefault(c.fs_id, []).append(c)
    return Deck(by, sorted(by))


def test_debut_pools_skip_the_founding_season_and_the_weaker_half_is_the_lower_half():
    cards = [_card("old", 2008, 99)] + [_card(f"d{i}", 2015, 70 + i) for i in range(10)]
    pools = M.pooled_debuts(_deck(cards))
    assert pools[("batter", None)].n == 10 and pools[("batter", None)].display == round(74.5)
    weaker = M.pooled_debuts(_deck(cards), weaker_half=True)
    assert weaker[("batter", None)].n == 5 and weaker[("batter", None)].display == 72


def test_a_passport_split_needs_twenty_debuts():
    pools = {("bowler", True): M.Pooled(80, frozenset(), None, None, None, None, None, None, None, None, 19),
             ("bowler", None): M.Pooled(77, frozenset(), None, None, None, None, None, None, None, None, 60)}
    assert M.pooled_for(pools, "bowler", True).display == 77
    pools[("bowler", True)] = M.Pooled(80, frozenset(), None, None, None, None, None, None, None, None, 20)
    assert M.pooled_for(pools, "bowler", True).display == 80


def test_the_preset_counts_places_from_the_official_table_and_lists_nobody_twice():
    cards = [_card("r1", 2026, 85), _card("r1", 2025, 80), _card("o1", 2026, 82, overseas=True),
             _card("x1", 2026, 79)] + [_card(f"d{i}", 2016, 75 + i % 5) for i in range(40)] + \
            [_card(f"b{i}", 2016, 74 + i % 5, bat_balls=10, bowl_balls=120, role="bowler")
             for i in range(40)]
    retained = [dict(short="KKR", team="Kolkata Knight Riders", name="R One", person_id="r1",
                     person_id_checked="", role_checked=""),
                dict(short="KKR", team="Kolkata Knight Riders", name="New Kid", person_id="",
                     person_id_checked="debutant", role_checked="Batter", overseas="no")]
    listed = [dict(source="released", name="O One", person_id="o1", capped="yes", overseas="yes",
                   role="Batter", base_2026_lakh="", include=""),
              dict(source="released", name="R One again", person_id="r1", capped="yes",
                   overseas="no", role="Batter", base_2026_lakh="", include=""),
              dict(source="unsold", name="Left Out", person_id="x1", capped="no", overseas="no",
                   role="Batter", base_2026_lakh="40", include="n"),
              dict(source="unsold", name="Unknown Lad", person_id="", capped="no", overseas="no",
                   role="Bowler", base_2026_lakh="30", include=""),
              dict(source="unsold", name="Old Hand", person_id="d0", capped="yes", overseas="no",
                   role="Batter", base_2026_lakh="50", include=""),
              dict(source="newcomer", name="League Star", person_id="", capped="no", overseas="yes",
                   role="Bowler", base_2026_lakh="", include="",
                   evidence="18 m (MLC, SAT); 9 runs off 14, SR 64; 38 wkts, econ 8.27; +352 runs above par"),
              dict(source="newcomer", name="D One Back", person_id="d1", capped="no", overseas="no",
                   role="Batter", base_2026_lakh="", include="",
                   evidence="SMAT 2025-26 for Goa: top-scored twice, 120 runs off 70"),
              dict(source="unsold", name="Form Lad", person_id="", capped="no", overseas="no",
                   role="Bowler", base_2026_lakh="30", include="",
                   evidence="SMAT 2025-26 for Assam: led the attack twice, 10 wkts at 5.00 an over"),
              dict(source="unsold", name="Cut Lad", person_id="", capped="no", overseas="no",
                   role="Batter", base_2026_lakh="30", include="", balance="out"),
              dict(source="unsold", name="Kept Lad", person_id="", capped="no", overseas="no",
                   role="Batter", base_2026_lakh="30", include="y", balance="out")]
    purse = [dict(short="KKR", purse_left_lakh="4165", slots="8", overseas_slots="3")]
    births = [dict(person_id="o1", birth_date="1990-12-16")]
    p = M.build_preset(_deck(cards), retained, listed, purse, births, au.base_price)
    assert [c.person_id for c in p.squads["KKR"]] == ["r1", "new:newkid"]
    assert p.squad_size["KKR"] == 2 + 8 and p.overseas_cap["KKR"] == 0 + 3
    # retained and 'n' left out; an IPL player's name is the deck's, never the list row's
    # the balance's cut leaves a row out unless a hand `include = y` keeps it [A299]
    assert [c.name for c, _, _ in p.lots] == ["o1", "Unknown Lad", "d0", "League Star", "d1",
                                              "Form Lad", "Kept Lad"]
    assert p.lots[1][1] == 30 and p.lots[0][1] == au.base_price(p.lots[0][0])
    assert p.ages["o1"] == pytest.approx(36.0, abs=0.01)
    assert p.debutants == {"new:newkid", "new:unknownlad", "new:leaguestar", "new:keptlad",
                           "new:formlad"}
    # [A298] where each stands going in, in place of a rating
    assert p.status == {"r1": "played", "new:newkid": "idle", "o1": "played",
                        "new:unknownlad": "unsold", "d0": "unsold", "new:leaguestar": "new",
                        "d1": "new", "new:keptlad": "unsold", "new:formlad": "unsold"}
    # an unsold player shows this season's SMAT figures, and never an IPL 'last card' [A299]
    assert p.notes["new:formlad"] == ("Unsold at the 2026 auction. Yet to play in the IPL. This "
                                      "season: SMAT 2025-26 for Assam: led the attack twice, 10 "
                                      "wkts at 5.00 an over.")
    assert M.form_words("last card 86") == ""
    # back on the list on this season's form, with an old IPL record: new, not idle [A299]
    assert p.notes["d1"] == ("New to the list this year. Last IPL season: 2016. This season: "
                             "SMAT 2025-26 for Goa: top-scored twice, 120 runs off 70.")
    assert p.notes["o1"] == "IPL 2026."
    assert p.notes["d0"] == "Unsold at the 2026 auction. Last IPL season: 2016."
    assert p.notes["new:unknownlad"] == "Unsold at the 2026 auction. Yet to play in the IPL."
    assert p.notes["new:newkid"].startswith("In a 2026 squad without playing a match.")
    # the evidence's runs above par is a rating by another name, and is dropped
    assert p.notes["new:leaguestar"] == ("New to the list this year. This season: 18 matches "
                                         "(MLC, SAT); 9 runs off 14, SR 64; 38 wkts, econ 8.27.")
    assert all("rat" not in n and "par" not in n for n in p.notes.values())


# ---------------------------------------------------------------- the engine

def test_a_team_reserves_money_only_for_the_places_it_must_fill():
    t = au.Team(0, "KKR", "Kolkata Knight Riders", purse=1000, squad_size=25, squad_min=18,
                squad=[_card(f"p{i}", 2026, 80) for i in range(17)])
    assert t.open_places == 8 and t.required_places == 1
    assert t.max_bid() == 1000                          # the one place owed is this one
    t.squad_min = None
    assert t.max_bid() == 1000 - au.MIN_PRICE * 7       # every place owed: the old rule


def test_the_overseas_limit_is_the_teams_own(monkeypatch):
    monkeypatch.setattr(au, "twelve_feasible", lambda squad, wild: True)   # the cap alone
    t = au.Team(0, "KKR", "Kolkata Knight Riders", overseas_cap=1,
                squad=[_card("p0", 2026, 80, overseas=True)] + [_card(f"p{i}", 2026, 80) for i in range(1, 13)])
    assert not t.may_buy(_card("o", 2026, 85, overseas=True))
    t.overseas_cap = 2
    assert t.may_buy(_card("o", 2026, 85, overseas=True))


def test_need_scales_with_the_squad_and_is_unchanged_at_eighteen():
    squad = [_card(f"p{i}", 2026, 80) for i in range(4)]   # four top-order batters
    batter = _card("x", 2026, 80)
    eighteen = au.Team(0, "KKR", "k", squad=list(squad))
    assert au.need(eighteen, batter) == pytest.approx(1.0)    # 4 = BAND_TARGET[top]: no premium
    twenty_five = au.Team(0, "KKR", "k", squad=list(squad), squad_size=25)
    assert au.need(twenty_five, batter) > 1.0                  # 25/18 of the targets: still short


def _market(**kw):
    base = dict(ages={}, debutants=frozenset(), per_place=500.0)
    base.update(kw)
    return au.Market(**base)


def _lot(card, base=30):
    return au.Lot(7, card, "BA1", base)


def test_an_unscouted_debutant_draws_no_bid_and_age_lowers_a_view():
    team = au.Team(1, "MI", "Mumbai Indians", purse=4000, squad_size=25, squad_min=18,
                   squad=[_card(f"p{i}", 2026, 80) for i in range(16)])
    young = _card("young", 2026, 88)
    seen = [s for s in range(200)
            if au.mini_ceiling(team, _lot(young), s, 0, _market(debutants=frozenset({"young"}))) > 0]
    # scouted (60%) and keen (half of those): about 30% of seeds
    assert 0.2 < len(seen) / 200 < 0.4
    # a card below the price cap, and high enough that seven years off still clears his base
    mid = _card("mid", 2026, 82)
    m = _market(ages={"mid": 37.0})
    old_view = [au.mini_ceiling(team, _lot(mid), s, 0, m) for s in range(50)]
    plain = [au.mini_ceiling(team, _lot(mid), s, 0, _market()) for s in range(50)]
    assert max(plain) < au.MINI_TOP
    assert all(v > 0 for v in old_view)
    assert sum(old_view) == pytest.approx(0.68 ** 7 * sum(plain), rel=0.02)


def _views(team, card, market):
    return [au.mini_ceiling(team, _lot(card), s, 0, market) for s in range(200)]


def test_a_capped_debutant_is_valued_as_a_known_player_not_at_the_debut_premium():
    """[A300] A capped debutant is an international: real minis paid about half a known
    overseas player's price for one, not the uncapped debutant's premium."""
    team = au.Team(1, "MI", "Mumbai Indians", purse=4000, squad_size=25, squad_min=18,
                   squad=[_card(f"p{i}", 2026, 80) for i in range(16)])
    deb = _card("deb", 2026, 80)        # high enough that a capped view clears his base
    known = [v for v in _views(team, deb, _market()) if v]
    uncapped = _views(team, deb, _market(debutants=frozenset({"deb"})))
    capped = _views(team, deb, _market(debutants=frozenset({"deb"}), capped=frozenset({"deb"})))
    # the same scouting draw decides who bids at all
    assert [v > 0 for v in uncapped] == [v > 0 for v in capped]
    bid = [i for i, v in enumerate(uncapped) if v]
    plain = _views(team, deb, _market())
    assert bid and known
    cap = min(au.MINI_TOP, team.max_bid())
    assert all(uncapped[i] == cap if 2.5 * plain[i] >= cap
               else uncapped[i] == pytest.approx(2.5 * plain[i], rel=0.01) for i in bid)
    assert all(capped[i] == pytest.approx(0.35 * plain[i], rel=0.01) for i in bid)


def test_an_overseas_player_is_worth_more_and_nobody_passes_the_record_price():
    """[A300] Same card, an overseas player is valued OVERSEAS_PREMIUM times an Indian, and no
    ceiling passes MINI_TOP, the highest price a real mini auction has paid."""
    team = au.Team(1, "MI", "Mumbai Indians", purse=9000, squad_size=25, squad_min=18,
                   squad=[_card(f"p{i}", 2026, 80) for i in range(16)])
    ind, os_ = _card("ind", 2026, 76), _card("os", 2026, 76, overseas=True)
    a, b = _views(team, ind, _market()), _views(team, os_, _market())
    assert all(y == pytest.approx(2.3 * x, rel=0.01) for x, y in zip(a, b) if 2.3 * x < 2400)
    star = _card("star", 2026, 95)
    assert max(_views(team, star, _market())) == au.MINI_TOP == 2520


def test_the_catalogue_runs_capped_sets_first_strongest_first():
    entries = [(_card(f"c{i}", 2026, 90 - i), 100, True) for i in range(15)] + \
              [(_card(f"u{i}", 2026, 80), 30, False) for i in range(3)]
    lots = au.mini_catalogue(entries, 1)
    codes = [l.set_code for l in lots]
    assert codes == ["BA1"] * 12 + ["BA2"] * 3 + ["UBA1"] * 3
    assert {l.card.person_id for l in lots[:12]} == {f"c{i}" for i in range(12)}


@pytest.fixture(scope="module")
def preset():
    from tools.snapshot_mini import load_preset
    p = load_preset()
    if p is None:
        pytest.skip("no 2027 preset snapshot")
    return p


def test_the_market_knows_who_is_capped(preset):
    """[A300] the capped-debutant rule only acts if the market carries the list's caps"""
    m = au.mini_market(preset)
    assert m.capped == {c.person_id for c, _, capped in preset.lots if capped}
    assert m.capped & m.debutants                  # capped debutants exist on the list


def test_a_real_mini_auction_keeps_every_rule(preset):
    for seed in (1, 2, 3):
        a = au.run_mini_auction(preset, seed)
        assert not a.stranded
        for t in a.teams:
            assert au.MINI_SQUAD_MIN <= len(t.squad) <= t.squad_size == preset.squad_size[t.short]
            assert t.overseas <= preset.overseas_cap[t.short]
            assert t.purse >= 0
            assert au.best_twelve(t.squad) is not None
        buys = [s for s in a.sales if s.winner is not None]
        assert len({s.lot.card.person_id for s in buys}) == len(buys)
    again = au.run_mini_auction(preset, 1)
    first = au.run_mini_auction(preset, 1)
    assert [(s.lot.index, s.winner, s.price) for s in again.sales] == \
           [(s.lot.index, s.winner, s.price) for s in first.sales]


def test_the_preset_snapshot_matches_its_sources_and_round_trips(preset):
    from tools import snapshot_mini as S
    built = S.build()
    assert S.encode(S.to_document(built)) == S.SNAPSHOT.read_bytes()
    assert S.encode(S.to_document(preset)) == S.SNAPSHOT.read_bytes()


# ---------------------------------------------------------------- the session

def test_an_AM_state_is_a_mini_auction_and_plays_to_its_twelve(preset):
    from tools.snapshot_deck import deck_from, read_document
    import web.auction_session as S
    deck = deck_from(read_document())
    state = S.new_state(4, "MI", mini=True)
    assert state.startswith("AM4-MI-") and S.is_mini(state) and not S.is_mega(state)
    r = S.pass_lots(deck, state, "all")
    while r.phase == "fill":
        r = S.fill(deck, r.state, 0)
    assert r.phase == "twelve" and r.mini and len(r.you.squad) >= au.MINI_SQUAD_MIN
    assert r.you.retained == len(preset.squads["MI"])
    assert S.decode(r.state)[:2] == (4, "MI")


def test_a_future_season_is_centred_like_the_latest_and_a_missing_past_one_still_fails():
    from game.__main__ import season_key
    table = {("batting", 2025): 0.1, ("batting", 2026): 0.2}
    assert season_key(table, "batting", 2027) == 0.2
    with pytest.raises(KeyError):
        season_key(table, "batting", 2024)


# ---------------------------------------------------------------- no ratings shown [A298]

def _walk(obj, path=""):
    """Every (path, key, value) in a JSON document."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def _no_ratings(doc):
    """Nothing that is a rating, or opens with one, anywhere in a response."""
    for path, k, v in _walk(doc):
        if k in ("rating", "overall_rating"):
            assert v is None, f"{path}.{k} = {v}"
        if k == "skills":
            assert v == [], f"{path}.skills"
        if k == "good":
            assert v == 0, f"{path}.good"
        if isinstance(v, str):
            assert not re.search(r"\brated\b", v), f"{path}.{k}: {v}"


def _cards(doc):
    return [v for _, k, v in _walk(doc) if isinstance(v, dict) and "person_id" in v
            and "kind" in v and "rating" in v]


@pytest.fixture(scope="module")
def client(preset):
    from fastapi.testclient import TestClient
    from web.app import app
    with TestClient(app) as c:
        yield c


def test_a_mini_auction_shows_no_rating_anywhere_from_the_floor_to_the_season(client, preset):
    post = lambda url, body: client.post(url, json=body)
    d = post("/api/auction", {"team": "MI", "mini": True, "seed": 4}).json()
    assert d["mini"] and d["ratings_hidden"] and d["phase"] == "bid"
    _no_ratings(d)
    seen = _cards(d)
    sets = client.get(f"/api/auction/{d['state']}/sets").json()
    assert sets["ratings_hidden"]
    _no_ratings(sets)
    rows = [x for st in sets["sets"] for x in st["lots"]]
    assert {x["history"] for x in rows} == {"played", "unsold", "new"}
    d = post(f"/api/auction/{d['state']}/pass", {"scope": "all"}).json()
    while d["phase"] == "fill":
        _no_ratings(d)
        seen += _cards(d)
        d = post(f"/api/auction/{d['state']}/fill", {"index": 0}).json()
    assert d["phase"] == "twelve"
    _no_ratings(d)
    seen += _cards(d)
    rep = client.get(f"/api/auction/{d['state']}/report").json()
    _no_ratings(rep)
    assert rep["teams"] == [] and rep["brief"] == [] and rep["paper"]
    assert not any(c["key"] in ("steal", "splurge") for c in rep["paper"])
    order, impact = d["suggestion"][:11], d["suggestion"][11]
    d = post(f"/api/auction/{d['state']}/twelve", {"order": order, "impact": impact}).json()
    s = post(f"/api/season/{d['state']}/skip", {"scope": "tournament"}).json()
    assert s["complete"] and s["overall_rating"] is None
    _no_ratings(s)
    v = client.get(f"/api/season/{s['state']}/verdict").json()
    _no_ratings(v)
    assert v["brief"] == []
    # every card carries its status, and figures only for a 2026 season
    stats = ("bat_runs", "bat_balls", "bat_strike_rate", "bowl_wickets", "bowl_runs",
             "bowl_balls", "bowl_economy")
    assert seen and all(c["status"] == preset.status[c["person_id"]] for c in seen)
    for c in seen:
        if c["status"] != "played":
            assert all(c[k] is None for k in stats), c["name"]
    played = [c for c in seen if c["status"] == "played"]
    assert played and any(c["bat_balls"] or c["bowl_balls"] for c in played)


def test_an_ordinary_auction_still_shows_its_ratings(client):
    """The hiding is the mini auction's alone: a mega auction's cards keep their numbers."""
    d = client.post("/api/auction", json={"team": "MI", "mega": False, "seed": 4}).json()
    assert not d["ratings_hidden"] and d["lot"]["card"]["rating"] is not None
    assert d["lot"]["card"]["status"] is None
    sets = client.get(f"/api/auction/{d['state']}/sets").json()
    assert all(x["rating"] is not None and x["history"] is None
               for st in sets["sets"] for x in st["lots"])


def test_the_paper_without_ratings_quotes_none_and_drops_the_rating_judged_stories(preset):
    # Run on an open auction: a mini auction's list never produces a steal, so the rule
    # that drops one would have nothing to act on there.
    import game.auction_report as R
    from tools.snapshot_deck import deck_from, read_document
    # seed 2: the unsold lot with the highest base is not the unsold lot with the best card
    a = au.run_auction(deck_from(read_document()), 2)
    full = R.morning_after(a, 0)
    bare = R.morning_after(a, 0, ratings=False)
    assert {"record", "steal", "splurge", "yours", "unsold"} <= {c.key for c in full}
    assert any(re.search(r"\brated\b", " ".join(c.body)) for c in full)
    assert not any(re.search(r"\brated\b", " ".join(c.body)) for c in bare)
    assert {c.key for c in bare} == {c.key for c in full} - {"steal", "splurge"}
    # the biggest name nobody bought is the one with the highest base, not the best card
    unsold = next(c for c in bare if c.key == "unsold")
    sold = {s.lot.index for s in a.sales if s.winner is not None and s.round != "fill"}
    left = [l for l in a.lots if l.index not in sold]
    top_base = max(l.base for l in left)
    assert unsold.body[0].startswith(max((l for l in left if l.base == top_base),
                                         key=lambda l: -l.index).card.name)
