# Working notes

Read `SPEC.md` first. This file tracks state, commands, and open questions.

**`SPEC.md` is the source of truth and this is not a formality.** Four figures recorded
there as measured fact turned out wrong once code actually read the archive: the reduced-
match split (15/19, really 15/12/7), the Impact Player replacement count (523, really 524
deliveries carrying 563 entries), the state-model fitting size (~145,000, really
149,697), and Kohli's 2016 balls faced (637, really 640 — see A22). Each was corrected in
the spec the moment it was found, with the old value named so the correction is auditable
rather than silent. Keep doing that — a figure quietly replaced is indistinguishable from a
figure that was never checked.

The fourth is the worst of the four and worth studying. It was reported here as *verified
against the public record* when only the runs had actually been checked; the balls figure
was asserted to match and did not. Every internal check passed, because the error was a
misreading of cricket's own scoring convention and the database was perfectly consistent
with it. **Only check 7 — a human reading a scorecard — could catch it.** When claiming a
figure matches an external source, check the figure being claimed, not a neighbouring one.

## Environment

Python 3.11 pinned via `uv`. No Homebrew, no Docker, no local Postgres. Database is
Neon (managed), so no Postgres extension may be used.

`.env` needs **both** Neon endpoints. `DATABASE_URL` is pooled (application);
`DIRECT_URL` is unpooled and is what migrations and every bulk load use, because
PgBouncer transaction mode breaks DDL and `COPY FROM STDIN`.

```bash
export PATH="$HOME/.local/bin:$PATH"
uv sync                                   # install deps
cp .env.example .env                      # then fill in BOTH endpoints
uv run python -m etl.download             # resolve + fetch archives, write manifest
uv run python -m etl.inspect_teams        # deck shape + archive audit
uv run python -m etl.migrate --status     # show applied vs pending
uv run python -m etl.migrate              # apply pending migrations
uv run python -m etl.load                 # load season 2016 (the default)
uv run python -m etl.load --season 2008 --season 2020
uv run python -m etl.load --all           # the whole archive
```

Reloading a season is safe. `etl.load` deletes the target matches first and
`deliveries`/`appearances` cascade from them, so a reload replaces rather than
duplicates. `people` is upserted, since a person spans seasons.

Parse-check migrations without a server (catches syntax errors before they hit Neon):

```bash
uv run --with pglast python -c "
import pglast, pathlib
for p in sorted(pathlib.Path('migrations').glob('*.sql')):
    pglast.parse_sql(p.read_text()); print('OK', p.name)"
```

## Hard rules

- **Bulk load with `COPY FROM STDIN`, never row-by-row inserts.** This is the only reason
  a remote database is viable for `deliveries`.
- Never key a player on a name string. Always `person_id` from `info.registry.people`.
- Never use `info.season` as the season year. Derive from match dates.
- Never fabricate a value. NULL, log it, report it.
- **Where the source carries a field we can derive independently, derive it and validate
  against the source rather than storing both.** [A19] Two copies of the same fact drift,
  and the storage is the lesser cost — what is really lost is the check. Deriving turns a
  redundant column into a test of our reading against the source's own answer, which is
  the only kind of check that cannot pass by agreeing with itself. `actual_delivery` is
  the worked example: not stored, and validation 15 is stronger for it.
- **Balls faced is `extra_wides = 0`, not `legal_ball`.** [A22] A no-ball is a ball faced;
  a wide is not. `legal_ball` is for overs bowled and the bowling economy denominator only.
  The two denominators are different predicates and sharing them inflates batting ratings.
- **A null must propagate as unknown.** Never let it acquire a default downstream. Prefer
  filters that test for the known value (`scheduled_balls = 120`) over filters that test
  for the absence of a problem (`not was_reduced`), because a null fails the first by
  construction and slips through the second.
- **Never default an unobserved value to the majority class, anywhere a gap in the source
  can masquerade as a real value.** [A23] This is the general form of the rule above and
  it applies to every derived field, not just the one that broke. A majority default is
  the single hardest error for internal checks to catch: it is plausible, it is right most
  of the time, and where the source is blind it is wrong silently and in a body rather
  than one row at a time. §6.1 defaulted unobserved nationality to Indian on the perfectly
  sound reasoning that uncapped IPL players are overwhelmingly domestic — and the archives
  turn out to contain no Afghanistan at all, so the rule minted eight confidently false
  records including an `is_overseas = false` that would have let a legal XI field five
  overseas players while the check reported compliance. **If a field can be unobserved, it
  must be able to be NULL.**
