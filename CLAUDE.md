# Working notes

Read `SPEC.md` first. This file tracks state, commands, and open questions.

**`SPEC.md` is the source of truth and this is not a formality.** Three figures recorded
there as measured fact turned out wrong once code actually read the archive: the reduced-
match split (15/19, really 15/12/7), the Impact Player replacement count (523, really 524
deliveries carrying 563 entries), and the state-model fitting size (~145,000, really
149,697). Each was corrected in the spec the moment it was found, with the old value named
so the correction is auditable rather than silent. Keep doing that — a figure quietly
replaced is indistinguishable from a figure that was never checked.

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
- **A null must propagate as unknown.** Never let it acquire a default downstream. Prefer
  filters that test for the known value (`scheduled_balls = 120`) over filters that test
  for the absence of a problem (`not was_reduced`), because a null fails the first by
  construction and slips through the second.
- No LLM or AI API call anywhere in this codebase.
- **A migration is immutable the moment it has been applied to any database, including a
  local or personal one. Until then it is a draft and may be edited freely.** The runner
  checksums applied files and refuses drift. `003` was edited in place on 2026-07-29
  (`innings_scheduled_overs` -> `innings_scheduled_balls`) under a one-time exception,
  granted only because no database existed yet. This is not a precedent.

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
| **Total** | **~100 MB against a 500 MB cap** |

Roughly 5x headroom. The brief's "1.2 million deliveries" figure is ~4x high. The exact
count, from parsing the whole archive, is **295,732 deliveries across 1,243 matches and
28,106 appearances** — not an estimate.

## Decisions log

| # | Decision |
|---|---|
| A1 | Extras split into 5 smallint columns. Byes/legbyes are not charged to the bowler, so one column makes economy wrong. |
| A2 | Cohort offsets pooled across seasons, not per-cell z-scoring. Validation 10/11 replaced with predictive under/over-shrinkage tests; new check 13 for cohort era-drift. |
| A3 | Nationality from Test + ODI + T20I archives combined. Defaulted list gets full review, sorted by matches played desc. |
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

## Open questions

- **`deliveries_phase_idx`** — specified in the brief, but `phase` has three distinct
  values over ~300k rows. The planner will almost never choose it over a sequential scan,
  and it costs ~10 MB plus write overhead. Recommend dropping it and relying on the
  `(match_id, innings_no)` index. *Awaiting ruling.*
- **`wickets_down` on `deliveries`** — deliberately not added. Derivable with a window
  function; revisit as a migration when §7.1 lands if the recompute cost is annoying.

## Stage status

| Stage | State |
|---|---|
| 1. Scaffold + migrations + runner | done |
| 2. Downloader | done; 5 archives fetched, manifest + checksums written |
| 3. Franchise reconnaissance | done; mapping confirmed, 15 franchises / 166 franchise-seasons |
| 4a. Parser (pure, no DB) | done; 1,243 matches parse, 295,732 deliveries, 16 tests pass |
| 4b. Migrations applied | done; 4 applied to Neon, 8 tables / 14 checks / 21 indexes, runner idempotent |
| 4c. Loader, 2016 only | done; 60 matches, 14,096 deliveries, 1,324 appearances, 160 people, 8 franchise-seasons |
| 5. Validation 1-7 | not started |

2016 was checked against the public record before being called done: Kohli 973 runs off
637 balls (the all-time single-season record), Warner 848, de Villiers 687, Bhuvneshwar
Kumar 23 wickets (Purple Cap), SRH 17 matches. Display names came out era-correct
("Royal Challengers Bangalore", "Delhi Daredevils", "Kings XI Punjab", "Rising Pune
Supergiants"), which is what `franchise_seasons.display_name` exists for. One parser
warning, `980989 innings 1`, and its 108 deliveries carry a null scheduled length.

Stage 4 uses **2016** as the single hardcoded season: it exercises Gujarat Lions and
Rising Pune Supergiants in one go. The 2008 `"2007/08"` label trap and the super-over trap
are covered by unit tests on individual files rather than by loading those seasons yet.
