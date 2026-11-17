"""2027 mini auction, Phase 0: parsing the retained squads, resolving players, squad sums
and the two-source birth-date rule. No network and no database."""
import etl.mini2027 as M

PAGE = """
==Pre-auction==
{| class="wikitable plainrowheaders"
|+ Traded players
|-
| 23 June 2026
! scope="row" | {{sortname|Rishabh|Pant}}
| {{INRConvert|15|c|lk=on}} || [[Lucknow Super Giants]] || [[Delhi Capitals]]
|}

=== Released players ===
{| class="wikitable plainrowheaders"
|+ style="background:#x" | [[Chennai Super Kings|<span>Chennai Super Kings</span>]]
|-
| 1
! scope="row" | {{sortname|Devon|Conway}}
| {{cr|NZ}} || {{INRConvert|6.25|c|lk=on}}
|}

==Player retention==
=== Retention policy ===
{| class="wikitable plainrowheaders"
|+ style="background:#x" | [[Chennai Super Kings|<span style="color:x">Chennai Super Kings</span>]]
|-
! scope="col" | No.
! scope="col" | Player
|-
| 1
! scope="row" | {{sortname|Ruturaj|Gaikwad}}
| {{cr|IND}} || {{INRConvert|18|c|lk=on}}
|-
| 2
! scope="row" | [[Jack Edwards (cricketer, born 2000)|Jack Edwards]]
| {{cr|AUS}} || {{INRConvert|3|c|lk=on}}
|-
| 3
! scope="row" | {{sortname|Sarfaraz|Khan|dab=cricketer}}
| {{cr|IND}} || {{INRConvert|75|l|lk=on}}
|}
{| class="wikitable plainrowheaders"
|+ style="background:#x" | [[Rajasthan Royals|<span>Rajasthan Royals</span>]]
|-
| 1
! scope="row" |
{{sortname|Manimaran|Siddharth}}
| {{cr|IND}} || {{INRConvert|75|l|lk=on}}
|-
| 2
! scope="row" | {{sortname|Sam|Curran}}
| {{cr|ENG}} || {{INRConvert|2.4|c|lk=on}}
|}
{| class="wikitable plainrowheaders"
|+ style="background:#x" | [[Kolkata Knight Riders|<span>Kolkata Knight Riders</span>]]
|-
|}

==Auction==
"""


def test_salaries_read_crore_and_lakh():
    assert M.lakh("{{INRConvert|14.2|c|lk=on}}") == 1420
    assert M.lakh("{{INRConvert|75|l|lk=on}}") == 75
    assert M.lakh("{{INRConvert|2.4|c|lk=on}}") == 240
    assert M.lakh("no salary here") is None


def test_only_the_retention_tables_are_read_with_every_row_shape():
    rows = M.parse_retained(PAGE)
    got = [(r["short"], r["wikipedia"], r["overseas"], r["salary_lakh"]) for r in rows]
    assert got == [
        ("CSK", "Ruturaj Gaikwad", "no", 1800),
        ("CSK", "Jack Edwards (cricketer, born 2000)", "yes", 300),
        ("CSK", "Sarfaraz Khan (cricketer)", "no", 75),
        ("RR", "Manimaran Siddharth", "no", 75),      # the row header written over two lines
        ("RR", "Sam Curran", "yes", 240),
    ]
    # Tables above the section -- traded players, and a team's RELEASED players, whose
    # caption names a team just as a retention table's does -- must never count.
    assert not any(r["wikipedia"] in ("Rishabh Pant", "Devon Conway") for r in rows)


def test_a_player_resolves_by_article_before_name_and_never_on_an_ambiguous_name():
    full = [
        {"person_id": "p1", "full_name": "Sarfaraz Khan", "wikipedia": "Sarfaraz Khan (cricketer)"},
        {"person_id": "p2", "full_name": "Sarfaraz Khan", "wikipedia": "Sarfaraz Khan (other)"},
        {"person_id": "p3", "full_name": "Madhav Tiwari", "wikipedia": ""},
    ]
    by_title, by_name = M.name_index(full)
    assert M.resolve({"wikipedia": "Sarfaraz Khan (cricketer)", "name": "Sarfaraz Khan"}, by_title, by_name) == ("p1", "title")
    # Two people share the name: no guess.
    assert M.resolve({"wikipedia": "Nope", "name": "Sarfaraz Khan"}, by_title, by_name) == ("", "ambiguous")
    assert M.resolve({"wikipedia": "Madhav Tiwari", "name": "Madhav Tiwari"}, by_title, by_name) == ("p3", "name")
    assert M.resolve({"wikipedia": "Debut Player", "name": "Debut Player"}, by_title, by_name) == ("", "none")


