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
- **When a rule and the schema that serves it are decided together, verify the schema
  carries every input the rule names — before applying the migration.** This is a standing
  check, not a note about one bug. Migration 007 stored `runs_remaining_total`, and the
  *same message* corrected SPEC 7.1 to price a wicket by expected **final** total, which is
  `runs_so_far + runs_remaining`. Both halves were reasoned about carefully and separately;
  nobody put them side by side, so a rule went into the spec that the table just applied
  could not compute. Migration 008 added the column. **The fix was two lines and is not the
  lesson.** The reconciliation step is the one that got skipped, and it gets skipped
  precisely when both halves feel already-decided. Expect this to recur in the simulator,
  where the parameters it consumes and the tables that hold them will be specified together.
  **It recurred immediately.** Migration 009's `not_rateable_reason` CHECK and the loader's
  gate were specified in the same pass, and reconciling them found that "matches played" had
  two candidate denominators disagreeing on 890 of 3,333 player-seasons and flipping 61
  across the gate. This check is not a formality even one section after it was written down.
- **A rule enforced by a CHECK is protected by the database. A rule that lives in loader
  behaviour is protected by nothing unless a test points at it, so a ratified loader rule
  without a test is an unprotected rule.** Schema rules announce themselves — a reader of
  the migrations sees them. Behavioural rules are invisible: A37's shared state-resolution
  walk has no column and no constraint, so nothing tells a future reader it was ever
  decided, and the day somebody rewrites the walk it vanishes silently with a moved rating
  as the only symptom. When ratifying a rule, ask which of the two kinds it is; if it is
  the second, the test ships **with** the rule, not after it. `tests/test_impact.py` is the
  worked example, and every assertion in it was verified to fail with the corresponding line
  broken — an unfalsified test is the same unprotected rule wearing a badge.
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
| **Total, MEASURED after SPEC 7.1 and migration 007** | **68.6 MB** — the state model costs **88 kB** for 241 rows. The fitted grid is four orders of magnitude smaller than the deliveries it was fitted on, which is the whole argument for storing it. |
| **Total, MEASURED after SPEC 7.2-7.3 and migration 009** | **73.1 MB** — `player_season_impact` is **1,080 kB** for 5,149 rows, most of it the two indexes. Every rating in the game costs one megabyte. |

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
| A31 | **§7.1 state model stored as two tables, migration 007, because the grains genuinely differ.** `state_ball_outcomes` is (over x bucketed wickets), 80 rows; `state_runs_remaining` is (over x **exact** wickets), 161 rows. One grain cannot serve both: per-ball rates are too noisy for exact wickets, and inside a two-wide bucket one more wicket usually stays in the bucket, so a bucketed runs-remaining grid would price a wicket at zero for half the states. **Counts only, never rates** (A19), with a CHECK that the seven outcome columns sum to balls faced. **The cost of a wicket is the drop in expected FINAL total, not the difference in expected runs remaining** — the latter is confounded by runs already scored and comes out **negative** at over 12, 0 down. SPEC 7.1 corrects the old wording and names it. All 80 states stored, the 5 never observed as **explicit zeroes**, because an absent row and a zero row say different things to the simulator. Thin cells kept raw with their `n` and shrunk by the consumer, never smoothed at rest. |
| A32 | **Shrinkage cannot carry the low-volume problem, and §7.2 was written as though it could.** Shrinking toward an own-career prior protects against a thin *career* and never against a thin *season*: for a thin season the prior is mostly that same thin season, so a larger `k` makes the outlier climb rather than fall. Measured: BA Bhatt's 5-ball 2011 goes **+2.003 -> +2.206** as `k` runs 50 -> 800. No value of `k` fixes this. The fix is a gate, not a constant — see A33. |
| A33 | **Two draftability gates, not one.** The §7.3 four-match gate stays, and a **balls floor** joins it: **100 balls faced** for batting, **150 legal balls bowled** for bowling. Below the floor a player-season is *not rated at all* — not shrunk, not floored, simply not draftable from that squad. Floors measured from where the raw top-5 stabilises, per discipline rather than assumed to transfer: batting settles at 100 (+0.99 at 100, +0.97 at 150 and 200), bowling only at **150** (+0.87 at 100 with RG Sharma's 138-ball 2009 in the top twelve; +0.76 at 150 and 200, and he is gone). 150 balls is ~7 matches, so the match gate is non-binding for bowling. Rateable: **1,027 of 2,954** batting seasons, **787 of 2,195** bowling. **Both floors are empirically derived from where each raw list stops moving, not round numbers** — they only look round because of the sweep grid, so a revised archive moves them rather than keeping them. **Both gates count the scoring set**, not `squad_members.matches_played`: the two disagree on 890 of 3,333 seasons by up to 7 matches and flip 61 across the gate, and a gate asking "is there enough evidence to rate this?" must count the evidence the rating was computed from. Confirmed after the load — all 61 newly-gated seasons are marginal (max 63 balls faced, max 72 bowled) and **0 of 61 would have cleared a balls floor anyway**, so the tightening is right in principle and inert in effect. |
| A34 | **A residual survives the floor and is recorded rather than papered over.** Suryavanshi 2025 (122 balls, raw +0.574) shrinks **up** to +0.754 and outranks Sehwag 2011 (240 balls, raw +0.615, shrunk +0.429). Strictly-dominated inversions — fewer balls *and* lower raw, yet higher rating — are **4.56% within-band**. Dropping A7's own-career LOSO prior for a band-season-mean prior alone gives **3.71%** and raises Kohli's retained spread 68% -> 80%, at the cost of A7. **Explicitly provisional and deliberately not fixed now** — the repair means calibrating a new prior against pre-normalisation numbers, and §7.4 changes exactly the baseline the prior is measured against, so tuning it twice risks landing on a value that was right for the wrong baseline. **A34 and A35 are one decision viewed twice** (how hard to pull a thin season toward a prior, and which prior) and **resolve together in a single pass post-§7.4**. Neither may be ratified alone. |
| A35 | **`k = 100` is provisional and is not settled until re-checked post-§7.4 against within-season-normalised numbers.** Chosen for noise protection over spread: Kohli's within-player spread retained runs 81/68/53/38/23% at k = 50/100/200/400/800. Normalisation may make a lower `k` affordable. **Do not treat 100 as ratified.** Kohli calibrates within-player spread and ordering only, never level — the league mean is not zero and drifts from **-0.188 (2009) to +0.205 (2026)**, which is exactly what §7.4 removes. **Resolves in the same pass as A34.** `k` lives in exactly one place, the `player_season_rating` view, and is never a stored column — a stored shrunk value plus a stored `k` are two copies of one fact and would drift the moment the constant is revisited, which is guaranteed. Re-tuning is a view replacement and no reload. One view, single source, no exceptions. |
| A36 | **Dismissal attribution is striker-only.** Charging a batter for a non-striker run-out is wrong on its face. 7,458 fitting-set dismissals, **319 (4.28%)** belong to somebody other than the striker. The aggregate effect is tiny (**+0.0010**/ball mean) and the per-player effect is not (**max +0.193**, min -0.049, two changes in the top 20), which is why the aggregate could not have settled it. The any-wicket grid is kept as a **labelled diagnostic only**, never consumed by ratings. Bowling attribution is `credited_to_bowler`, so run outs are excluded there too. |
| A37 | **One state-resolution rule across both halves of a ball's impact.** The runs baseline silently `continue`d on thin cells while the wicket cost fell back to the nearest state — so **867 balls (0.31%)** were dropped, and non-randomly: thin cells are collapse and death states, so the drop biased against exactly the players who batted in them. Kohli 2016 read **838 off 583** against a real **860 off 590**. Both baselines now take the same nearest-bucket-in-the-same-over walk. After the fix: **19 of 19** Kohli seasons reconcile exactly, 2016 = **860 off 590**, and nothing is dropped — 886 of 281,389 batting balls and 921 of 280,192 bowling balls resolve to a neighbour instead. `uv run python -m etl.impact --reconcile` is the standing check. **A37 has no schema representation and never will** — it is a property of a walk, so `tests/test_impact.py` is the only thing protecting it, per the standing rule above. |
| A38 | **`player_season_impact` is long grain, migration 009 applied 2026-07-30.** One row per (franchise-season, person, **discipline**), not one wide row with batting and bowling side by side. Measured: 5,149 long rows against 3,333 wide, and the wide form carries a NULL half on 2,132 of them because most player-seasons are one discipline only — and a discipline that does not exist reads identically to one that scored zero. Long grain also lets A33's two different floors share one column. **Counts and inputs only, never a rating** (A19): `impact_total` is the SUM, and the gate constants are stored beside the result so the `not_rateable_reason` CHECK can recompute the reason and refuse a loader that gates on one rule and writes another. Reasons after the load: `balls` 2,086, NULL 1,812, `both` 1,249, `matches` **2** — Symonds 2008 (105 balls, 3 matches) and Hussey 2008 (100 balls, 3 matches), both verified to have identical all-match and scoring-set counts, so the match gate earns its keep independently of the A33 denominator choice. |
| A39 | **A33's floors leave whole cohorts unrateable, and this is a §7.4 and check-12 problem recorded before §7.4 rather than discovered inside it.** Coverage of the 166 franchise-seasons, counting how many can field a *rated* player per slot: opener 166, top_order 165, middle 150, **finisher 43**, **tail 0**; bowling middle 164, mixed 148, powerplay 137, **death 8** (of the 66 that have a death bowler at all, and 3 of the 8 are DJ Bravo). **`tail` is not thin, it is empty** — 0 of 707 tail player-seasons clear the 100-ball batting floor and the largest tail season in nineteen years is **95 balls**, so no tail batter can ever be rated on batting. That is the floor being *correct* — a number 9 genuinely has no measurable batting season — but it has three consequences. (1) §7.4 cannot estimate a cohort offset for `tail` from zero observations and must not try; `death` at 8 is the same problem in A2's stated form. (2) **Check 12 must be written to FAIL on slot coverage, not tolerate it.** (3) The deck may be fine anyway, because a tail batter is drafted for bowling and **393 of 730 tail player-seasons are bowling-rateable** — but that is a claim about the slot template (A6/§6.4) that has not been checked and must be, before §7.4 assumes it. **Do not resolve any of this by lowering a floor** — A33 measured both and they are where the lists stop moving. |
| A40 | **The slot template was provisional pending feasibility; it is now measured, repaired and CLOSED.** `etl.feasibility` plays the draft out — 15 picks, uniform deck, dedupe on `person_id`, the §1.1 deal-time guarantee — instead of counting slots. **The completion rate was the wrong headline: with the guarantee on the draft completed 100% of the time under every policy, and guarantee-off the same template failed 1.7% / 55.5% / 54.4% for a rational / naive / random drafter**, stranding on **`finisher`** then `keeper`. SPEC called the guarantee a safety net and it was a load-bearing beam — and a guarantee that fires constantly is quietly collapsing the variety A10's sampling exists to create. **The repair, ratified: `middle` (x2) and `finisher` (x1) become one `middle_or_finisher` band of three covering positions 5-8**, because `finisher` alone was servable from 43 of 166 franchise-seasons and **29 distinct people in nineteen years**, and because the 5-to-8 order is a cricketing continuum rather than two jobs. **`keeper` is resolved by the hand-filled `keepers_by_season.csv`, not by a template change** (126 of 166, 35 people, *with the CSV unfilled*). Measured on the real merged template, not the simulated variant: supply **154 fs / 117 people** (was 150/102 + 43/29), rational guarantee-off **0.0%, 0 of 2,000**, naive 10.7%, random 16.2%, and **the guarantee fires on 0.0% of rational drafts** — it is a net again. **The bottleneck did not move up one position, which was checked rather than assumed**: `opener` (166 fs / 108 people) and `top_order` (165 / 134) are numerically identical pre- and post-merge, so the merge added supply and took none. Thinnest slot is now `keeper`, i.e. check 19's business. **The guarantee stays under two new conditions: it must log every firing** (the report prints drafts-fired, picks-fired, worst-single-pick) **and the free-pass fallback stays.** |

**A26 is not settled.** The thresholds classify twelve undisputed players correctly, and
twelve is a small anchor set. **When §7 lands and the top-20-per-cohort lists print, read
them for a player who looks miscategorised** — the shape to watch for is a pure batter
tagged all-rounder on the strength of a few tidy overs. If that appears, the thresholds
get retuned then, against the larger evidence. Do not touch them before then, and do not
treat the current values as ratified beyond the twelve.

**And one extra question to ask in that same pass, filed from A40:** the pre-merge
`finisher` pool was dominated by all-rounders — Warne, Ashwin, Cummins and Jadeja sat in it
beside Dhoni, Karthik, Russell and Bravo. **Does retuning the all-rounder threshold change
5-8 batting coverage?** A26 decides who is an all-rounder, and that decision is what pushes
a player out of a batting band into a bowling one, so the two are coupled and only one of
them has been measured. The merge makes this non-urgent — it does not answer it.

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
| 5. Validation 1-7 | done; check 5 now live off `squad_members`, check 6 now live off the state model |
| 5b. Checks 15 and 16 | done 2026-07-30; both had been asserted in SPEC as measured fact with no standing check behind them. 15 regenerates all 295,732 scorecard references from `legal_ball`; 16 pins the unknown-length innings at exactly 6. |
| 6. Squads, roles and bands | code done; **check 19 FAILS by design — 26 franchise-seasons have no keeper and cannot be drafted legally until `keepers_by_season.csv` is filled** |
| **7.1 State model** | **done 2026-07-30**, migration 007 applied. 146,159 balls faced into 80 states (5 never observed, kept as zeroes) plus 161 exact-wicket runs-remaining states, 88 kB. Checks 6, 8 and 20 written; check 6 came off SKIP. **A31 corrected SPEC's wicket-cost rule — the old wording priced a wicket by differencing runs remaining, which is confounded and goes negative.** |
| **7.2-7.3 Shrinkage + gates** | **done 2026-07-30**, migration 009 applied. State model refit striker-only (7,139 of 7,458 dismissals), **5,149 `player_season_impact` rows** written, `player_season_rating` view live. Ratified: two gates on the scoring-set denominator (A33), striker-only attribution (A36), one fallback rule (A37), long grain (A38). Provisional and paired: A34 residual + A35 `k`, both post-§7.4. `tests/test_impact.py` pins A37 and the gate arithmetic, 15 tests. |
| **1.1 Draft feasibility** | **done 2026-07-30**, out of order and deliberately so. §7.4 normalises toward the slot template, so normalising before knowing the template is legal would be wasted work. `etl.feasibility` runs the draft; check 12 asserts it and was verified falsifiable. **Template retuned and SETTLED (A40 closed):** `middle` + `finisher` merged into `middle_or_finisher` x3, keeper resolved by the CSV. Rational guarantee-off failure 1.7% -> **0 of 2,000**, the guarantee is a net again, and `opener`/`top_order` are unmoved so the bottleneck did not shift upward. |
| 7.4-7.7 Ratings | **not started.** Normalisation, display-vs-simulator split, top-20 print. §7.7's lists have been read once and pass (see A39 for the cohorts that do not). **Checks 9-11, 13 and 14 are blocked behind this** and must not be written first; check 12 is now written and passing. A34 and A35 get re-read together once §7.4 lands. |

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
uv run python -m validation                  # checks 1-6, 8, 15-20
uv run python -m validation --check 20       # one check, BY ITS SPEC 8 NUMBER
uv run python -m validation --scorecards     # check 7, for reading by hand
uv run python -m validation --match 598027   # one scorecard
uv run python -m validation --leaderboards   # check 8's lists, for reading by hand
```

**14 checks: 13 pass, 1 fails.** The failure is check 19 and it is correct — 26
franchise-seasons have no keeper until `keepers_by_season.csv` is hand-filled. Nothing
skips any more: check 6 came off SKIP when migration 007 gave it a derived table.

**Checks 9-11, 13 and 14 are still unwritten and cannot be written yet.** Every one of them
reads a *normalised* rating (9 leakage, 10 under-shrinkage, 11 over-shrinkage, 13 cohort
era-drift, 14 innings skew), and §7.4-7.7 does not exist. Writing them now would produce
the tautologies A2 already had to delete twice — the original 10 and 11 were removed for
being true by construction, and check 2 for being enforced by five foreign keys. Do not add
them until there is a normalised rating to point them at.

**Check 12 is written and is not one of those.** It reads the §7.3 gate, which exists, and
it asks a question no count can answer — see A40. Its predecessor, "every franchise-season
has enough draftable players to fill every slot type", would have passed while the draft
was infeasible, because a drafter is served fifteen franchise-seasons one at a time and
dedupe removes a player from every other card. It runs the draft instead.

`--check` takes the SPEC 8 number, not the position in the list. It used to take the
position, which was harmless while the checks ran 1-6 and became a silent trap the moment
the suite skipped to 17: `--check 17` reported "no such check" while `--check 7` ran it.

## Section 7.1: the state model

```bash
uv run python -m etl.state_model            # fit and print the grid, no writes
uv run python -m etl.state_model --cells    # all 80 cells with their distributions
uv run python -m etl.state_model --write    # load migration 007's two tables
```

`--write` truncates and rewrites, so it is re-runnable, and it must be re-run after any
`etl.load`. Check 20 recounts the fitting set from `deliveries` and fails if the stored
totals have gone stale, so a forgotten refit is caught rather than silently believed.

**The outcome guard.** Seven columns hold the whole distribution because 0-6 covers 100% of
the archive today — 32 fives off overthrows, zero sevens. That is a measurement, not a law:
an 8 off an overthrow is legal. So `--write` **refuses to run** if any outcome has no column,
naming every offending value, and migration 007 carries a CHECK that the seven columns sum to
balls faced. Both, deliberately: the constraint protects the table, and the loader protects
against the constraint being quietly wrong about what belongs in it. Cricsheet revises recent
seasons, so a refreshed archive is exactly when both matter. `tests/test_state_model.py` pins
the guard and was verified to fail with it removed.

## Section 7.2-7.3: per-ball impact, shrinkage and the gates

```bash
uv run python -m etl.impact                 # every distribution the decisions rest on
uv run python -m etl.impact --player "V Kohli"
uv run python -m etl.impact --bowler "SL Malinga"
uv run python -m etl.impact --reconcile "V Kohli"   # A37's check, vs the scorecard
uv run python -m etl.impact --cohorts       # SPEC 7.7 top-20 per band and usage phase
uv run python -m etl.impact --gate-gap      # the 61 seasons the scoring-set denominator flips
uv run python -m etl.impact --write         # load migration 009's player_season_impact
```

Everything except `--write` is read-only: it scores every delivery in memory against the
§7.1 grids and prints raw and shrunk lists, the floor sweeps behind A33, the `k` sweep
behind A35, and the prior-breadth and era-drift tables. Re-running it is how any figure
quoted in A32-A39 gets re-checked.

`--write` truncates and rewrites `player_season_impact`, so it is re-runnable, and it must
be re-run after any `etl.load` **and after any `etl.state_model --write`** — the impact
numbers are scored against the stored grids, so a refit grid without a refit impact is a
stale rating that still looks fresh. Order: `etl.load` -> `etl.state_model --write` ->
`etl.impact --write`.

`k` is not written. It lives in the `player_season_rating` view alone (A35), so re-tuning
it post-§7.4 is `create or replace view` and nothing else.

## Section 1.1: draft feasibility

```bash
uv run python -m etl.feasibility                    # slot supply, policies, variants
uv run python -m etl.feasibility --trials 5000 --seed 11
uv run python -m validation --check 12              # the same thing, as a check
```

Read-only, no writes, no arguments needed. It plays 15-pick drafts against the real deck —
uniform over the 166 franchise-seasons, dedupe on `person_id`, the §1.1 deal-time
guarantee — under three drafter policies, and reports completion, where a stranded drafter
strands, how the superseded pre-merge template compares, and **how often the guarantee
fires**.

**Read the guarantee-off column, not the guarantee-on one.** Guarantee-on is 100% and will
stay 100% until coverage collapses, because the guarantee re-draws until it finds a
servable squad; it tells you the game works, not whether the template does. Guarantee-off
is the template judged on its own, and it is the number that moved from "fine" to "the
finisher slot is carrying all the risk" — and then, after the A40 merge, to 0 of 2,000.

**Then read the firing table, which is an A40 condition and not a diagnostic.** The
guarantee is allowed to exist only as a net, and the only way to notice it has become a
beam again is to count how often it fires. Rational is currently 0.0% of drafts and 0.00%
of picks. **If that starts climbing, the template has drifted out from under the deck** —
most likely because a revised archive moved A33's floors — and it is the template that
gets re-measured, not the guarantee that gets trusted harder.

`TEMPLATE` in `etl/feasibility.py` is the single definition of the slot template and check
12 imports it, so retuning the template moves the check with it rather than leaving the
check asserting a shape nobody drafts against.

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

A **SKIP is not a pass** and the runner says so. Checks 5 and 6 targeted tables SPEC 6 and 7
build; they skipped with a reason rather than reporting green against an empty table. Check 6
was written to turn into a hard FAIL the moment a derived table appeared without the check
being written, so it could not be forgotten — **and on 2026-07-30 it did exactly that.**
Migration 007 created the state tables and check 6 failed on the next run with "a derived
stat table exists but this check has not been written". The trap worked. It is now a real
pass and the suite skips nothing.

Check 6 also reports something worth knowing: `not is_super_over` in the §7.1 fitting filter
is **carrying no weight**. A super over has `innings_no >= 3` and a null
`innings_scheduled_balls`, so `innings_no = 1` and `= 120` each exclude all 175 of them
unaided. That is A21 working as designed rather than a reason to delete the clause — it
states intent — but the check asserts each clause independently so nobody mistakes the
explicit one for the thing doing the work.

**Check 6's trap fired twice more when migration 009 landed** — once for
`player_season_impact` and again for the `player_season_rating` view — and both times the
right response was a real assertion rather than adding the name to a list. The scoring set
gets the same independent-clause treatment as the fitting set, and the view is checked by
recomputing which rows *should* be offered from the gate columns and naming any row where
the view and the arithmetic disagree. A view that simply selected everything would pass a
count-based check, which is why the check counts nothing.

Check 7 is the only check a human can disagree with, and it is the only one that has ever
found anything the others could not — see A22. Five matches, hand-verified: `335982`
(McCullum 158* off 73), `598027` (Gayle 175* off 66), `980987` (Kohli 109, de Villiers
129*), `1216517` (tied, then two super overs), `1426312` (2024 final, SRH 113 all out).

2016 was the inspection season for stage 4 because it exercises Gujarat Lions and Rising
Pune Supergiants in one go. It remains the `etl.load` default. The full archive went in
before stage 5, because check 7 needs 2008/2013/2020/2024 present and one season out of
nineteen exercises almost none of the parser's traps — no super over, no `2007/08` label,
no fractional target, and one of the eight miscounted overs.
