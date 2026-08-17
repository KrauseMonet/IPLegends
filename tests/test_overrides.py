"""The override CSVs, and the merge that must never eat a human's work.

These files are the only place in the project where a value is entered by hand rather
than derived, so the generator that rewrites them is the one piece of code that can
destroy something it cannot recompute. Every test here is about that boundary: the
decision column is sacred, everything beside it is disposable, and no generator may
write a decision it has not proved.
"""

from __future__ import annotations

import csv

import pytest

from etl.derive_people import OVERRIDES, cricinfo_ids, merge_override

DECIDED = {
    "nationality.csv": "nationality",
    "keepers.csv": "is_keeper",
    "keepers_by_season.csv": "kept",
    "bowling_style.csv": "bowling_style",
}


def write(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def read(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


COLUMNS = ["person_id", "name", "catches", "kept"]


def test_a_hand_filled_decision_survives_a_rerun(tmp_path):
    path = tmp_path / "k.csv"
    write(path, COLUMNS, [{"person_id": "a", "name": "A", "catches": "3", "kept": "y"}])
    merge_override(
        path, COLUMNS,
        [{"person_id": "a", "name": "A", "catches": "9", "kept": ""}],
        decided="kept",
    )
    assert read(path)[0]["kept"] == "y"


def test_derived_evidence_is_refreshed_rather_than_preserved(tmp_path):
    """The reason merge_override was rewritten. A new signal is worthless if it can
    only reach rows that did not exist yet."""
    path = tmp_path / "k.csv"
    write(path, COLUMNS, [{"person_id": "a", "name": "A", "catches": "3", "kept": "y"}])
    merge_override(
        path, COLUMNS,
        [{"person_id": "a", "name": "A", "catches": "9", "kept": ""}],
        decided="kept",
    )
    assert read(path)[0]["catches"] == "9"


def test_a_row_that_is_no_longer_derived_is_kept_not_dropped(tmp_path):
    """It may carry a decision, and a generator able to delete one silently will."""
    path = tmp_path / "k.csv"
    write(path, COLUMNS, [{"person_id": "gone", "name": "G", "catches": "1", "kept": "y"}])
    kept, added, orphans = merge_override(
        path, COLUMNS,
        [{"person_id": "b", "name": "B", "catches": "2", "kept": ""}],
        decided="kept",
    )
    rows = read(path)
    assert (kept, added, orphans) == (0, 1, 1)
    assert [r["person_id"] for r in rows] == ["b", "gone"]
    assert rows[1]["kept"] == "y"


def test_rows_come_out_in_the_order_given(tmp_path):
    """The ranking is the deliverable. A file that reorders it is not one."""
    path = tmp_path / "k.csv"
    order = ["c", "a", "b"]
    merge_override(
        path, COLUMNS,
        [{"person_id": p, "name": p, "catches": "0", "kept": ""} for p in order],
        decided="kept",
    )
    assert [r["person_id"] for r in read(path)] == order


def test_a_new_column_reaches_rows_that_already_existed(tmp_path):
    path = tmp_path / "k.csv"
    write(path, ["person_id", "kept"], [{"person_id": "a", "kept": "y"}])
    merge_override(
        path, COLUMNS,
        [{"person_id": "a", "name": "A", "catches": "4", "kept": ""}],
        decided="kept",
    )
    row = read(path)[0]
    assert row["catches"] == "4" and row["kept"] == "y"


@pytest.mark.parametrize("filename,column", sorted(DECIDED.items()))
def test_every_row_carries_a_cricinfo_id(filename, column):
    """The claim these columns were added on: a reviewer identifies a player rather
    than searching for one. Initials-only names are ambiguous and some are shared."""
    rows = read(OVERRIDES / filename)
    assert rows, filename
    assert all(row["cricinfo_id"] for row in rows), filename


def test_a_column_the_generator_does_not_produce_is_never_destroyed(tmp_path):
    """A112. The column-wise twin of the orphan-row rule above.

    `bowling_style.csv` carries a `source` column recording HOW each hand-checked style
    was decided. No generator produces it, so it is absent from every caller's `columns`
    list - and the old code wrote `fieldnames=columns`, which erased it on the next run
    while faithfully preserving the decisions themselves. A file whose decisions survived
    but whose provenance silently did not is indistinguishable from one that was never
    checked, which is the single outcome this whole module exists to prevent.
    """
    path = tmp_path / "k.csv"
    write(
        path,
        COLUMNS + ["source"],
        [{"person_id": "a", "name": "A", "catches": "3", "kept": "y", "source": "web"}],
    )
    merge_override(
        path, COLUMNS,
        [{"person_id": "a", "name": "A", "catches": "9", "kept": ""}],
        decided="kept",
    )
    row = read(path)[0]
    assert row["source"] == "web", "provenance was destroyed"
    # The other two rules still hold alongside it, or the fix traded one for another.
    assert row["kept"] == "y" and row["catches"] == "9"


def test_a_carried_column_survives_on_a_row_that_is_no_longer_derived(tmp_path):
    """Orphan rows are built by a separate expression from merged ones, so the rule
    above is unprotected on exactly the rows most likely to hold hand-entered work."""
    path = tmp_path / "k.csv"
    write(
        path,
        COLUMNS + ["source"],
        [{"person_id": "gone", "name": "G", "catches": "1", "kept": "y", "source": "web"}],
    )
    merge_override(
        path, COLUMNS,
        [{"person_id": "b", "name": "B", "catches": "2", "kept": ""}],
        decided="kept",
    )
    rows = read(path)
    assert [r["person_id"] for r in rows] == ["b", "gone"]
    assert rows[1]["source"] == "web"
    assert rows[0]["source"] == "", "blank in, blank out - a new row invents no provenance"


def test_a_new_row_never_arrives_carrying_a_decision(tmp_path):
    """Blank in, blank out. The merge may copy a decision forward but never mint one."""
    path = tmp_path / "k.csv"
    merge_override(
        path, COLUMNS,
        [{"person_id": "a", "name": "A", "catches": "7", "kept": ""}],
        decided="kept",
    )
    assert read(path)[0]["kept"] == ""


# What each reader actually accepts. A value outside these sets is not a syntax error
# anywhere - it reads as a silent 'no', which is the one outcome the whole 'blank means
# undecided' convention exists to prevent. A typo would cost a keeper their slot and
# report nothing, so it is worth a test even though nothing has typed in these yet.
YES = ("y", "yes", "true", "1")
NO = ("n", "no", "false", "0")
VOCABULARY = {
    "kept": YES + NO,
    "is_keeper": YES + NO,
    "bowling_style": ("pace", "spin"),
}


@pytest.mark.parametrize("filename,column", sorted(DECIDED.items()))
def test_hand_filled_decisions_use_a_vocabulary_the_reader_understands(filename, column):
    allowed = VOCABULARY.get(column)
    if allowed is None:
        return  # nationality is free text, checked below
    bad = [
        (row["person_id"], row[column])
        for row in read(OVERRIDES / filename)
        if row[column].strip() and row[column].strip().lower() not in allowed
    ]
    assert not bad, f"{filename}: {bad[:5]} would read as 'no' rather than fail"


def test_no_nationality_is_a_composite_side():
    """A24's other half: 'ICC World XI' is not a nationality, and it is exactly what a
    reviewer copying from a cap list would write down."""
    from etl.derive_people import COMPOSITE_TEAMS

    bad = [
        row["person_id"] for row in read(OVERRIDES / "nationality.csv")
        if row["nationality"].strip() in COMPOSITE_TEAMS
    ]
    assert not bad, bad


def test_the_register_covers_the_people_we_have_to_ask_about():
    ids = cricinfo_ids()
    missing = set()
    for filename in DECIDED:
        missing |= {
            row["person_id"] for row in read(OVERRIDES / filename)
            if not ids.get(row["person_id"])
        }
    assert not missing, f"{len(missing)} person(s) absent from the Cricsheet register"