- No LLM or AI API call anywhere in this codebase.
- **A migration is immutable the moment it has been applied to any database, including a
  local or personal one. Until then it is a draft and may be edited freely.** The runner
  checksums applied files and refuses drift. `003` was edited in place on 2026-07-29
  (`innings_scheduled_overs` -> `innings_scheduled_balls`) under a one-time exception,
  granted only because no database existed yet. This is not a precedent. The rule was then
  honoured for real: A22 found a wrong column comment in the applied `003` and it was
  corrected in a new `005` rather than edited in place.

## Deck shape (measured from the archive, not estimated)

Regenerate with `uv run python -m etl.inspect_teams`; also persisted to
`data/manifest.json` under `ipl_archive_audit`.

| | |
|---|---|
| Seasons | **19** (2008–2026) |
| Matches | **1,243** |
| Team-name strings | 19 |
| Franchises | **15** |
| **Franchise-seasons (deck size)** | **166** |

Every deck decision depends on the 166. Franchises by season count: Delhi, Kolkata,
Mumbai, Punjab and Bengaluru 19 each; Chennai and Rajasthan 17 (2016–17 suspension);
Hyderabad 14; Deccan, Gujarat Titans and Lucknow 5; Pune Warriors 3; Gujarat Lions and
Rising Pune 2; **Kochi Tuskers Kerala 1**.

Deck sampling is **uniform over franchise-seasons**, so Mumbai appears 19x and Kochi 1x.
Intended — rarity should feel like an event. Must be a named constant in the dealer.
Never weight by franchise.

## Archive anomalies (permanent notes)

**Season labels.** `info.season` is unusable. Three observed anomalies: 2008 says
`2007/08`, **2010 says `2009/10`**, 2020 says `2020/21`. The 2010 case was not in the
original brief. The trap is the asymmetry — 2009, played in South Africa where a split
label would be forgivable, is labelled plainly `2009`, while 2010 is not. No pattern to
exploit; always derive from match dates.

**Missing matches.** Abandoned-without-a-ball matches are absent from the archive
entirely, because no deliveries means no file. Ten interior gaps across seven seasons:

| Season | Missing match numbers |
|---|---|
| 2008 | 47 |
| 2009 | 7, 13 |
| 2011 | 20 |
| 2012 | 32, 34 |
| 2015 | 25 |
| 2017 | 29 |
| 2024 | 63, 66 (**and 70**, see below) |

Proved by contrast, not assumed: 2011 #68 *is* present carrying `"result": "no result"`,
because play did occur before abandonment. So absence means zero balls, not missing data.

**The detector only sees interior gaps.** A match missing from the tail of a schedule is
invisible, because the highest number observable is the highest one present. 2024 is
short three league matches but only #63 and #66 are detectable; #70 cannot be inferred
without knowing the intended schedule size, which is not in the data. The audit therefore
also records `highest_number_seen` and `numbered_matches_present` so the shortfall is at
least visible. Do not add a hardcoded expected-schedule table to close this — it would
rot, and the consequence is nil since a zero-ball match contributes no rows anyway.

Recorded per season in `data/manifest.json` under `ipl_archive_audit.match_numbering`,
keyed to the archive checksum, so a future gap can be told apart from a download failure.

**`info.overs` never varies.** It is `20` in all 1,243 matches. The brief's §4.5 premise
that it drops for rain-reduced games is **false for this archive**. Reduction is visible
only via:

- `innings.target.overs` — the *chasing* innings' revised length (34 matches have < 20)
- innings 1 ending before 20 overs with fewer than 10 wickets down — curtailed mid-innings

**[Corrected 2026-07-30 — the earlier 15/19 split was wrong.]** Of the 34 reduced matches,
**15** had innings 1 equal to `target.overs`, **12** had it at full length with only the
chase cut, and **7** had innings 1 curtailed at a third length written down nowhere in the
file. The rule that resolves this is SPEC §4.5/A17.

The load-bearing part of that rule: when the chase was scheduled for a full twenty, innings
1 was too, and ended on wickets. Without that clause 44 innings go null instead of 6,
because an ordinary batting collapse in the 19th over looks exactly like rain from inside
a single innings. Six innings remain genuinely unknowable and are logged by name:
`1136566`, `1136592`, `980989` (shortened, abandoned mid-over) and `1473495`, `1527685`,
`501265` (abandoned with no second innings).

