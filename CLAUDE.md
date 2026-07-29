# Working notes

Read `SPEC.md` first. This file tracks state, commands, and open questions.

## Environment

Python 3.11 pinned via `uv`. No Homebrew, no Docker, no local Postgres. Database is
Neon (managed), so no Postgres extension may be used.

```bash
export PATH="$HOME/.local/bin:$PATH"
uv sync                                   # install deps
cp .env.example .env                      # then fill in DATABASE_URL
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

## Storage budget (Neon free tier)

Free tier is **0.5 GB logical data per project**; instant-restore history is metered
separately with a 1 GB-month allowance, so reload churn during development does not eat
the 0.5 GB.

Analytic estimate, to be replaced with measured figures after the Stage 2 download:

| | |
|---|---|
| Matches (2008–2025) | ~1,175 |
| Deliveries per match | ~250 (240 legal + extras) |
| **Total delivery rows** | **~300k** |
| `deliveries` heap | ~40 MB |
| 5 indexes on `deliveries` | ~50 MB |
| Everything else | <10 MB |
| **Total** | **~100 MB against a 500 MB cap** |

Roughly 5x headroom. Note the brief's "1.2 million deliveries" figure is ~4x high;
1,175 matches x ~250 balls is ~300k, not 1.2M.

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
| 1. Scaffold + migrations + runner | done, migrations parse; not yet applied to a live DB |
| 2. Downloader | not started |
| 3. Franchise reconnaissance (team-name-by-year grid) | not started — **needs user confirmation before 4** |
| 4. Parser + loader, 2016 only | not started |
| 5. Validation 1-7 | not started |

Stage 4 uses **2016** as the single hardcoded season: it exercises Gujarat Lions and
Rising Pune Supergiants in one go. The 2008 `"2007/08"` label trap and the super-over trap
are covered by unit tests on individual files rather than by loading those seasons yet.