def test_squad_sums_slots_and_purse():
    rows = [dict(short="CSK", overseas="no", salary_lakh=1800, person_id="a", name="A"),
            dict(short="CSK", overseas="yes", salary_lakh=300, person_id="", name="B"),
            dict(short="RR", overseas="yes", salary_lakh=None, person_id="c", name="C")]
    sq = M.squads(rows)
    csk, rr = sq["CSK"], sq["RR"]
    assert (csk.players, csk.overseas, csk.spent_lakh) == (2, 1, 2100)
    assert (csk.slots, csk.overseas_slots) == (23, 7)
    assert csk.unresolved == ["B"]
    assert rr.unpriced == 1                         # a salary we could not read is counted, not zeroed
    assert sq["KKR"].slots == 25


def _official(**teams):
    return {k: dict(short=k, purse_left_lakh=v[0], slots=v[1], overseas_slots=v[2]) for k, v in teams.items()}


def _sq(short, players, overseas, spent, unpriced=0):
    return M.Squad(short, players=players, overseas=overseas, spent_lakh=spent, unpriced=unpriced)


def test_our_places_are_checked_against_the_official_table():
    sq = {"CSK": _sq("CSK", 19, 6, 9995), "MI": _sq("MI", 16, 6, 0, unpriced=16)}
    ok, _ = M.reconcile(sq, _official(CSK=(2805, 6, 2), MI=(3260, 9, 2)))
    assert ok == []
    bad, _ = M.reconcile(sq, _official(CSK=(2805, 5, 2), MI=(3260, 9, 3)))
    assert any(p.startswith("CSK") and "places left" in p for p in bad)
    assert any(p.startswith("MI") and "overseas places" in p for p in bad)
    missing, _ = M.reconcile(sq, _official(CSK=(2805, 6, 2)))
    assert any(p.startswith("MI") and "no official" in p for p in missing)


def test_salaries_are_judged_by_the_purse_most_teams_imply_and_never_by_a_guess():
    def table(left):
        return {k: dict(short=k, purse_left_lakh=v, slots=24, overseas_slots=8) for k, v in left.items()}

    def squads(spent, unpriced=()):
        return {k: M.Squad(k, players=1, spent_lakh=v, unpriced=int(k in unpriced)) for k, v in spent.items()}

    # A, B and C all imply Rs 130 cr, D implies Rs 128 cr, E's salaries are unknown.
    problems, implied = M.reconcile(squads(dict(A=12000, B=11000, C=10000, D=12300, E=0), unpriced="E"),
                                    table(dict(A=1000, B=2000, C=3000, D=500, E=0)))
    assert implied == {"A": 13000, "B": 13000, "C": 13000, "D": 12800}
    salary = [p for p in problems if "salaries" in p]
    assert len(salary) == 1 and salary[0].startswith("D")
    # Three teams, three different totals: no majority, so no team is blamed.
    problems, _ = M.reconcile(squads(dict(A=12000, B=11000, C=9000)), table(dict(A=1000, B=1000, C=1000)))
    salary = [p for p in problems if "salaries" in p]
    assert len(salary) == 1 and "no majority" in salary[0]


def test_a_birth_date_is_kept_only_where_both_sources_agree():
    people = [{"person_id": "a", "full_name": "A"}, {"person_id": "b", "full_name": "B"},
              {"person_id": "c", "full_name": "C"}, {"person_id": "d", "full_name": "D"}]
    wd = {"a": ["1990-01-01"], "b": ["1988-02-16"], "c": ["1990-01-01", "1991-01-01"]}
    wp = {"a": "1990-01-01", "b": "1991-05-20", "c": "1990-01-01"}
    rows = {r["person_id"]: r for r in M.birth_rows(people, wd, wp)}
    assert rows["a"]["birth_date"] == "1990-01-01" and rows["a"]["agree"] == "yes"
    assert rows["b"]["birth_date"] == "" and rows["b"]["agree"] == "no"      # disagree: a human decides
    assert rows["c"]["birth_date"] == ""        # Wikidata holds two dates: not one source's answer
    assert rows["d"]["birth_date"] == ""        # no source at all