Count **overs, not legal balls**, when reading innings-1 length — otherwise the two 5-ball
miscounted overs below read as 119-ball curtailments.

**`target.overs` can be fractional.** Match `392186.json` carries `9.2` — nine overs and
two balls. Any integer overs column cannot represent it; scheduled length must be stored
in **balls**.

**`miscounted_overs`.** Cricsheet flags eight overs across the archive where the umpire
miscounted, giving 5 or 7 legal balls instead of 6. Validation check 3 must whitelist
these rather than treat them as parser bugs — they are real, and the source declares them:

`1136564` inn2 ov11 (7) · `335987` inn1 ov7 (5) · `335994` inn2 ov10 (7) ·
`336015` inn2 ov14 (5) · `392198` inn2 ov10 (7) · `419155` inn1 ov18 (7) ·
`501202` inn1 ov5 (5) · `501255` inn2 ov9 (5)

**`replacements`** appears on 585 deliveries in two kinds: `match` is the Impact Player,
`role` is a substitute taking over a fielding or bowling role. Both feed the A4
batting-position rule. **[Counts corrected 2026-07-30]** `match` is on **524 deliveries**
carrying **563 entries** — some deliveries record two at once — not 523. `role` is 61.
`match` entries always name a `team`; `role` entries never do and never need to.

**`actual_delivery`** is on every delivery and is the *legal-ball* scorecard reference, so
it repeats after a wide (11,075 duplicates). Not stored — verified exactly derivable from
`legal_ball` on all 295,732 deliveries, which makes it a free check on our own wide and
no-ball classification against the source's answer. Validation check 15.

**Powerplay bounds are positional, not legal-ball.** `powerplays[].to` reaches `5.9`,
which no legal-ball index can produce. Compare against `ball_no`.

**Super overs.** 16 matches, of which `1216517` (Mumbai v Kings XI, 2020) went to a
*second* super over and holds six innings. Ties and eliminators coincide exactly: all 16
ties carry `outcome.eliminator`, nothing else does, and a tie carries no `winner` key.

**At most one wicket per delivery** across all 295,732 — the single `wicket_kind` /
`player_out_id` pair on `deliveries` is safe and does not need a child table.

**`outcome.method`.** Present on 23 matches, always `'D/L'`, never anything else. All 23
sit inside the 34 reduced matches, so D/L is a strict subset of reduction and not a
separate exclusion criterion. But a D/L result carries an ordinary margin — 13 by runs,
10 by wickets — so it is invisible in `result_type` and would have gone unrecorded without
`matches.decided_by`.

**2026 revision risk.** Cricsheet is contributor-driven and recent seasons get revised.
Re-verify the 2026 files against a fresh download before finalising ratings. If the 2026
baseline sits far off trend after within-season normalisation, flag it rather than
absorbing it silently.

## Storage budget (Neon free tier)

Free tier is **0.5 GB logical data per project**; instant-restore history is metered
separately with a 1 GB-month allowance, so reload churn during development does not eat
the 0.5 GB.

| | |
|---|---|
| Matches (2008–2026), measured | **1,243** |
| Deliveries per match | ~250 (240 legal + extras) |
| **Total delivery rows** | **~310k** |
| `deliveries` heap | ~40 MB |
| 5 indexes on `deliveries` | ~50 MB |
| Everything else | <10 MB |
| **Total, estimated** | ~100 MB against a 500 MB cap |
| **Total, MEASURED after the full load** | **70 MB** (deliveries 65 MB, appearances 3.8 MB, rest <1 MB) |
| **Total, MEASURED after SPEC 6 and migration 006** | **69 MB** (deliveries 63 MB after the phase index went, appearances 3.9 MB, squad_members 520 kB) |

Roughly 7x headroom, so the estimate was conservative rather than wrong. The brief's "1.2 million deliveries" figure is ~4x high. The exact
count, from parsing the whole archive, is **295,732 deliveries across 1,243 matches and
28,106 appearances** — not an estimate.

## Decisions log

