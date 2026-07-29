# Working notes

Read `SPEC.md` first. This file tracks state, commands, and open questions.

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
```

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
- No LLM or AI API call anywhere in this codebase.
- Never edit an applied migration; the runner checksums them and will refuse. Write a new one.

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

Roughly 5x headroom. Note the brief's "1.2 million deliveries" figure is ~4x high:
1,243 matches x ~250 balls is ~310k, not 1.2M.

Replace the delivery figure with the true count once 2016 is loaded.

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

## Open questions

- **`deliveries.innings_scheduled_overs`** — added (2 bytes/row) so the per-innings death
  boundary and the §7.1 full-length filter are computable without re-deriving from the
  match. Not in the original schema. *Awaiting ratification.*
- **`deliveries_phase_idx`** — specified in the brief, but `phase` has three distinct
  values over ~300k rows. The planner will almost never choose it over a sequential scan,
  and it costs ~10 MB plus write overhead. Recommend dropping it and relying on the
  `(match_id, innings_no)` index. *Awaiting ruling.*
- **`wickets_down` on `deliveries`** — deliberately not added. Derivable with a window
  function; revisit as a migration when §7.1 lands if the recompute cost is annoying.

## Stage status

| Stage | State |
|---|---|
| 1. Scaffold + migrations + runner | done; migrations parse, not yet applied to a live DB |
| 2. Downloader | done; 5 archives fetched, manifest + checksums written |
| 3. Franchise reconnaissance | done; mapping confirmed, 15 franchises / 166 franchise-seasons |
| 4. Parser + loader, 2016 only | **blocked on `.env` connection strings** |
| 5. Validation 1-7 | not started |

Stage 4 uses **2016** as the single hardcoded season: it exercises Gujarat Lions and
Rising Pune Supergiants in one go. The 2008 `"2007/08"` label trap and the super-over trap
are covered by unit tests on individual files rather than by loading those seasons yet.