LIST = """# a comment
CSK|Ruturaj Gaikwad||
CSK|Jack Edwards|O|T
RCB|Virat Kohli||
RCB|Phil Salt|O|
RCB|Mohd. Siraj||
"""


def test_the_transcribed_list_reads_overseas_and_trade_marks_and_refuses_an_unknown_team():
    rows = M.parse_list(LIST)
    assert [(r["short"], r["name"], r["overseas"], r["traded"]) for r in rows] == [
        ("CSK", "Ruturaj Gaikwad", "no", ""), ("CSK", "Jack Edwards", "yes", "yes"),
        ("RCB", "Virat Kohli", "no", ""), ("RCB", "Phil Salt", "yes", ""), ("RCB", "Mohd. Siraj", "no", "")]
    try:
        M.parse_list("XYZ|Somebody||")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown team must be refused, not dropped")


def test_name_order_and_mohd_find_the_same_player_but_never_guess():
    full = [{"person_id": "v", "full_name": "Vijaykumar Vyshak", "wikipedia": ""},
            {"person_id": "s", "full_name": "Mohammed Siraj", "wikipedia": ""},
            {"person_id": "a", "full_name": "Ravi Kumar Singh", "wikipedia": ""},
            {"person_id": "b", "full_name": "Singh Ravi Kumar", "wikipedia": ""}]
    by_title, by_name = M.name_index(full)
    assert M.resolve({"name": "Vyshak Vijaykumar"}, by_title, by_name) == ("v", "name order")
    assert M.resolve({"name": "Mohd. Siraj"}, by_title, by_name) == ("s", "name")
    # An exact name still wins first; a reordering that fits two people is no guess.
    assert M.resolve({"name": "Singh Ravi Kumar"}, by_title, by_name)[0] == "b"
    assert M.resolve({"name": "Kumar Singh Ravi"}, by_title, by_name) == ("", "ambiguous")


def test_wikipedia_wins_where_it_is_complete_and_the_list_fills_the_rest():
    wiki = [dict(short="CSK", team="Chennai Super Kings", name="Ruturaj Gaikwad", person_id="r", overseas="no", salary_lakh=1800),
            dict(short="CSK", team="Chennai Super Kings", name="Jack Edwards", person_id="", overseas="yes", salary_lakh=300),
            dict(short="RCB", team="Royal Challengers Bengaluru", name="Phil Salt", person_id="p", overseas="yes", salary_lakh=1150)]
    listed = [dict(short="CSK", team="x", name="Ruturaj Gaikwad", person_id="r", overseas="no", salary_lakh=None),
              dict(short="CSK", team="x", name="Somebody Else", person_id="z", overseas="no", salary_lakh=None),
              dict(short="RCB", team="x", name="Virat Kohli", person_id="k", overseas="no", salary_lakh=None),
              dict(short="RCB", team="x", name="Phil Salt", person_id="p", overseas="yes", salary_lakh=None)]
    rows, notes = M.choose_rows(wiki, listed)
    csk = [r for r in rows if r["short"] == "CSK"]
    rcb = [r for r in rows if r["short"] == "RCB"]
    # CSK: Wikipedia lists as many, so Wikipedia's rows -- and the disagreement is reported.
    assert {r["source"] for r in csk} == {"wikipedia"} and len(csk) == 2
    assert any("Somebody Else" in n for n in notes) and any("Jack Edwards" in n for n in notes)
    # RCB: Wikipedia has only the traded player, so the list -- carrying his known salary.
    assert {r["source"] for r in rcb} == {"list"} and len(rcb) == 2
    assert next(r for r in rcb if r["name"] == "Phil Salt")["salary_lakh"] == 1150
    assert next(r for r in rcb if r["name"] == "Virat Kohli")["salary_lakh"] is None
    assert not any(n.startswith("RCB") for n in notes)       # a team Wikipedia has not finished is not a disagreement