| # | Decision |
|---|---|
| A1 | Extras split into 5 smallint columns. Byes/legbyes are not charged to the bowler, so one column makes economy wrong. |
| A2 | Cohort offsets pooled across seasons, not per-cell z-scoring. Validation 10/11 replaced with predictive under/over-shrinkage tests; new check 13 for cohort era-drift. |
| A3 | Nationality from Test + ODI + T20I archives combined. Unknown list gets full review, sorted by matches played desc. **Superseded in part by A23 — the India default is gone.** |
| A4 | Batting position scans both `batter` and `non_striker`. Retired hurt/out, concussion subs and Impact Player entrants keep their original position. |
| A5 | Display phase: death = final 25% of *that innings'* scheduled overs. State model is separate: exact over x wickets bucketed 0-1/2-3/4-5/6+, fitted on full-length innings only, reduced innings mapped by balls remaining. |
| A6 | Slot template revised. Keeper is orthogonal to batting band. Bands from 6.4 are canonical. |
| A7 | Career shrinkage prior computed leave-one-season-out. `appearances` splits `named_in_squad` from derived `participated`. |
| A8 | Pace/spin slots collapse to a generic bowler slot of 5 until `bowling_style.csv` is filled. That CSV ships right after the full archive load. |
| A9 | Scope is 2008-2026, 19 seasons. Re-verify 2026 against a fresh download before finalising ratings; flag an off-trend 2026 baseline. |
| A10 | Deck sampling uniform over the 166 franchise-seasons, as an explicit named constant. Never weight by franchise. |
| A11 | Franchise-reroll availability queried from data at deal time, never hardcoded. |
| A12 | Season-label and missing-match anomalies recorded permanently; gaps persisted to the manifest keyed to the archive checksum. |
| A13 | `info.overs` is a constant carrying no information. Scheduled length comes from `target.overs` and innings-1 truncation, stored in BALLS (9.2 overs -> 56). |
| A14 | Miscounted overs taken from the source flag, whitelisted in validation, dropped from the 7.1 fit. |
| A15 | State table fitted on FIRST innings only, reduced matches and miscounted overs excluded. Reduced-match deliveries excluded from rating but kept in display stats. Second-innings table built only if validation 14 fires. |
| A16 | `replacements` kinds: `match` = Impact Player, `role` = substitute. A substitute who bowled counts as participation; a named-but-unused Impact Player does not. |
| A17 | Innings-1 scheduled length resolved by a six-case rule needing match-level context, not innings-level. Null for exactly 6 innings, each logged. Corrects A13's 15/19 split to 15/12/7. |
| A18 | A tie is stored as `result_type = 'tie'` with `winner_fs_id` set to the eliminator. Match won, match tied, both recorded. One match holds two super overs (six innings). |
| A19 | `actual_delivery` not stored — exactly derivable, so it serves as an independent check on wide/no-ball classification instead (validation 15). Powerplay bounds are positional, compare against `ball_no`. Generalised into a hard rule above. |
| A20 | `matches.decided_by` (runs / wickets / eliminator / dls / null) makes the result row self-describing. Takes `dls` over the margin type, since the margin type is already in `result_type` and D/L is recorded nowhere else. |
| A21 | §7.1 fitting filter tests `innings_scheduled_balls = 120`, not `not was_reduced`. The latter leaked 148 deliveries from 3 matches abandoned in innings 1 with no chase to mark them reduced. |
| A22 | Balls faced = `extra_wides = 0`. A no-ball is a ball faced, a wide is not, so `legal_ball` is the wrong predicate and undercounts. Corrects Kohli 2016 from 637 balls to 640. No new column (A19); migration `005` fixes the wrong comment in the immutable `003`. Pinned by validation check 17. |
| A23 | **Corrects A3.** Unknown nationality is NULL, never a default to India. The three international archives contain **no Afghanistan team at all**, so the default was systematically wrong for a whole nation — 8 Afghan internationals were being written down as Indian and not overseas. `is_overseas` is NULL too, since it was a claim derived from a value never observed. Composite sides (ICC World XI, World XI, Africa XI, Asia XI) excluded from the vote: Rashid Khan resolved to `ICC World XI` on one cap. 502 resolved, 314 unknown. |
| A24 | Keeper role is per franchise-season, from stumpings attributed to the season they happened in — not the career `people.is_keeper` flag, which gave exactly one keeper to only 41 of 166 franchise-seasons. Now 116 with one, 24 with two, 26 unproved. `keepers_by_season.csv` carries the per-squad answer; blank is undecided, not yes. |
| A25 | Two phases level on the same count is `mixed`, not whichever the tie-break picks. A tie for the lead among three phases can only be 50/50, which passes the >= 50% dominance test, so the naive rule names a phase by coin toss. |
| A26 | Role thresholds asymmetric and calibrated, not chosen: `BAT_MIN = 9.0` balls faced/match, `BOWL_MIN = 6.0` legal balls bowled/match. Symmetric cutoffs gave 2.9% or 12.6% all-rounders with a "neither" bucket. Falsifiable window pinned by a 12-player calibration table in `etl/roles.py`. **Provisional — see the note below the table.** |
| A27 | A player who neither batted nor bowled gets no `squad_members` row. `role` is NOT NULL and fielding a dismissal implies no role. 47 of 3,384 pairs excluded, 3,337 written. |
| A28 | §7.1 fitting filter is `innings_scheduled_balls = 120` alone; the `not was_reduced` clause is dropped. It excluded 12 first innings that were themselves scheduled for and played to a full twenty — the reduction fell on the chase, after they ended. 149,697 -> **151,175** deliveries. Refines A21, which remains correct about *why* `= 120` is the right predicate. |
| A29 | `deliveries_phase_idx` dropped in migration `006`. Three distinct values over 295,732 rows: the planner will not choose it, and every query that filters on phase also filters on match or franchise-season. `deliveries` is 63 MB, down from 65. |
| A30 | **The four override CSVs cannot be narrowed further by derivation, and that was measured rather than assumed.** Afghanistan's absence is documented Cricsheet *policy* — 158 matches withheld — so no re-download will ever close it. No nationality-restricted competition exists among the 1,115 published archives. Catches name the proven keeper 51% of the time (86% within the top 3), and the career-keeper cross-reference is **wrong for 5 of the 39 squads it is confident about**, failing in the A23 shape. So the generators emit **evidence and a ranking, never a verdict**: `merge_override` now takes the decision column by name, preserves it untouched, and refreshes every other column. `cricinfo_id` added to all four files. No migration was needed — A19 stands unchanged. |

**A26 is not settled.** The thresholds classify twelve undisputed players correctly, and
twelve is a small anchor set. **When §7 lands and the top-20-per-cohort lists print, read
them for a player who looks miscategorised** — the shape to watch for is a pure batter
tagged all-rounder on the strength of a few tidy overs. If that appears, the thresholds
get retuned then, against the larger evidence. Do not touch them before then, and do not
treat the current values as ratified beyond the twelve.

## Open questions

- **`wickets_down` on `deliveries`** — deliberately not added. Derivable with a window
  function; revisit as a migration when §7.1 lands if the recompute cost is annoying.
- ~~**Fielders on `deliveries`, as the fallback keeper signal.**~~ **Closed 2026-07-30,
  A30 — the idea was tested and does not work.** It was filed as the better keeper
  signal, needing a migration and a full reload. It needed neither: keeper derivation
  already reads the archive, so catches were measured for free. Cricsheet has **no keeper
  flag** on `fielders[]` — only `substitute` — so a catch behind the stumps cannot be
  told from one at slip, and the top non-bowling catch-taker is the proven keeper only
  51% of the time. It survives as a *ranking* in `keepers_by_season.csv`, nothing more.
  **Do not reopen this as a way to avoid the hand-fill; it was already tried.**

## Stage status

| Stage | State |
|---|---|
| 1. Scaffold + migrations + runner | done |
| 2. Downloader | done; 5 archives fetched, manifest + checksums written |
| 3. Franchise reconnaissance | done; mapping confirmed, 15 franchises / 166 franchise-seasons |
| 4a. Parser (pure, no DB) | done; 1,243 matches parse, 295,732 deliveries, 16 tests pass |
| 4b. Migrations applied | done; 4 applied to Neon, 8 tables / 14 checks / 21 indexes, runner idempotent |
| 4c. Loader, 2016 only | done; 60 matches, 14,096 deliveries, 1,324 appearances, 160 people, 8 franchise-seasons |
| 4d. Full archive loaded | done; 1,243 matches, 295,732 deliveries, 28,106 appearances, 816 people, 166 franchise-seasons, 70 MB, 33s |
| 5. Validation 1-7 | done; check 5 now live off `squad_members`, check 6 still awaits SPEC 7 |
| 6. Squads, roles and bands | code done; **check 19 FAILS by design — 26 franchise-seasons have no keeper and cannot be drafted legally until `keepers_by_season.csv` is filled** |

2016 was checked against the public record before being called done: Kohli 973 runs off
**640** balls (the all-time single-season record — recorded here as 637 until check 7
corrected it, see A22), Warner 848, de Villiers 687, Bhuvneshwar Kumar 23 wickets (Purple
Cap), SRH 17 matches. Display names came out era-correct ("Royal Challengers Bangalore",
"Delhi Daredevils", "Kings XI Punjab", "Rising Pune Supergiants"), which is what
`franchise_seasons.display_name` exists for. One parser warning, `980989 innings 1`, and
its 108 deliveries carry a null scheduled length.

The full archive produced exactly the 6 A17 warnings and no others, and every headline
figure matched what the parser had reported before any database existed.

## Validation

```bash
uv run python -m validation                 # checks 1-6, 17, 18, 19
uv run python -m validation --check 17      # one check, BY ITS SPEC 8 NUMBER
uv run python -m validation --scorecards    # check 7, for reading by hand
uv run python -m validation --match 598027  # one scorecard
```

`--check` takes the SPEC 8 number, not the position in the list. It used to take the
position, which was harmless while the checks ran 1-6 and became a silent trap the moment
the suite skipped to 17: `--check 17` reported "no such check" while `--check 7` ran it.

## Section 6: squads, and the four CSVs that gate the deck

```bash
uv run python -m etl.derive_people --all    # nationality, keepers, bowling style
uv run python -m etl.derive_squads          # squad_members, ~30s
```

Both are re-runnable and `derive_people` is idempotent to the byte. `derive_squads`
truncates and rewrites. Run `derive_people` before `derive_squads` — the keeper role
reads `keepers_by_season.csv`, which the first command generates.

**One column per file is the decision; the rest is evidence** (A30). `merge_override`
takes the decision column by name, carries it across untouched, and refreshes everything
else, so adding a new signal reaches rows that already exist. **Nothing else in the repo
may write a decision column.**

| CSV | Decision column | Rows awaiting a human | Blocks |
|---|---|---|---|
| `nationality.csv` | `nationality` | 314 of 314 | the four-overseas rule |
| `keepers.csv` | `is_keeper` | 440 of 489 | the keeper slot |
| `keepers_by_season.csv` | `kept` | 467 of 633 | the keeper slot |
| `bowling_style.csv` | `bowling_style` | 479 of 479 | A8 pace/spin slots (not a legal draft) |

Blank means undecided in all four. Nothing is guessed and nothing is defaulted, so an
unfilled row shows up as a NULL or a missing keeper rather than as a wrong answer. Every
row carries a `cricinfo_id` so a reviewer identifies the player instead of searching for
one; coverage is complete and a test enforces it.

`keepers_by_season.csv` grew from 345 rows to 633 because each squad now also lists its
top three non-bowling catch-takers as candidates. **The ranking is not the answer** — for
2011 Delhi it puts Sehwag first and Ojha, who kept, second. Read `catch_rank`,
`keeper_elsewhere` and `balls_bowled` together, not `catch_rank` alone.

A typo in a decision column reads as a silent "no" rather than an error, so
`tests/test_overrides.py` pins the vocabulary each reader accepts: `kept` and `is_keeper`
take y/yes/true/1 or n/no/false/0, `bowling_style` takes pace or spin.

A **SKIP is not a pass** and the runner says so. Checks 5 and 6 target tables SPEC 6 and 7
build; they skip with a reason rather than reporting green against an empty table. Check 6
turns into a hard FAIL the moment a derived table appears without the check being written,
so it cannot be forgotten.

Check 7 is the only check a human can disagree with, and it is the only one that has ever
found anything the others could not — see A22. Five matches, hand-verified: `335982`
(McCullum 158* off 73), `598027` (Gayle 175* off 66), `980987` (Kohli 109, de Villiers
129*), `1216517` (tied, then two super overs), `1426312` (2024 final, SRH 113 all out).

2016 was the inspection season for stage 4 because it exercises Gujarat Lions and Rising
Pune Supergiants in one go. It remains the `etl.load` default. The full archive went in
before stage 5, because check 7 needs 2008/2013/2020/2024 present and one season out of
nineteen exercises almost none of the parser's traps — no super over, no `2007/08` label,
no fractional target, and one of the eight miscounted overs.
