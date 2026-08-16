# IPL Draft Game — Phase 1 (Data Pipeline)

**This file is the single source of truth. It must not drift behind the code.**

Amendments ratified 2026-07-29 are folded in and marked **[A1]**–**[A12]**. Where an
amendment supersedes the original brief, only the amended text remains.

---

## 0. Working agreement

- Read the whole brief before writing code. Plan first, get approval, then build.
- Small, committed steps. One logical unit per commit.
- **Never fabricate a data value.** If a field cannot be derived from source, leave it
  NULL, log it, and say so. A wrong statistic is worse than a missing one here.
- Write the validation script before declaring a stage done.
- **Do not scrape** ESPNcricinfo, Transfermarkt, Howstat or similar. Terms prohibit it.
- **No LLM or AI API call anywhere in this codebase.** The system is deterministic.
- Ask before making a schema decision not specified here.

---

## 1. What we are building (context, not this phase's scope)

A browser game modelled on 7a0.com.br, rebuilt for the IPL.

The player is dealt **one** random franchise-season at a time (e.g. Deccan Chargers 2009).
They pick a single player from that squad, or spend a reroll. Repeat until they have a
15-man squad against a fixed slot template. They then choose a playing XI respecting the
four-overseas limit, nominate an Impact Player, and simulate a seven-match run (five
league games, qualifier, final). The goal is an unbeaten title.

The simulator is a ball-by-ball model fitted from historical delivery data. No auction,
no purse, no budget. Slots are the only draft constraint.

### 1.1 Draft rules

**Rerolls.** A reroll discards the current squad and deals another.

| Mode | Rerolls |
|---|---|
| Classic (ratings visible) | 3 |
| Memory board (ratings hidden) | 1 |
| Room game | host chooses 1–5 |

Budget is for the whole draft, not per pick.

**Two reroll types.** Random (any franchise-season), or same franchise / different year.
The second must be disabled with a visible reason when the franchise has no other
eligible season.

**[A11] Reroll availability is queried from the data at deal time, never hardcoded.**
The reconnaissance quantifies it from the confirmed mapping:

| Franchise | Seasons | Alternative seasons available |
|---|---|---|
| Kochi Tuskers Kerala | 1 | **0 — reroll type permanently unavailable** |
| Gujarat Lions | 2 | 1 |
| Rising Pune Supergiant | 2 | 1 |
| Pune Warriors | 3 | 2 |

A hardcoded list would rot the moment a season is added. Query it, and grey out the
option with the reason shown.

**[A10] Deck weighting is uniform over franchise-seasons, and is an explicit named
constant in the dealer — not an accident of the query.**

Uniform sampling over the 166 franchise-seasons means Mumbai appears 19 times and Kochi
Tuskers Kerala once, so a player can complete a draft without ever seeing Kochi. **This
is intended: rarity should feel like an event.** But it must be a tunable weight so
playtesting can revisit it.

**Do not weight by franchise.** That would flatten the deck and destroy exactly the
scarcity the design wants.

**Deal-time guarantee.** Only deal a franchise-season containing at least one undrafted
player eligible for a slot the drafter has not filled. Check before serving, re-draw
silently otherwise. With one squad on offer instead of three, a stranded drafter is
likely rather than rare, especially at picks 13–15. Add a free pass that does not consume
a reroll as a safety net.

**Dedupe.** A person may appear in the deck many times at different ratings — Kohli RCB
2016 and Kohli RCB 2022 are distinct entries. A drafted squad may contain any person only
once. Dedupe on `person_id`, not on the franchise-season entry.

**Slot template. [A6 — supersedes the original template. SUPERSEDED IN TURN by A72/A73,
2026-08-02 — see §1.1a below. Kept here, not deleted, because the measurement history
below (A40, A61) is real and the reasoning it establishes — read the guarantee-off number,
not the guarantee-on one; a net that fires constantly is a beam — is still exactly how
§1.1a's own draft-feasibility numbers are read.]**

| Slot | Count |
|---|---|
| Wicketkeeper | 1 |
| Opener (1–2) | 2 |
| Top order (3–4) | 2 |
| **Middle or finisher (5–8)** | **3** |
| Pace bowler | 3 |
| Spin bowler | 2 |
| Open (any) | 2 |

**[A40, closed 2026-07-30] `middle` (5–6) x2 and `finisher` (7–8) x1 were separate slots
and are now one band of three.** The old split is named here so the change is auditable.

Keeper is a **role, orthogonal to batting band** — the keeper slot is filled by any keeper
regardless of where they bat. Batting-band slots apply to specialist batters. Bowlers fill
bowling slots regardless of their batting band, which is why `tail` gets no slot.

Bands are the §6.4 canonical bands. All-rounders may fill either a batting or a bowling
slot, decided by the drafter at pick time.

**[A40, ratified and CLOSED 2026-07-30] The template was provisional pending feasibility;
it is now measured, repaired and settled.** It had been written before coverage was known,
in the same way `k` was chosen before normalisation existed. `uv run python -m
etl.feasibility` plays the draft out; check 12 asserts it.

*What the simulation found, and why the completion rate was the wrong headline.* With the
deal-time guarantee on — which is the game — the draft completed **100% of the time under
every drafter policy**, which reads as "feasible" and is not. Removing the guarantee is the
honest measure, because it asks whether the template stands on its own rather than whether
the net can rescue it. Guarantee-off, the template as written failed **1.7% of rational
drafts and 55.5% of naive ones**, stranding on `finisher` and then `keeper`. **SPEC called
the guarantee a safety net and it was a load-bearing beam.** A guarantee that fires
constantly is also silently collapsing the variety uniform sampling exists to create.

*The repair, ratified: two changes.*

- **`finisher` accepts `middle` too — one `middle-or-finisher` band of three covering
  positions 5–8.** `finisher` alone was servable from **43 of 166 franchise-seasons and 29
  distinct people in nineteen years**; a mandatory slot drawn from 29 people has a nearly
  predetermined pick. Cricket backs the merge — the 5-to-8 order is a continuum, not two
  jobs.
- **`keeper` is treated as resolved by the hand-filled `keepers_by_season.csv`**, not by a
  template change. It is servable from **126 of 166 with 35 people** *while the CSV is
  unfilled*; filling it is the fix. Check 19 remains the gate on that.

*Measured on the real merged template (2,000 drafts per policy, seed 7), not on a simulated
variant:*

| | pre-merge | post-merge |
|---|---|---|
| slot supply | `middle` 150 fs / 102 people + `finisher` 43 / 29 | **`middle_or_finisher` 154 fs / 117 people** |
| rational, guarantee off | 1.7% | **0.0% — 0 of 2,000** |
| naive, guarantee off | 55.5% | 10.7% |
| random, guarantee off | 54.4% | 16.2% |
| guarantee fires, rational | — | **0.0% of drafts, 0.00% of picks** |

**The bottleneck did not move up one position, and that was checked rather than assumed.**
Widening the 5–8 band draws from the `middle` pool, so the concern was that `middle`'s
upstream neighbours would become the new constraint. `opener` (**166 of 166 fs, 108
people**) and `top_order` (**165 of 166, 134**) are numerically *identical* pre- and
post-merge — the merge added supply to the band and took none from anywhere else. The
thinnest slot is now `keeper` at 126 of 166, which is the CSV, not the template.

*The guarantee stays, as the net it was meant to be, under two conditions:*

1. **It must log every time it fires.** A net that silently becomes a beam again is the
   exact failure this exercise found, and the only way to notice is a firing count. The
   feasibility report prints drafts-where-it-fired, picks-where-it-fired and worst-single-
   pick for this reason.
2. **The free-pass fallback stays.** Rerolls 1–3 plus a free pass are unchanged.

*Filed, not closed:* the `finisher` list was dominated by all-rounders (Warne, Ashwin,
Cummins and Jadeja sat in it alongside Dhoni, Karthik, Russell and Bravo). **When A26's
role thresholds are revisited against the §7.7 lists, ask whether retuning the all-rounder
threshold changes 5–8 coverage.** The merge makes this non-urgent; it does not answer it.

**[A8] Interim bowling slots.** Until `etl/overrides/bowling_style.csv` is filled, collapse
the pace (3) and spin (2) slots into a single generic **bowler slot of 5**, so the draft
loop is testable end to end. `bowling_style.csv` ships immediately after the full archive
load, not at the end of the derivation stage.

**Memory board mode.** Hides the rating only. Name, role, nationality and franchise-season
stay visible.

**Room determinism.** Derive each dealt squad from `hash(room_seed, pick_index,
reroll_index)`, not by drawing sequentially from a stream. Identical choices then produce
identical squads, divergence after a reroll is legitimate, and any completed run is
reproducible from the seed alone for leaderboard disputes.

**Phase 1 builds none of that.** This phase is the data pipeline only.

### 1.1a Position eligibility and the final twelve, as actually built [A72/A73, A76]

The slot template above (A6/A40) was built, played, and replaced with something
skill-driven: no slot categories at all. The drafter picks a playing **twelve directly**
— eleven who bat, one Impact Player — one slot at a time, rather than fifteen candidates
followed by a separate arranging phase.

**Eligibility, as of A76, is a single career-based ROLE, not a per-exact-position set.**
A72/A73's mechanism (below, kept for the history) is retired. Every player is exactly one
of `top` (slots 1-3), `middle` (3-5), `finisher` (5-7), or `tail` (8-11), decided by
`etl.batting_roles.batting_role` against a four-tier cascade: that SEASON's own position
evidence if it clears `MIN_INNINGS_FOR_ROLE = 5`; else the player's CAREER evidence if
that clears it; else, if `squad_members.role != 'bowler'`, whatever thin evidence exists
at all (a short-career domestic batter is not the same population as a bowler who never
bats, and must not be defaulted the same way — see A76); else `tail`. Within each grain,
the dominant band is the one with the largest AGGREGATE innings total, positions 3 and 5
each allocated to whichever neighbouring band has more unambiguous evidence — not "the
single most-frequent exact position," which is measurably wrong (Rohit Sharma's most
common individual position is 4, yet his top-order innings, split across positions 1 and
2, outweigh it in aggregate). New table, migration 021:
`person_season_batting_positions`, the per-season twin of A72's `person_batting_positions`
below. Samson now reads `top` in his opener seasons and `middle` in his others; Rinku
reads `finisher` every season; Suryavanshi reads `top`; Narine swings between `top` and
`tail` season to season, matching how he actually batted.

**A72/A73's mechanism, superseded but kept for the history.** A position was real
evidence about a player only if he had batted there in at least
`MIN_INNINGS_AT_POSITION = 5` separate career innings, counted career-wide in
`person_batting_positions` (migration 017, derived by `etl.career_positions`, which A76
still uses for its own career-grain evidence). The naive alternative — a career
`MIN`/`MAX` envelope, already stored on `squad_members` — was tried first and measured
wrong: one outlier innings widens an envelope forever (SV Samson came out 1–8, Rinku
Singh 3–8). A player with no qualifying position anywhere, or whose qualifying positions
were *all* already in the lower order, widened to the full `LOWER_ORDER_BAND = (7, 11)`.
Both `MIN_INNINGS_AT_POSITION` and `LOWER_ORDER_BAND` are deleted from the code; A76's
own floor and default tier answer the same two questions differently, as above.

**The final twelve is drafted directly, not arranged afterward.** Every pick names both a
candidate and the slot he occupies (a batting position, or Impact) in one move, **final the
instant it lands** — no bench, no unplace, no rearranging. Squad size and slot count are
therefore the same number, twelve, and the deal-time guarantee's forward check
(`could_still_complete`) reduces to two integer inequalities rather than a bipartite-
matching search: with exactly as many future picks as future open slots, a maximally-
flexible hypothetical future pick can always be matched to *some* remaining slot (Hall's
theorem), so only the keeper and bowling-depth counts can still be unreachable.

**Requirements on the final twelve**, unchanged in spirit from the old template: at least
one wicketkeeper, at least five with a bowling rating, at most four known-overseas (A61).
There is no keeper "slot," no pace/spin split, no "open" band — a keeper bats wherever his
own career says he bats, exactly like anyone else.

---

## 2. Stack

- **ETL:** Python 3.11 (pinned via `uv`; do not install Homebrew). `polars`,
  `psycopg[binary]`, `pydantic`. No heavyweight framework.
- **Database:** Neon Postgres. No extension unavailable on managed Postgres.
- **Two endpoints, and the distinction matters.** `DATABASE_URL` is Neon's **pooled**
  endpoint, for the application later. `DIRECT_URL` is **unpooled**, and migrations and
  every bulk load must use it — the pooled endpoint runs PgBouncer in transaction mode,
  which interferes with session-level operations and makes DDL and `COPY FROM STDIN`
  unreliable. `etl.db.direct_url()` rejects a URL containing `-pooler` outright.
- **Bulk loading:** `COPY FROM STDIN`, never row-by-row inserts. This is what makes a
  remote database viable for the delivery table.
- **Migrations:** plain numbered `.sql` files in `migrations/`, applied by `etl/migrate.py`.
  No ORM, no Alembic. Applied migrations are checksummed; editing an applied file is an
  error, write a new one.

```
/migrations        numbered SQL migrations
/etl               ingestion and transform scripts
/etl/overrides     hand-maintained CSVs for data the source lacks
/validation        checks that must pass before a stage is "done"
/data              gitignored, downloaded archives land here
.env.example
SPEC.md
CLAUDE.md          working notes, maintained as we go
CREDITS.md         ODbL attribution
```

---

## 3. Data sources

### 3.1 Cricsheet

Ball-by-ball data for every IPL match, under the Open Database License (ODbL).

**Do not hardcode download URLs from memory.** Fetch `https://cricsheet.org/downloads/`
first, resolve the current links, and log exactly what was resolved. Filenames have
changed before.

We need:
- The IPL ball-by-ball JSON archive (one JSON file per match)
- The people register CSV
- **[A3]** The men's **Test, ODI and T20I** archives, for nationality derivation

ODbL requires attribution — see `CREDITS.md`. Share-alike attaches if we ever publish the
derived database.

**[A9] Scope is 2008–2026, 19 seasons, 1,243 matches, 166 franchise-seasons.**

2026 is complete (74 matches, consistent with the current format) and is in scope.
Excluding it would date the game immediately. Two conditions attached, and **both are now
discharged — A9 is CLOSED (2026-07-30).**

1. **Cricsheet is contributor-driven and recent matches get revised after the fact.**
   The archive SHA256 is recorded in `data/manifest.json`, and the 2026 files had to be
   **re-verified against a fresh download before ratings were finalised**. *Discharged:*
   `ipl_json.zip` was re-fetched from cricsheet.org on 2026-07-30 and is bit-identical to
   the archive every figure in this spec was measured from — SHA256 `841b9829…6ea2a79`,
   5,180,977 bytes. **Verified by re-downloading, not by re-reading the recorded hash**,
   which would only have confirmed the file on disk had not changed locally.
2. If any 2026 playing condition materially changed scoring, within-season normalisation
   (§7.4) absorbs it — but **flag it** if that season's baseline sits far off trend
   rather than letting it pass silently. *Discharged:* 2026's batting baseline is
   **+0.2567** runs/ball against 2025's **+0.2149** and 2024's **+0.1869** — the last step
   in a monotone climb, the same size as the two before it. On trend, so there is nothing
   to flag, and §7.4's centring removes the level either way.

**Closing A9 does not make the archive permanent.** A future re-download that differs is a
reload and a refit, not a discrepancy to reconcile; the hash is what tells the two apart.

### 3.2 Match JSON shape

Parse defensively; older matches carry less detail.

```json
{
  "meta": { "data_version": "1.1.0", "revision": 1 },
  "info": {
    "balls_per_over": 6, "city": "Bangalore", "dates": ["2008-04-18"],
    "event": { "name": "Indian Premier League", "match_number": 1 },
    "match_type": "T20",
    "outcome": { "winner": "Kolkata Knight Riders", "by": { "runs": 140 } },
    "overs": 20, "player_of_match": ["BB McCullum"],
    "players": { "<team name>": ["<player name>", "..."] },
    "registry": { "people": { "<player name>": "<person_id>" } },
    "season": "2007/08", "teams": ["...", "..."],
    "toss": { "winner": "...", "decision": "field" },
    "venue": "M Chinnaswamy Stadium"
  },
  "innings": [{
    "team": "Kolkata Knight Riders",
    "powerplays": [{ "from": 0.1, "to": 5.6, "type": "mandatory" }],
    "overs": [{ "over": 0, "deliveries": [{
      "batter": "SC Ganguly", "bowler": "P Kumar", "non_striker": "BB McCullum",
      "runs": { "batter": 0, "extras": 1, "total": 1 },
      "extras": { "legbyes": 1 },
      "wickets": [{ "player_out": "SC Ganguly", "kind": "caught",
                    "fielders": [{ "name": "..." }] }]
    }]}]
  }]
}
```

---

## 4. Known data traps

Address each explicitly and prove it in validation.

**4.1 Identity.** Never key on name strings. Resolve every name via
`info.registry.people` to a stable person identifier and key everything on that.
Spellings vary across seasons and there are genuine collisions.

**4.2 Season labels.** `info.season` is inconsistent. **Derive `season_year` from match
dates.** Store the raw label for reference only.

**[A12] Three anomalies observed in the archive, all confirmed:**

| Season played | `info.season` says |
|---|---|
| 2008 | `2007/08` |
| **2010** | **`2009/10`** |
| 2020 | `2020/21` |

The 2010 case was not in the original brief and is the reason the field is untrusted
rather than merely special-cased. Note the asymmetry that makes it dangerous: 2009 — the
season actually played in South Africa, where a split label would be forgivable — is
labelled plainly `2009`, while 2010 is not. There is no pattern to exploit.

**4.3 Franchise renames.** `franchise_id` is stable across renames; `display_name` is
era-correct per season.

**Mapping confirmed 2026-07-29 against the archive. 19 team-name strings → 15
franchises → 166 franchise-seasons.** Every merge required was one already authorised;
nothing had to be guessed. Authoritative file: `etl/overrides/franchises.csv`.

| Names over time | Treatment |
|---|---|
| Royal Challengers Bangalore → Royal Challengers Bengaluru | Same franchise |
| Delhi Daredevils → Delhi Capitals | Same franchise |
| Kings XI Punjab → Punjab Kings | Same franchise |
| Rising Pune Supergiants → Rising Pune Supergiant | Same franchise (dropped "s") |
| Deccan Chargers, Sunrisers Hyderabad | **Separate** franchises |
| Gujarat Lions, Gujarat Titans | **Separate** franchises |

Chennai Super Kings and Rajasthan Royals each carry a 2016–17 gap (the suspension), with
Rising Pune Supergiant and Gujarat Lions occupying exactly that window. The data confirms
this independently — it is not a special case in code.

A team-name string absent from the override file is a **hard error**, so a new or renamed
side surfaces immediately rather than being silently split into its own franchise.

**4.4 Super overs.** Appear as additional innings. Exclude from all player statistics.
Flag the match.

**[A18]** 16 matches went to a super over and one of those — Mumbai v Kings XI, 2020 —
went to a **second** one, so it holds six innings. Innings numbering is the position in
the array, which gives that match super-over innings 3, 4, 5 and 6.

Both super overs of that match are flagged by the same `is_super_over` mechanism as any
other — there is no special case for the second one. `innings_no` is the position in the
array, so the match occupies 1 through 6 and the `smallint` column accommodates it.

Ties and super overs coincide exactly: all 16 ties carry an `outcome.eliminator` and
nothing else does. A tie carries **no `winner` key at all**. It is stored as
`result_type = 'tie'` with `winner_fs_id` set to the eliminator and `result_margin` null.
The side that won the match is recorded because it did win; the match is still recorded as
tied. Neither fact is lost and neither is invented.

**`matches.decided_by`** makes that row self-describing, because `result_type = 'tie'` with
a populated `winner_fs_id` otherwise reads as a contradiction. One of `runs`, `wickets`,
`eliminator`, `dls`, or null for a no-result match. No downstream aggregate has to infer
intent.

**D/L is the case that would otherwise hide completely.** `outcome.method` is present on
**23 matches and is always `'D/L'`** — there is no other method in the archive. Those 23
carry an ordinary margin, **13 by runs and 10 by wickets**, so a D/L result is
indistinguishable from a normal win by `result_type` alone. All 23 fall inside the 34
reduced matches, so D/L is a strict subset of reduction and not an independent exclusion
criterion — but it was recorded nowhere before this column existed.

`decided_by` therefore takes **`dls` in preference to the margin type**: the margin type is
already in `result_type`, and this is the only place the D/L involvement survives. Observed
distribution across all 1,243 matches:

| `result_type` | `decided_by` | Matches |
|---|---|---|
| wickets | wickets | 650 |
| runs | runs | 545 |
| tie | eliminator | 16 |
| runs | dls | 13 |
| wickets | dls | 10 |
| no result | *null* | 9 |

Two check constraints hold this shape: a tie must be `decided_by = 'eliminator'`, and a
no-result match must have `decided_by` null.

**4.5 Rain-reduced matches. [A13 — supersedes the original text]**

**`info.overs` is `20` in all 1,243 matches. It is a constant and carries no
information.** The original premise, that it drops for rain-reduced games, is false for
this archive. Do not read scheduled length from it.

A chasing innings states its own length in **`innings.target.overs`**, always — all 1,237
second innings carry one, and 34 carry a value below 20. The **first innings never states
it** and has to be read off what was bowled.

**[A17 — corrects the innings-1 breakdown given in A13]** A13 originally recorded the 34
reduced matches as 15 with innings 1 equal to `target.overs` and 19 with a full first
innings. That is wrong. The true split is **15 equal to target, 12 full at 120, and 7
neither** — innings 1 was itself curtailed at some third length, and that length is
written down nowhere in the file.

An innings stops short of twenty overs for one of three reasons and only two of them say
anything about the schedule. The rule, in order:

| Condition | Scheduled balls |
|---|---|
| Twenty overs bowled | 120 |
| No second innings at all | **null** — abandoned during innings 1, nothing attests to the intent |
| Chase scheduled for a full twenty | 120 — so was this innings; it ended on wickets |
| Shortened match, side all out | **null** — the innings ended on wickets, not on the clock |
| Shortened match, final over incomplete | **null** — rain stopped play mid-over |
| Shortened match, final over complete | 6 × overs bowled |

This resolves to null for exactly **6 innings** in the archive, each logged by name. It is
never guessed. The "chase scheduled for a full twenty" row is what keeps ordinary all-out
innings at 120 rather than mistaking a collapse for a rain curtailment — without it, 44
innings go null instead of 6.

**The 6 are not all inside the 34 reduced matches.** Three are — `1136566`, `1136592`,
`980989`, shortened matches abandoned mid-over. The other three — `1473495`, `1527685`,
`501265` — were abandoned during the first innings with no second innings, so there is no
`target.overs` to mark them reduced and `was_reduced` is **false**. Excluding them from
rating therefore cannot rely on the reduced flag; see the §7.1 fitting filter, which tests
for a known 120 so that a null fails the test by construction.

Note the interaction with §4.5b: the two innings that bowled 119 legal balls are the
5-ball miscounted overs, not curtailments. Counting **overs rather than legal balls** is
what keeps them at 120, so A14 is load-bearing here and not merely cosmetic.

**Scheduled length is stored in BALLS, not overs.** `target.overs` is not always an
integer — one match carries `9.2`, meaning nine overs and two balls. No overs column can
represent that. `deliveries.innings_scheduled_balls` holds 56 for that case.

**4.5b Miscounted overs. [A14]** Cricsheet flags eight overs across the archive where the
umpire miscounted and 5 or 7 legal balls were actually bowled, via `innings
.miscounted_overs`. Take this **from the source flag, never infer it**. Validation
check 3 whitelists them, and §7.1 drops them from the state-model fit — eight overs is
nothing, and it costs less to drop them than to reason about what a 7-ball over does to
the ball-index dimension.

**4.6 Abandoned and no-result matches.** May contain zero or partial innings. Must not
break the parser or skew per-match averages.

**[A12] Matches abandoned without a ball bowled are absent from the archive entirely** —
Cricsheet publishes ball-by-ball data, so no deliveries means no file. Ten interior gaps
across seven seasons: 2008 #47; 2009 #7, #13; 2011 #20; 2012 #32, #34; 2015 #25;
2017 #29; 2024 #63, #66.

Only *interior* gaps are detectable — a match missing from the tail of a schedule is
invisible, since the highest observable number is the highest one present. 2024 is short
three league matches but only two are detectable. Do not close this with a hardcoded
expected-schedule table; it would rot, and a zero-ball match contributes no rows anyway.

This was proved by the contrast case rather than assumed: 2011 match #68 *is* present and
carries `"result": "no result"`, because play did occur before abandonment. So absence
means zero balls, not missing data.

**Missing match numbers per season are recorded in `data/manifest.json`**, keyed to the
archive checksum they were computed from, so a future gap can be attributed to a revised
archive rather than mistaken for a failed download.

**4.7 Delivery numbering.** Wides and no-balls produce extra deliveries within an over.
Preserve the true sequence in `ball_no` and store a `legal_ball` flag so overs bowled
computes correctly.

**[A22] `legal_ball` is NOT the predicate for balls faced.** A no-ball *is* a ball faced —
the batter can and does score off it — but it is not a legal ball. A wide is neither. So:

| quantity | predicate |
|---|---|
| Balls faced, batting strike rate | `extra_wides = 0` |
| Overs bowled, bowling economy denominator | `legal_ball` |
| Runs conceded (A1) | `runs_batter + extra_wides + extra_noballs` |

**[Corrected 2026-07-30 — the earlier figure of 637 was wrong.]** This was found by
check 7, by hand, and nothing else in the suite could have found it. Gayle's 175* came out
at 65 balls against a published 66; the same one-ball gap appeared on Dilshan in the same
innings. Confirmed against three published 2016 season aggregates, where the two predicates
differ unmistakably: Kohli **973 off 640** (not 637), Warner **848 off 560** (not 558),
de Villiers **687 off 407** (not 405). Pinned by validation check 17.

Per A19 this adds no column — `extra_wides = 0` is already derivable from the extras split.
But the `legal_ball` column comment in migration `003` asserts the wrong rule and that
migration is applied, so it is immutable; the comment is corrected in `005` instead.

**[A19] Every delivery carries `actual_delivery`**, the scorecard ball reference such as
`"5.3"`. It is the *legal-ball* index, so it repeats on the re-bowl after a wide — 11,075
duplicates archive-wide — while `ball_no` counts physical deliveries.

It is **not stored**, because it is exactly derivable: verified equal to
`over.(legal balls so far, +1 if this one is illegal)` on all **295,732** deliveries, zero
mismatches. That makes it something better than a column — an independent check on the
wide and no-ball classification, from the source itself rather than from our own reading
of `extras`. Validation check 15.

Watch the trap: **powerplay bounds are positional, not legal-ball.** `powerplays[].to`
reaches `5.9` in the archive, which no legal-ball index can produce. Compare powerplay
membership against `ball_no`, never against `actual_delivery`.

**4.8 Phase definition. [A5]** Two distinct mechanisms, do not conflate them:

- *Display splits* (`deliveries.phase`): powerplay comes from the innings `powerplays`
  block when present. **Death = the final 25% of that innings' scheduled overs** (overs
  16–20 in a full match), derived per innings. Middle is the remainder.
- *State model* (§7.1): does **not** use these three buckets. See §7.1.

---

## 5. Schema

See `migrations/`. Notes on decisions that differ from a naive reading:

**[A1] Extras are five smallint columns**, not one `extra_type` text column:
`extra_wides, extra_noballs, extra_byes, extra_legbyes, extra_penalty`.

Two independent reasons. A delivery can be both a no-ball and concede byes, so one column
forces data loss. And more fundamentally, **wides and no-balls are charged to the bowler's
analysis while byes and legbyes are not** — a single column makes economy rate wrong.

**[A7 note] `appearances` carries two flags.** `named_in_squad` stores what Cricsheet
asserts in `info.players`, as-is. From 2023 the Impact Player rule means this can list 12
per side including players who never took the field. `participated` is derived separately
from actual batting, bowling or fielding involvement.

**[A16, counts corrected]** Deliveries carry a `replacements` field in two kinds.
**`match`** — the Impact Player — appears on **524 deliveries** carrying **563 entries**,
some deliveries recording two at once; the earlier figure of 523 conflated the two counts.
**`role`** — a substitute taking over a fielding or bowling role — appears on **61**.

Both feed the A4 batting-position rule, and both feed `participated`: **a substitute who
bowled is participation; a named-but-unused Impact Player is not.**

A `match` entry always names its `team`; a `role` entry never does and never needs to,
since taking over a bowling or fielding role places the player on the fielding side. The
team is therefore read, not inferred.

A player who takes the field only as a substitute fielder or an Impact Player gets an
`appearances` row with **`named_in_squad = false` and `participated = true`**, which is
the case the two flags exist to separate.

**[A13] `deliveries.innings_scheduled_balls`** — carried on every delivery so the
per-innings death boundary (§4.8) and the §7.1 full-length fitting filter are both
computable without re-deriving from the match. In balls, not overs, for the reason given
in §4.5.

Index `deliveries` on `(batter_id)`, `(bowler_id)`, `(match_id, innings_no)` and
`(phase)`. This table is the hot path.

Derived stat tables land at the end of the phase: `player_season_stats` and
`player_career_stats`, split by phase and overall.

- Batting: innings, runs, balls faced, dismissals, average, strike rate, boundary %,
  dot %, balls per dismissal
- Bowling: innings, balls bowled, runs conceded, wickets, economy, strike rate, dot %
- Splits against pace and against spin, once §6.3 lands

---

## 6. The three fields Cricsheet does not give us

Do not invent any of them.

**[A30] The override CSVs hold two kinds of column and they get opposite treatment.**
Exactly one column per file is the **decision** — `nationality`, `is_keeper`, `kept`,
`bowling_style` — and it is the only copy of work that cannot be recomputed. It is
carried across every regeneration untouched and is never written over, not even with an
identical value. Every other column is **evidence** we derived, and it is refreshed on
each run; otherwise a newly added signal could only ever reach rows that did not exist
yet, which defeats the point of adding one. `merge_override` takes the decision column
by name so the distinction is enforced rather than remembered. A row that stops being
derived is kept at the end of the file rather than dropped: it may hold a decision, and a
generator that can silently delete one eventually will.

Every row in all four files carries a **`cricinfo_id`**, joined from the Cricsheet people
register we already download. It fills nothing in. The slow part of answering these
questions is not the judgement but identifying the player — initials-only names are
ambiguous and several are shared outright — and the register resolves that for all 1,627
rows needing a human, with no gaps.

**6.1 Nationality and overseas status. [A3]** Build a person → national-team map from the
men's **Test, ODI and T20I** archives combined. T20I alone misses anyone whose caps
predate the format or came only in longer forms.

**[A23 — corrects A3, 2026-07-30] Anyone not found is NULL. There is no default.**

This section previously said an unobserved player defaults to Indian, on the reasoning
that uncapped IPL players are overwhelmingly domestic. **The reasoning was sound and the
rule was still wrong**, because it assumed the archive contains every cricketing nation.
It does not contain Afghanistan at all — not one match across Tests, ODIs and T20Is — and
the default turned that single gap into eight confidently false records: Mohammad Nabi,
Rashid Khan, Mujeeb Ur Rahman, Rahmanullah Gurbaz, Noor Ahmad, Naveen-ul-Haq, Fazalhaq
Farooqi and Karim Janat, each stored as Indian **and** as `is_overseas = false`. That
second column is the damaging one: it would have let a legal XI field five overseas
players while the four-overseas check reported compliance.

So: `nationality`, `nationality_source` and `is_overseas` all stay **NULL** until a human
fills in `etl/overrides/nationality.csv`. `nationality_source = 'default'` is never
written. `is_overseas` is a claim, and a claim may not be derived from a value that was
never observed.

> **General rule, and the reason this is written down.** **Never default an unobserved
> value to the majority class where a gap in the source can masquerade as a real value.**
> The majority default is exactly the shape of error that internal checks cannot catch:
> it is plausible, it is usually right, and it fails silently and in a body wherever the
> source is blind. If a field can be unobserved, it must be able to be NULL. This applies
> to every derived field in this document, not only nationality.

**Composite sides are not nationalities.** ICC World XI, World XI, Africa XI and Asia XI
are excluded before the most-frequent-team vote. Rashid Khan resolved to `ICC World XI`
on a single cap, which is how the missing nation surfaced at all: with Afghanistan gone,
that was his only appearance in any of the three archives.

**Coverage audit, run 2026-07-30.** The archives hold 110 team names. Of the twelve ICC
full members, **Afghanistan is the only one absent** — the other eleven appear in all
three formats. Netherlands, UAE, USA, Nepal, Scotland and Ireland are all present, so the
associate nations that actually supply IPL players are covered. Re-run this audit against
a fresh download before the deck is final; a second missing nation would be invisible
otherwise. `etl.derive_people --nationality` prints it.

**[A30] The Afghanistan gap is deliberate and permanent, so no download will close it.**
It reads like a truncated archive and is not one. The T20I zip's own `README.txt` says:
*"A further 158 matches have been withheld due to either featuring the Afghanistan men's
team or being played in the Afghanistan Premier League, due to the Cricsheet policy to no
longer feature matches involving Afghanistan men"* (`cricsheet.org/withheld-matches`).
The standing instruction to re-verify against a fresh download therefore does **not**
apply here — it will report the same absence forever, and `nationality.csv` must carry
every Afghan player by hand permanently. This is worth stating because the failure mode
is a future reader "fixing" the gap by re-downloading and concluding the audit is broken.

**There is no second source on Cricsheet.** All 1,115 published JSON archives were
scanned for a competition whose team membership is nationality-restricted — a Ranji,
Mushtaq Ali, County Championship or Sheffield Shield archive would prove an Indian or
English or Australian by positive observation. **None exists.** Cricsheet publishes
internationals and franchise T20 leagues, and a franchise league proves nothing: its
squads are deliberately mixed. Tests + ODIs + T20Is is the whole of the derivable
signal and it is already fully exploited. The residual 314 cannot be narrowed further by
any means available in this project; they can only be filled.

Measured 2026-07-30: 502 resolved from international caps, **314 unknown**. By IPL
footprint — matches actually participated in — the unknown ones are 13 with 50+, 61 with
20–49, 126 with 5–19, 112 with 1–4, and 2 who never took the field. **The 74 at 20
matches or more are the priority tier** for the override; Rashid Khan alone is 153
matches over 10 seasons and is overseas. The list needs **full review, not a spot check**, printed **sorted by matches
played descending** so the ones that matter surface first.

`etl/overrides/nationality.csv` is authoritative over the derivation.

**[A51] The 314 were filled by hand on 2026-07-31, and checking them found that the
*derived* half answers a different question from the one the draft asks.** Every figure
above stands as measured; what follows is what filling them exposed.

The override file is now complete — **816 people: 489 international, 327 override, 0
unknown** — and the game reports no uncertainty at all: over 400 drafted XIs, **313 legal,
0 uncertain, 87 with no legal XI**, against **127 / 179 / 94** when the file was empty.
The 87 are §1.1's missing nationality constraint (§10.4), not a gap in this section.

Three of the 314 were checked and rejected before loading, and the check that rejected them
is now **validation check 21**: the IPL has capped a playing XI at four overseas players in
every season since 2008, so replaying our nationality data against **2,486 real team
sheets** tests it against a rule the archive never states. `VS Malik` and `Shoaib Ahmed`
were entered as Pakistan but played in 2009 and later, and no Pakistani has played the IPL
since 2008; `AG Murtaza` was entered as Afghanistan but played 2010–2013, and the first
Afghan in the league was 2017. A fourth, `WA Mota`, was entered as Australia and sits in
**10 of his 12 XIs** as an illegal fifth overseas player. All four are recorded as India.

**The derived half has a systematic flaw of the same family as A23, running the other way.**
The archive vote resolves *which nation a player has ever represented*; the draft needs
*which nation he held that season*. They come apart for a player who emigrated after his
IPL career — `S Sohal` opened for Punjab from 2008 and later played T20Is for the United
States, so the vote marks him overseas for seasons in which he was domestic. A23 stopped an
unknown defaulting to domestic; this marks a genuine Indian overseas, which wrongly
constrains an XI rather than illegally permitting one. Safer direction, still wrong.

**RATIFIED: nationality is the one held DURING the IPL career, not the career vote.** A
player who was uncapped-domestic in an IPL season stays domestic for that season however
many caps he wins for another country afterwards. All 13 affected players were checked
against the public record and filled: eight resolve to India (Sohal, Akshdeep Nath, Sachin
Rana, Harpreet Singh Bhatia, Jaskaran Singh, Milind Kumar, Harmeet Singh (2), Tanmay
Mishra), three to South Africa (van der Merwe, Wiese, Theron — all capped by South Africa
while they played, and mislabelled by a later move), and two stand as genuine overseas
slots (ten Doeschate, Lamichhane).

Two are worth keeping for the reasoning rather than the answer. **Tanmay Mishra was a
capped Kenya international and still domestic**: Deccan signed him as a local player on his
Indian passport, so the question the draft asks is not "has he a cap" but "did he occupy an
overseas slot". **Sachin Rana was never a Seychelles player at all** — the vote had matched
a different Rana, so that one was a registry collision rather than an emigration.

**Check 21 also had a bug of its own, and it was inflating the alarm by a third.** It
counted everyone who *participated*, which includes a fielding substitute who took a catch
— someone never in the XI and never against the cap. On the named-XI basis the same data
read 63 illegal XIs, not 99. The Impact Player is still counted deliberately: from 2023 a
team names twelve, and four overseas in the XI obliges an Indian Impact Player, so four
across the twelve remains the right bound.

**Check 21 now PASSES: 0 illegal XIs of 2,486.** Nationality is complete and internally
consistent with the IPL's own rule — 816 people, 489 international, 327 override, 0 unknown.
Falsified rather than assumed: marking Kohli as Australian implicates 82 players, so the
zero is a measurement and not a check that stopped looking.

The generator emits **evidence and never a verdict** (A30), and the evidence has two
strengths that must not be confused. Sitting in an illegal XI is a *suspicion*: five
overseas players means one of the five is wrong and does not say which, so every genuine
overseas player in that XI is named alongside the culprit — 158 players over 99 XIs, mostly
Sangakkara and de Villiers. Four *other* players being overseas is a *proof*: the cap
leaves no room, so this player is domestic whatever any archive says. The proof is drawn
only from full-member labels and only from XIs already consistent, and both restrictions
are load-bearing — reading it inside a contradicted XI proves every player in that XI
domestic, Warner and Pollard included, which is how the first version was caught.

**6.2 Wicketkeepers.** Mine `wicket_kind = 'stumped'` for positive signals. That misses
keepers who never stumped anyone. Produce the derived list, then complete
`etl/overrides/keepers.csv` by hand (~50–70 players, tractable).

The keeper is the **fielder** on a stumping, not the bowler, and fielders are not stored
(A19), so this reads the archive. 388 stumpings prove **49** keepers.

**[A24] A career keeper flag cannot answer 6.6's "kept for that squad".**
`people.is_keeper` is a career fact; using it alone made a keeper of every squad the
player ever appeared in, including seasons they played purely as a batter, and gave
exactly one keeper to only **41 of 166** franchise-seasons. Attributing each stumping to
the franchise-season it happened in gives **116 with exactly one, 24 with two** (real —
squads do rotate keepers) and **26 with none**, those being squads that took no stumping
all season. The per-squad answer lives in `etl/overrides/keepers_by_season.csv`, keyed on
`(franchise_season_id, person_id)`, pre-filled `y` where a stumping proves it and blank
where it does not. **Blank is undecided, and undecided is not a yes** — an unproved squad
gets no keeper rather than a guessed one.

**[A52] The 26 were CLOSED on 2026-07-31, and the diagnosis is worth more than the fix: a
stumping is PROOF of a keeper, not the DEFINITION of one.** Every one of the 26 had the
same cause — **zero stumpings all season**. With 388 stumpings over 166 franchise-seasons,
about 2.3 each, a season with none is ordinary luck rather than missing data, so the rule
was working exactly as designed and simply had nothing to work with. The squads had keepers
throughout: 2008 Chennai had Dhoni, 2022 Mumbai had Ishan Kishan, 2024 Kolkata had Phil
Salt. They were resolved from the public record, which is **the only place the answer
exists** — Cricsheet carries no keeper flag (A30 below) and no internal signal can close
it. 29 rows across 26 squads, three of which genuinely shared the gloves (2008 Chennai
Dhoni and Parthiv Patel, 2009 Kolkata van Wyk and Saha, 2025 Hyderabad Klaasen and Kishan),
which A24 already permits. **Check 19 now passes at 166 of 166** — 139 squads with one
keeper, 27 with two — and check 12's thinnest slot moves from 126 to 150 of 166.

**[A30] Catches rank the candidates. They may not pick one.** The 26 unproved squads
were the case for mining catches behind the stumps, filed earlier as the better keeper
signal. It was tested before it was trusted, against the 140 squad-seasons a stumping
already settles, and it does not survive the test:

| Signal | Measured against 140 proven squad-seasons |
|---|---|
| Top catch-taker who bowled no ball that season | **72/140 — 51%**, a coin flip |
| One of the **top three** such catch-takers | 121/140 — 86% |
| Career keeper in the squad, proved in another season, leave-one-season-out | names a **unique** candidate for only 39, and is **wrong for 5 of those 39** |

The last row disqualifies the tempting rule. Its errors are the A23 shape exactly — it
picks de Villiers over KS Bharat for 2021 RCB, Bairstow over Rickelton for 2025 MI, KL
Rahul over Stubbs for 2025 DC. **A famous keeper playing a season as a pure batter is
indistinguishable from the man who actually kept**, and the wrong answer is the plausible
one. A 13% error rate on the cases it is confident about is worse than 26 honest blanks.

The archive cannot break the tie either: Cricsheet's `fielders[]` carries only `name` and
`substitute`. There is **no keeper flag**, so a catch behind the stumps is not
distinguishable from one at slip. The signal was never as strong as it was filed as.

So `keepers_by_season.csv` carries the evidence and withholds the verdict. Each squad
lists everyone a stumping proved, plus every career keeper in it, plus its top **3**
non-bowling catch-takers, ordered most-likely first, with `matches`, `catches`,
`catch_rank`, `balls_bowled` and `keeper_elsewhere` alongside. 2011 Delhi is the worked
example of why the ranking is not the answer: Sehwag ranks first on catches and Ojha,
who kept, is second — visible only because `keeper_elsewhere` is on his row too.

**Neither is this a schema question.** The fallback assumed storing fielder ids, a
migration and a full reload. It needed none: keeper derivation already reads the archive
directly (A19), so catches were measured for free and A19 stands unchanged.

**6.3 Pace versus spin.** Not derivable from the data at all. Generate
`etl/overrides/bowling_style.csv` pre-populated with every person who has bowled ≥30 legal
deliveries, style column blank. **The CSV is the only source of truth.** Until filled,
`bowling_style` stays NULL and pace/spin splits return NULL, never a guess.

**[A30]** This is the one of the four files that no derivation can narrow even partly.
Cricsheet records no bowling action anywhere, and nothing in the ball-by-ball data
implies one — economy and phase usage correlate with pace or spin without ever
determining it, and a correlation is not a value. It is also the least urgent: per A8
pace and spin slots collapse to a generic bowler slot of 5, so unlike the keeper file
this one does not gate a legal draft.

**6.4 Batting position.** Derivable. Within each innings, the two batters on the first
delivery occupy positions 1 and 2. Every subsequent batter takes their position from the
order in which they first appear at the crease.

**[A4]** Scan **both** the `batter` and `non_striker` fields — a batter arriving after a
last-ball-of-the-over dismissal appears as non-striker first. Players who leave and return
(**retired hurt, retired out, concussion substitutes, and Impact Player entrants**) keep
their original position and do not take a new one.

Take each player's **modal** position per franchise-season, and store the spread.

**Canonical bands:**

| Band | Positions |
|---|---|
| `opener` | 1–2 |
| `top_order` | 3–4 |
| `middle` | 5–6 |
| `finisher` | 7–8 |
| `tail` | 9–11 |

**6.5 Bowling usage phase.** Derive from where a bowler's legal deliveries actually came:
`powerplay`, `middle`, `death`, or `mixed` when no phase dominates. Store per
franchise-season. Dominance is **≥ 50%** of that bowler's classifiable legal deliveries.

**[A25] Two phases level on the same count is `mixed`, whatever the threshold says.**
With three phases a tie for the lead can only be 50/50, which passes a ≥ 50% test — so
the naive rule names a phase, and which one it names is decided by whatever the tie-break
happens to be. That is a coin toss dressed up as a finding. A tie means the bowler split
their work, and `mixed` is what that is called.

A delivery with a NULL phase is unknown and is left out of the denominator rather than
counted anywhere. A bowler with no classifiable delivery gets NULL, never `mixed`:
`mixed` is a finding about a bowler, not a place to put missing data. One bowler in the
archive is in this position.

**6.6 Roles.** Derive per franchise-season, not per career:

- `keeper` if flagged as a keeper and they kept for that squad (A24)
- `bowler` if they bowled a meaningful share of team overs and faced few balls
- `batter` if they faced a meaningful share of balls and bowled little
- `allrounder` if both

Thresholds are named constants at the top of the file. Show the resulting distribution.
Something like 40% all-rounders means the thresholds are wrong. The slot template depends
on this being sane.

**[A26] The thresholds are asymmetric, and calibrated rather than chosen.** A team faces
about 120 balls over eleven batters and bowls 120 over five or six bowlers, so the median
squad member faces ~6 balls a match and bowls ~12. One symmetric cutoff either calls half
the deck all-rounders or almost none: 12 balls/match both ways gave 2.9% all-rounders, 6
gave 12.6% *plus* a 5.9% bucket that was neither. `BAT_MIN = 9.0` balls faced per match
(A22 predicate: excludes wides only) and `BOWL_MIN = 6.0` legal balls bowled per match —
one over a match — are the values that classify all twelve players in `etl/roles.py`'s
calibration table correctly. The window is narrow enough to be falsifiable: `BAT_MIN`
must be ≤ 10.5 to keep Jadeja an all-rounder and > 5.4 to keep Narine a bowler;
`BOWL_MIN` must be ≤ 7.8 to keep Pollard an all-rounder and > 0.9 to keep Kohli a batter.

Below **both** thresholds but not idle, the larger workload *relative to its own
threshold* wins, so the asymmetry is not quietly reintroduced by comparing raw counts.

**[A27] A player who neither batted nor bowled gets no row.** `squad_members.role` is
`NOT NULL` and fielding a dismissal says nothing about whether someone is a batter or a
bowler, so there is no honest role to assign. **47** of 3,384 (franchise-season, person)
pairs are in this position and are excluded, leaving **3,337** rows.

Measured 2026-07-30: bowler 49.8%, batter 36.8%, all-rounder 8.5%, keeper 4.9%. 483 rows
(14.5%) had a tied modal position, broken towards the lower number; 352 never batted.

**[A75, 2026-08-03] The API's card `kind` (the icon a drafter sees) now reads this `role`
directly, and did not always.** It used to be re-derived in `web/app._kind` from whether
each discipline carried any rating at all — which stopped tracking real workload the
moment A65 (§7.9) removed the volume floor everywhere, and tagged Bumrah/Hazlewood/
Cummins-type seasons `allrounder` off a token ball faced. See CLAUDE.md's A75 for the
measurement (1,816 wrongly tagged down to the correct 284). No schema change: this table
was always right, only a display-layer copy of the question had drifted from it.

---

## 7. The rating model

A rating attaches to a **franchise-season**, not a person. It reflects that IPL season
only — not career, not international form, not other T20 leagues. Kohli RCB 2016 and Kohli
RCB 2022 are separate entries carrying very different numbers, and that contrast is the
point of the game.

**7.1 Build the state model first.** Ratings are not computed from averages or strike
rates. Both are unusable across batting positions: a number 5 faces fewer balls, bats
mostly at the death where dismissal is cheap and scoring is forced, and accumulates
not-outs that corrupt their average.

**[A5] State definition:** exact `over_no` crossed with **wickets bucketed 0–1, 2–3, 4–5,
6+**. Not the three display phases.

**[A15] Fitting set: first innings only, reduced matches excluded, miscounted overs
excluded.**

Over numbers are not comparable across innings of different lengths, so reduced innings
cannot enter the fit. But the stronger reason is that **second innings are not the same
scheduling context as first innings even when both are 20 overs:**

- A chase **truncates on success**, which correlates with the batting side doing well.
  Expected runs remaining is therefore biased downward.
- Behaviour at a given over and wicket state depends on the **required rate**, which has
  no first-innings equivalent. A batter blocking out a won chase and a batter slogging a
  lost one both look bad against a neutral baseline.

**[A17] The fitting filter is a positive test, not the absence of a reduction:**

```sql
innings_no = 1 and not is_super_over and not over_miscounted
  and innings_scheduled_balls = 120        -- not: and not was_reduced
```

`was_reduced` alone leaks. Three matches — `1473495`, `1527685`, `501265` — were abandoned
during the first innings with no second innings at all, so no `target.overs` exists to mark
them reduced, and their scheduled length is unknown. `was_reduced` is false for all three
and 148 deliveries of unknown-length innings would have entered the fit.

Testing for a known 120 closes it by construction. **A null can never satisfy an equality,
so unknown scheduled length falls out of the fitting set automatically** rather than
depending on anyone remembering to exclude it.

**[A28, ratified 2026-07-30] `= 120` is the whole filter. `not was_reduced` is gone.**
Carrying both was the cautious reading, and it excluded 12 first innings that were
themselves scheduled for and played to a full twenty overs — the reduction in those
matches fell on the chase only, after the first innings was complete. A full-length
innings is a full-length scheduling context whatever else happened later in the match,
and what happens after an innings ends cannot change the run environment it was played
in. Excluding them discarded real observations to no purpose.

That takes the fitting set from 149,697 to **151,175 deliveries** (+1,478 from those 12
matches) across 20 overs × 4 wicket buckets — about **1,890 per cell**. The earlier
~145,000 was an estimate; both later figures are counted.

**Scoring, as distinct from fitting:**

| | Fitted on | Scored against it | In display stats |
|---|---|---|---|
| First innings, full-length | yes | yes | yes |
| Second innings, full-length | **no** | yes | yes |
| Any reduced-match delivery | no | **no** | **yes** |
| Miscounted overs | no | yes | yes |
| **[A17] Unknown scheduled length** | **no** | **no** | **yes** |

**[A17] The 6 innings of unknown scheduled length reach display statistics and nothing
else.** A player's card shows the runs they really scored in an abandoned game; no rating,
no state-model cell and no aggregate ever treats the unknown length as twenty overs. The
`= 120` filter above is what enforces it, and validation check 16 asserts the count is
still exactly 6.

**Reduced-match deliveries are excluded from rating computation entirely but still count
in display statistics.** A player's card shows their real runs and wickets; the rating
model does not need the 2.7%, and mis-pricing a 9-over game is worse than dropping it.

**Do not pre-emptively build a second-innings state table.** Validation check 14 is the
trigger — build it only if the diagnostic fires.

For each state compute:
- Expected runs off the bat per ball
- Dismissal probability per ball
- Expected runs remaining in the innings, at that state and at that state with one more
  wicket down.

**[A31, corrected 2026-07-30] The cost of a wicket is the drop in expected FINAL total, not
the difference in expected runs remaining.** This paragraph previously read "the difference
is the cost of a wicket there", pointing at runs remaining. That is wrong, and wrong in a
direction no internal check would catch, so the old claim is named here rather than quietly
replaced.

*The rule, stated positively.* Wicket cost at (over, wickets) is

```
E[final total | over, wickets]  -  E[final total | over, wickets + 1]
```

where expected final total is runs already scored plus expected runs remaining. Measured
this way it is **strictly monotone in wickets at every over, with no inversions**:

| over | 0 down | 1 down | 2 down | 3 down | 4 down |
|---|---|---|---|---|---|
| 4 | 182.0 | 170.2 | 156.9 | 142.3 | 118.6 |
| 8 | 190.6 | 182.3 | 169.3 | 156.5 | 139.1 |
| 12 | 199.4 | 194.4 | 180.2 | 169.1 | 156.5 |
| 16 | 215.1 | 204.1 | 192.0 | 183.9 | 170.9 |

*Why differencing runs remaining fails.* A team 4 down in over 12 is not a team that was 3
down and lost one. It is disproportionately a team being bowled at well, or one taking risks
because it has to, so the two states are not comparable and the difference between them
measures that selection as much as it measures the wicket. The confounding is strong enough
to flip the sign: over 12 at 0 down prices a wicket at **−2.0 runs**, over 18 at 2 down at
−0.0, and three cells came out indistinguishable from zero at two standard errors.
`etl.state_model` still prints that column, labelled as confounded, because the printout is
how the problem was found and the fastest way to see it again.

**Known limitation, deliberately accepted for phase 1.** The monotone version is still a
descriptive conditional expectation, not a causal one — the same selection sits inside it,
just not strongly enough to invert anything. That is acceptable because the rating is a
comparative yardstick and every player is priced against the same one. **If a rating later
looks wrong for a player who batted mostly in collapse situations, look here first,** before
suspecting shrinkage or the cohort offsets.

A batter's contribution on a delivery is the actual outcome minus the expected outcome for
that exact state, with dismissals priced by the wicket cost above. Sum across a season,
divide by balls faced for a per-ball impact figure. Same for bowlers with signs reversed.

**[A36, ratified 2026-07-30] Batting is charged only the striker's own dismissal.** The
state model's `dismissals` column counts *any* wicket, so its rate is P(a wicket falls) —
which prices 319 non-striker run outs (4.28% of fitting-set dismissals) to the player who
did not cause them. Migration 009 therefore stores a **striker-only** grid,
`player_out_id = batter_id`, and ratings consume that. The any-wicket reading stays fitted
and printed **as a diagnostic only, clearly labelled, consumed by nothing.** In aggregate
the choice is worth 0.001 runs per ball, which is the argument for not caring; per player it
reaches **0.193** and moves top-20 membership by two player-seasons, which is the argument
for caring, and in a game where players read individual ratings closely the
defensible-per-player version beats the convenient-in-aggregate one. Bowling is charged on
`credited_to_bowler` — bowled, caught, lbw, stumped, hit wicket, caught and bowled — which
excludes run outs on the same principle.

**[A37, ratified 2026-07-30] One state-resolution rule, both halves of the impact.** A
ball's impact has a runs half and a wicket half, and each needs a reference state. Where the
exact state is too thin to trust (12 of the 75 observed cells sit under 100 balls, and 5
have never happened), **both halves fall back the same way: to the nearest state in the same
over along the wicket axis.** `Grid` walks it at bucketed-wicket grain and `Costs` at
exact-wicket grain — the A31 split — but it is the same walk. This is not tidiness. Two
halves resolving differently would price one ball against two different reference states.

*The bug this fixes.* Wicket cost already fell back; the runs baseline did not, and simply
**dropped** any ball in a thin cell — 867 balls, 0.31%. That is not a rounding issue and not
neutral: thin cells are early collapses and death overs, so the drop biased against exactly
the players who batted in them. It surfaced as Kohli's 2016 reading **838 off 583** against
a real **860 off 590**. Under A37 nothing is dropped, every scoring-set ball is priced,
0.31% of batting balls and 0.33% of bowling balls resolve to a neighbour, and all 19 of
Kohli's seasons reconcile exactly against the scoring set. `etl.impact --reconcile` is that
check and it is the only place a silent drop would ever show.

**A37 has no schema representation and never will.** It is a property of a walk, not of a
column, so no CHECK can hold it and a reader of the migrations would not know it exists.
It is pinned instead by `tests/test_impact.py`, which asserts the walk stays inside the
over, picks the nearest trustworthy bucket, counts every fallback, refuses to resolve a
healthy cell, and exits rather than dropping an unpriceable ball. Each of those was
verified to fail with the corresponding line broken.

**What was actually fitted, measured 2026-07-30.** 151,175 deliveries = **146,159 balls
faced** (A22: `extra_wides = 0`) + 5,016 wides, reconciling exactly with A28. 196,068 runs
off the bat and 7,458 dismissals.

- **Coverage is 63 of 80 states.** 75 observed, 12 under 100 balls, and **5 that have never
  happened** — 4 down in over 0 and its neighbours. All 17 weak states sit in one corner,
  early-over collapses, plus `ov19 0-1`, the innings that loses almost nobody. Their
  thinness reflects how rarely cricket reaches them, not a sampling failure.
- **[A31] Thin states are stored raw with their `n` and shrunk by the consumer, never
  smoothed at rest.** How to handle a state with n = 40 is a simulator decision, to be made
  with the simulator in front of us. The table must not pre-empt it.
- **The archive produced the powerplay unprompted.** Scoring climbs 0.915 → 1.480 runs/ball
  across overs 0–5, **drops to 1.166 at over 6** as fielding restrictions lift, then climbs
  to 2.157 by over 18. Dismissal probability runs 0.029 (over 0) to 0.156 (over 19, 6+
  down). Nothing in the fit knows about over 6; the discontinuity is the data agreeing with
  the laws of the game, which is the closest thing to an external check available here.
- **Seven outcomes cover 100% of the fit**: 0,1,2,3,4,5,6 off the bat. 5s happen (32, off
  overthrows), 7s do not. That is a measurement of this archive, not a law, so it is guarded
  twice — see A31.

**[A22] The two denominators are different predicates and must not be shared.** Balls faced
is `extra_wides = 0`; legal balls bowled is `legal_ball`. A no-ball sits in the first and
not the second. Reusing `legal_ball` for the batting denominator inflates every batting
rating by roughly the no-ball rate.

This prices context automatically. A dot in the 19th over with four down is barely a
failure; a dot in the 8th with one down is worse. Thirty off 12 at the death is worth far
more than thirty off 30 in the middle. The finisher stops being punished for the situation
they were handed.

**7.2 Shrinkage is mandatory.** A season is ~14 matches; some players face 300 balls, some
face 11. Naively computed, the top-rated batters will be fringe players who hit two sixes
in one cameo. Apply empirical Bayes shrinkage toward a prior in proportion to sample size:

- Prior is the player's own career mean where they have meaningful career volume,
  **[A7] computed leave-one-season-out.** A season shrinking toward a mean containing
  itself would weaken shrinkage by exactly the amount that matters most for high-volume
  players.
- Prior falls back to the league-season mean **for their position band or bowling usage
  phase**, never the undifferentiated mean. Shrinking a number 6 toward the all-batters
  average drags every finisher toward top-order norms and underrates the whole class.
- Weight by balls faced (batting) or legal balls bowled (bowling).

The shrinkage constant is a named tunable, calibrated against the leaderboards it produces.

**[A32, ratified 2026-07-30] Shrinkage cannot carry the low-volume problem, and this
paragraph used to imply it could.** The sentence above — "the top-rated batters will be
fringe players who hit two sixes in one cameo" — named shrinkage as the fix. It is not one.
Shrinkage pulls a season toward a prior in proportion to sample size, and for a thin season
the own-career prior *is mostly that same thin season*, so no value of the constant helps:
raising it makes the season climb. Measured, with no career-volume floor, BA Bhatt's 5-ball
2011 rises from +2.003 at k = 50 to +2.206 at k = 800, because his prior is the other 2
balls of his career. **Shrinkage protects against a thin career. It never protects against a
thin season.** The clause "where they have meaningful career volume" above is what makes the
first half true and it is load-bearing; `CAREER_FLOOR` is 300 balls.

*The rule: two gates, not one.* §7.3's match gate stays, and a **balls floor** joins it.

**7.3 Draftability gates. [A33, ratified 2026-07-30]** Two, applied together. Both stored as
named constants.

| Gate | Value | Why this value |
|---|---|---|
| Matches played | **4** | SPEC's original, provisional and unratified |
| Balls faced (batting) | **100** | where the raw batting list stops moving — measured |
| Legal balls bowled (bowling) | **150** | where the raw bowling list stops moving — measured |

**Both floors are empirically derived from where each raw list stabilises. Neither is a round
number chosen for tidiness**, and the fact that they *look* round is a coincidence of the
sweep grid, not a reason to trust them less or to round the next one. The sweep below is the
evidence; if it is ever re-run on a revised archive and the stabilisation point moves, the
constants move with it.

**[Superseded 2026-08-01 by A65/A71 — this section originally said a player below a floor
was "not rated at all" and "not draftable from that franchise-season." That was true through
migration 012 and is no longer true.]** Below a floor a player-season is not *precisely*
estimable — there is not enough signal for its own per-ball figure to mean much — but A65
removed the consequence rather than the measurement: the gate still records
`not_rateable_reason` (this section's arithmetic is exactly how that column is filled), but a
thin season is now rated from its prior/reputation instead of being dropped from the view. Of
3,337 squad members, only 4 have literally zero balls in either discipline; A71 rates even
those, from the player's own career or (with no career anywhere) a shared scale floor. Every
squad member is draftable today. See CLAUDE.md's decisions log, A65–A71, for the full
correction — it is not repeated section by section here.

**The floors are measured, not assumed, and they are not the same number.** Reading the top
5 of the raw list at each candidate cut, batting settles at 100 and bowling does not:

| floor | batting top | bowling top |
|---|---|---|
| none | 1–2 ball seasons at +3.34 | 1–4 ball seasons at +7.28 |
| 30 | 73b **+1.36** | 30b **+1.48** |
| 50 | 73b **+1.36** | 96b **+1.11** |
| **100** | 141b **+0.99** | 126b **+0.87** |
| **150** | 313b +0.97 | 360b **+0.76** |
| 200 | 313b +0.97 | 360b +0.76 |

Batting's top stops moving between 100 and 150 (+0.99 → +0.97). Bowling's is still falling
13% over the same step (+0.87 → +0.76) and only stops at 150. **Do not transfer one floor
across disciplines**: a bowler is capped at 24 legal balls per match, so 100 balls is four
matches of a frontline bowler but a whole season of a part-timer, and at a 100-ball bowling
floor the top twelve contains RG Sharma's 138-ball 2009 — a batter who bowled a bit. 150
removes it. The bowling floor implies roughly seven matches, which makes the four-match gate
**non-binding for bowling**; it still binds for batting.

Effect: **1,027 of 2,954** batting player-seasons and **787 of 2,195** bowling player-seasons
are rateable; 123 in both. 1,642 player-seasons are rateable in neither, 802 of those past
the match gate.

**Both gates count the same matches: the scoring set.** The match gate could read either
`squad_members.matches_played` (every match the player appeared in) or the number of distinct
matches that contributed balls to the impact total (the §7.1 fitting-set filter — first
innings, full-length, not miscounted). They disagree on **890 of 3,333** player-seasons, by
up to **7** matches, and that gap flips **61** seasons across the four-match gate. The
scoring-set count is the correct denominator and not merely the convenient one: a gate that
answers "is there enough evidence to rate this season?" must count the evidence the rating
was actually computed from. Counting matches that contributed zero balls to the number lets a
season claim support it does not have. This was found by the standing reconciliation check —
the rule and the CHECK that enforces it were being written in the same pass, which is
precisely when the check gets skipped.

*Confirmed after the load:* all 61 newly-gated seasons are genuinely marginal — at most 63
balls faced and at most 72 balls bowled — and **0 of 61 would have cleared a balls floor
anyway**. The tightening is right in principle and inert in effect, which is the outcome to
want: it closes a hole without moving a single rating.

**[A34] Known residual, accepted and flagged.** The floors remove the pathology in the form
that motivated them — a thin season outranking the same player's own thick one, and a
47-ball season reaching the top eight — but not entirely. Under the ratified k,
Suryavanshi's 122-ball 2025 (raw +0.574) shrinks **up** to +0.754 and outranks Sehwag's
240-ball 2011 (raw +0.615, shrunk +0.429), because its leave-one-season-out prior is his own
outstanding 2026. Across all rateable batting seasons **4.56% of within-band pairs are
strictly-dominated inversions** — fewer balls *and* a lower raw figure, yet a higher rating.
This is empirical Bayes working as designed, but it imports career information into a number
§7 opens by defining as season-only. Dropping the own-career prior for the band-season mean
alone takes it to 3.71% and returns 80% of Kohli's raw spread instead of 68%, at the cost of
A7.

**Explicitly provisional, and deliberately not fixed now.** The obvious repair — drop the
own-career prior — means calibrating a new prior against pre-normalisation numbers, and §7.4
changes exactly the baseline the prior is measured against. Tuning it twice risks landing on
a value that was right for the wrong baseline. **A34 and A35 are the same decision viewed
twice** — how hard to pull a thin season toward a prior, and which prior to pull it toward —
so they are decided together, in one pass, after §7.4, against normalised numbers. Neither
may be ratified alone.

**[A46, RESOLVED 2026-07-30 — A34 and A35 closed together, post-§7.4. The prior is the
cohort-season mean, and `k` stays 100.] This reverses A7.**

Swept both priors across six constants against normalised numbers
(`uv run python -m etl.impact --calibrate`). The cohort-season prior beat leave-one-season-
out on **all three** criteria at **every** k — it is not a trade:

| prior | k | Kohli spread retained | thinnest season in the top 5 | dominated inversions |
|---|---|---|---|---|
| loso | 50 | 65% | 141 | 9.36% |
| loso | **100** | 56% | **122** | 10.97% |
| loso | 200 | 46% | 122 | 15.09% |
| band | 50 | **71%** | **141** | 9.24% |
| band | **100** | **63%** | **249** | **9.87%** |
| band | 200 | 55% | 180 | 11.15% |
| band | 300 | 50% | **104** | 12.55% |

`band` + `k = 100` is the pick: the highest retained spread among the rows that keep every
sub-150-ball season out of the top five, and the lowest inversion rate among those. **`k`
therefore does not move** — normalisation did not buy a lower constant, which was the open
possibility A35 named. k = 50 retains more spread but floats a 141-ball season into the top
five, and k ≥ 300 lets a 104-ball season in.

**Why the own-career prior was the wrong one, stated as a mechanism rather than a
measurement.** Shrinking a player toward their own career mean pulls every one of their
seasons toward **the same point**, which is precisely the within-player contrast §7 opens by
calling the point of the game. Shrinking toward the cohort pulls all players toward a
*common* point instead, so a player's seasons keep their distance from one another. A7
reasoned that a player is their own best comparison — true of the **level**, and exactly
wrong for the **spread**. A7's protection is not lost, only relocated: A33's balls floor is
what stops a thin season being rated at all, and it does that job without touching spread.

*One caveat on the inversion figures.* A34's 4.56%/3.71% were measured pre-normalisation
over a pair set that is not reconstructed here; the table above counts every strictly-
dominating within-cohort pair on normalised numbers, so the **levels are not comparable to
A34's** and only the ordering (band below loso) reproduces. Stated rather than quietly
presented as the same measurement.

*Result.* Suryavanshi's 122-ball 2025 leaves the top five entirely; the thinnest of the top
five is Russell 2019 at **249 balls**. Kohli runs **2016: 75** (his best) down to **2022: 30**
and **2008: 30** on the display scale, a 45.7-point spread — the contrast the deck is built
on, intact.

**[A35, ratified 2026-07-30] The shrinkage constant is `k = 100` balls, provisionally.**
`k >= 400` is ruled out on design grounds before any statistics: §7 opens by saying the
season-to-season contrast "is the point of the game", and k = 400 retains 38% of Kohli's
best-to-worst spread while k = 800 retains 23% and turns his genuinely poor 2008 *positive*.

| | raw | k=50 | k=100 | k=200 | k=400 | k=800 |
|---|---|---|---|---|---|---|
| Kohli 2016 | +0.332 | +0.315 | **+0.300** | +0.275 | +0.241 | +0.203 |
| Kohli 2022 | −0.259 | −0.200 | **−0.157** | −0.096 | −0.027 | +0.036 |
| Kohli 2008 | −0.377 | −0.252 | **−0.175** | −0.087 | −0.006 | **+0.054** |
| spread retained | 0.897 | 81% | **68%** | 53% | 38% | 23% |

k = 50 and k = 100 both pass the within-player ordering test; k = 100 pulls harder on the
noise and that is the trade taken. **Not settled.** It must be re-checked after §7.4's
within-season normalisation, against normalised numbers, which may permit a lower value —
**in the same pass that decides A34**, per the note above.

`k` lives in exactly one place: the `player_season_rating` view. It is **not** a stored
column on `player_season_impact`, because a stored shrunk value and a stored `k` are two
copies of one fact and would drift the moment the constant is revisited — which it is
guaranteed to be, post-§7.4. The table stores only the inputs the formula names
(`impact_total` as a **sum**, `balls`, `prior_per_ball`); re-tuning `k` is a view replacement
and no reload. One view, single source, no exceptions.

**[A35, continued] What Kohli can and cannot calibrate right now.** His 2016 reads as his *third* best
season (+0.332) behind 2026 (+0.520) and 2024 (+0.414). That is not the model disagreeing
with the record — the state baseline is fitted pooled across all 19 seasons, and the league
mean impact runs **−0.188 (2009) to +0.205 (2026)**, so recent seasons beat a pooled baseline
by construction. §7.4's within-season normalisation is exactly what removes it. **Absolute
cross-season ordering therefore cannot calibrate the shrinkage constant until §7.4 lands.**
Within-player spread and ordering can, and are what the `k` table above used.

**Storage, migration 009, applied 2026-07-30.** One table at **long grain** —
(`franchise_season_id`, `person_id`, `discipline`) — not one wide row per player-season with
batting and bowling columns side by side. Measured rather than assumed: 5,149 long rows
against 3,333 wide ones, and the wide form would carry a NULL batting or bowling half on
2,132 of them, because most player-seasons are one discipline only. A discipline that does
not exist and a discipline that scored zero are different facts and a NULL column cannot
say which. Long grain also lets the two floors, which are different numbers per A33, sit in
the same column.

**Counts and inputs only, never a rating** (A19). The table stores `impact_total` as a
**sum**, `balls`, `matches` (scoring-set), the prior and its provenance, and both gate
constants as they stood at load time; the rating is the `player_season_rating` view. Storing
the constants beside the result is what makes `not_rateable_reason` checkable — the CHECK
recomputes the reason from `matches`, `balls`, `gate_matches` and `floor_balls`, so a loader
that gates on one rule and writes another is refused by the database rather than believed.
Reasons after the load: `balls` 2,086, NULL 1,812, `both` 1,249, `matches` 2. **The two
where the match gate binds alone are Symonds 2008 (105 balls, 3 matches) and Hussey 2008
(100 balls, 3 matches)** — both verified to have identical all-match and scoring-set counts,
so the gate is catching "many balls, very few matches" on its own merits and is not an
artefact of the denominator choice.

**7.4 Normalise within season and within cohort. [A2]**

*Season:* IPL scoring rates have risen substantially since 2008. An absolute scale makes
recent seasons strictly better and nobody will ever want a 2009 squad, which destroys the
deck's variety.

*Cohort:* score openers against openers, finishers against finishers, death bowlers
against death bowlers. Never against the whole population.

**Method:** normalise within season globally, then apply **cohort offsets estimated pooled
across all seasons** (partial pooling). Do **not** z-score independently within each
(season × cohort) cell — cells like "tail batters, 2011" may hold four players after the
§7.3 gate, and standardising four observations produces noise. Measured: 20 of the 75
batting (season × cohort) cells hold fewer than ten players and the smallest holds one.

**[A41, 2026-07-30] The cohort correction is a small residual, and the reason this section
originally gave for it was wrong.** This paragraph used to read *"a great finisher should
surface at 92 as a finisher, not be compressed to 61 for facing fewer balls than an
opener"* — and that describes **raw strike rates**, where a finisher genuinely does look
worse than an opener. It is not true of this rating. Impact is scored per ball against the
exact (over, wickets) state, so **the state model has already made the situational
correction before cohorts are reached**; by the time §7.4 runs, the thing the cohort offset
was invented to fix is gone. Measured, after within-season centring:

| batting | offset | | bowling | offset |
|---|---|---|---|---|
| opener | −0.016 | | middle | +0.011 |
| top_order | −0.008 | | mixed | −0.011 |
| middle | **+0.033** | | powerplay | −0.004 |
| finisher | **+0.019** | | death | +0.002 |

Finishers score **above** average per ball, not below. The entire cohort spread is 0.049
runs/ball against a within-cohort SD of 0.185, and against a season drift of 0.25 — **five
times the whole cohort effect**. The offsets are kept, because they are cheap and still
move 27 ranks of 1,025, but they are a residual correction applied *after* the expensive
work, not a large correction for facing fewer balls. **The old framing is deleted rather
than softened: a reader acting on it would over-correct by roughly a factor of five.**

**[A42] Centre within season. Do not divide by the season's standard deviation.** The level
drift is real and large — batting runs +0.007 (2008) to +0.257 (2026), bowling mirrors it
from +0.083 to −0.111. The *spread* drift is not real: season SDs range 0.136–0.216, but
only **1 of 19** seasons per discipline sits more than two sampling standard errors from
the pooled SD, so the variation is estimation noise off ~50 observations. Dividing by it
reorders **564 of 1,025** batting seasons by more than ten places on nothing at all. This
is A2's argument against per-cell z-scoring one level up, and it holds at both levels: do
not normalise by a quantity you cannot estimate stably.

The season mean is **unweighted** — one entry per player-season, not per ball. The rating
is a statement about players, so the reference population is players. Ball-weighting would
let a handful of high-volume players define the baseline everyone else is measured against,
tilting the reference toward exactly the players who least need correcting. The two differ
by about 0.03; unweighted is the principled choice and not merely the close one.

**[A43] A cohort below the evidence threshold gets zero, and that is not the same statement
as measuring zero.** `death` holds 8 rateable seasons over 7 at +0.002 ± 0.125 — a coin
flip near zero, and fitting it would be fitting noise. `tail` holds none at all (A39). Both
are **zero-by-insufficient-evidence**, and the distinction is kept legible by expressing it
as a **threshold (≥ 20 observations) rather than a hardcoded list of two cohort names**: a
name list would silently go stale if a revised archive gave `death` forty seasons. The
threshold sits in a wide empty gap — `death` has 8, the next smallest cohort has 45 — so
any value in (8, 45] behaves identically today and it is a rule about evidence, the same
shape as A33's floors, not a tuned constant.

**[A44] The 0–100 display scale is LINEAR on the normalised value, globally anchored.**

Percentile is more legible one number at a time and destroys the only thing that makes the
ratings worth building — **the gaps**. Bumrah 2020 standing clear of the field is real
information and is the whole reason a knowledgeable drafter reaches for him; percentile
flattens that into "near the top, like several others". Worse, under percentile two players
a hair apart and two a chasm apart show the same gap, so **the drafter cannot see which
pick is the real edge**.

One anchor for the whole deck: a single SD across both disciplines, every season and every
cohort. Rescaling per cohort or per season would break the one thing a display number is
for — a 92 has to mean the same thing on every card. Batting's SD is 0.1838 and bowling's
0.1766, a 4% difference, so sharing costs almost nothing and buys the comparison a drafter
actually makes: a batting 92 and a bowling 92 are the same distance in runs per ball.

`rating = 50 + 50 × clip(normalised / (3.5 × SD), −1, +1)`. **S = 3.5, and the clip is a
guard rather than a transform** — it touches **1 of 1,812 rows**. The observed range is
−3.12 to +3.60 SD, near-symmetric with no fat tail, because shrinkage already pulled every
extreme season (all of them 120–360 balls) toward its prior before it could run away.
S = 3.0 would clip seven, and clipping is percentile's sin applied locally: two clipped
seasons both read 100 and the gap between them is gone. Resulting scale: min 6.7, mean
50.0, SD 14.3, p99 85, p95 74.

**7.5 Do not fold volume into the rating.** Opportunity is not quality. Balls faced belongs
in the draft interface as context and in the simulator as batting position, not as a
penalty inside the number.

**7.6 Keep display and simulation separate.** The 0–100 rating is for the UI. The simulator
consumes the underlying per-ball parameters, also season-derived and shrunk. The simulator
must not read the display rating. Store both.

**7.7 Show the output before moving on.** Print the top 20 franchise-season entries by
rating **within each position band and each bowling usage phase**. Lists full of unknowns
mean shrinkage is too weak; lists of only the six most famous names mean it is too strong.

*First read, post-A33 floors:* the lists pass. Purple Cap winners surface in the right
cohorts across the whole span (Tanvir 2008, RP Singh 2009, Malinga 2011, Faulkner 2013,
Bhuvneshwar 2017, Tahir 2019, Harshal Patel 2021) alongside Gayle 2011, Sehwag 2011, de
Villiers four times, Russell 2019, Karthik 2022 and 2024, Tewatia 2020, and Muralitharan,
Kumble and Warne in the spin-era seasons — famous names, but not *only* famous names.

**[A39] But the floors leave whole cohorts unrateable, and that is recorded here rather
than discovered inside §7.4.** Of the 166 franchise-seasons, the number that can field a
*rated* player in each slot:

| batting band | fs covered | | bowling phase | fs covered |
|---|---|---|---|---|
| opener | 166 | | middle | 164 of 166 |
| top_order | 165 | | mixed | 148 of 160 |
| middle | 150 | | powerplay | 137 of 164 |
| **finisher** | **43** | | **death** | **8 of 66** |
| **tail** | **0** | | | |

**`tail` is not thin, it is empty.** Zero of 707 tail player-seasons clear the 100-ball
batting floor, and the largest tail batting season in nineteen years is **95 balls**. This
is the floor being *right* — a number 9 genuinely has no measurable batting season — but
three things follow.

1. **A cohort with zero gate-passing seasons has no offset to estimate, and gets zero
   rather than a fitted one.** `tail` has nothing to estimate an offset FROM, so §7.4 must
   not estimate it; `death`, at 8 seasons and 3 of them DJ Bravo, must be pooled rather than
   fitted. This is exactly the condition A2 gave for refusing per-cell z-scoring, arriving a
   section earlier than expected. **[Corrected 2026-08-01 — this originally continued "...
   and any player whose only rateable discipline falls in it is simply not rated as a
   batter." A65 made that false: a `tail` batter with any balls at all, however few, gets a
   real batting rating today, just with an uncorrected (zero) cohort offset. Only a `tail`
   batter with LITERALLY ZERO batting balls that season — 4 of 3,337 squad members,
   archive-wide, across both disciplines — has no per-ball figure to rate at all, and A71
   covers those from reputation instead.]** A tail batter is still drafted mainly for their
   bowling, and that rating is real, but "not rated as a batter" is no longer the honest
   description of the common case.
2. **Check 12 must be written to FAIL on slot coverage rather than tolerate it.** A deck
   that cannot field a rated finisher from 123 of 166 franchise-seasons is a draft-legality
   problem, not a presentation one.
3. ~~**The deck may nonetheless be fine, and this has not been verified.**~~ **Verified
   2026-07-30, and the template was repaired — see A40, now closed.** `tail` never strands
   anyone: **393 of 730 tail player-seasons are bowling-rateable**, so a number 9 is drafted
   on the rating that is real. What the simulation found instead was that the deal-time
   guarantee had become load-bearing and `finisher` was carrying the risk. Merging `middle`
   and `finisher` into one 5–8 band of three took rational guarantee-off failure from 1.7%
   to **0 of 2,000**. §7.4 may now proceed; the template is settled, not deferred.

Do not resolve any of this by lowering a floor. A33 measured both and they sit where the
lists stop moving; moving them to fill a slot would be fitting the evidence to the deck.

---

### 7.8 The card: runs per match, disciplines added, and a declared reputation term

**Migration 011. A view replacement and nothing else** — no table, no column, no stored
number, exactly as 010 was.

**[A54] Per ball is right for the engine and wrong for a card.** The engine tilts one
delivery at a time, so it needs a per-ball rate. A drafter is buying a player for a match.
Four measurements said the difference was not cosmetic:

- Kohli 2016 (973 runs, the aggregate record) rated **75.4** against Russell 2019's
  **100.0**. Per match they are **+11.35** and **+12.13** runs above par — an 11% gap shown
  as 25 points, because Kohli's value spreads over 36.9 balls a game and Russell's over 19.2.
- Warner 2016 beat Tanvir 2008 on impact per match (**+12.61** to **+9.46**) and lost on the
  card (81.5 to 84.7). The card had the better season second.
- Players with four to six Player-of-the-Match awards in a season — Marsh 2008, Hussey 2013,
  Tendulkar 2010, Rohit 2016 — sat in the **60s**.
- Finishers held **4% of rated seasons and 20% of the top twenty**, because a death-overs
  ball carries **3.93** runs of variance against a powerplay ball's **2.12** and they face
  about half as many. Per-ball rating read that noise as merit.

Multiplying by balls per match fixes all four and needs no new correction: finishers fall to
**0% of the top 20 and 6% of the top 50**, because fewer balls now means less, not more.

**[A55] Batting and bowling ADD.** 010 gave an all-rounder two independent rows and every
consumer took the better one, so Narine 2024 — 488 runs at a strike rate of 181 *and* a 69.7
bowling season — was carded at 69.7 with his batting discarded. Both impacts are already
measured in runs above par, so they need addition, not weighting. **123 of 1,689 player-
seasons score in both disciplines.** Narine 2024 is now **95**.

**[A56] Player of the Match enters the rating.** Cricsheet carries it on **1,234 of 1,243**
matches and migration 002 has stored it since the first load; the ratings ignored it. It is
the one **human** judgement in the pipeline — somebody who watched the game naming who
decided it. Priced at **12 runs per award per match played**, which is a conversion and
therefore a choice, written once in the view. Deliberately too small to carry a season
alone: Marsh's five-award 2008 gains 5.5 runs a match against a merit of 16.8.

**[A57] A REPUTATION term, and it is a game-design decision rather than a measurement.**
This is the only parameter in the project not derived from evidence about the thing it
scores, and it is recorded plainly so nobody later mistakes it for one. The brief is a game
people want to play, and a card rating Bumrah 37 for one poor season reads as broken to
someone who knows he is Bumrah — however well earned that 37 is by 4 wickets in 49 overs at
8.37.

Mechanically it is **A7 reinstated as a second shrinkage stage**: each season is blended
toward the player's other seasons, leave-one-out, at **REPUTATION = 0.45**. A46's removal of
A7 from the *first* stage stands — `player_season_impact` still shrinks toward the band
prior, which is still the right answer for estimating a season. This is a different claim
applied after the estimate is complete: a card is *"Bumrah, as of 2026"*, not *"Bumrah in
2026"*. Result: his range is **78 to 98** across eleven seasons rather than 37.6 to 92.9.

Two guards, both load-bearing:

- The career estimate is itself shrunk toward the league by **CAREER_N = 2** pseudo-seasons.
  Without it a one-season player is blended toward himself, i.e. not blended at all, and
  Tanvir 2008 — a fine season, but eleven matches with no career to appeal to — ranked
  **third of 1,689**, ahead of every Kohli and Gayle season. He now sits at 92.
- **The blend feeds `rated_per_ball` too, not only the display.** If reputation lifted the
  card without lifting what the engine plays, the match would contradict the rating inside
  one over. **Check 22** enforces this and was verified falsifiable: with the blend on the
  card alone, **1,689 of 1,689** player-seasons fail it.

**[A58] The display scale is 70–100, INTEGER, anchored on percentiles.** A44's 0–100 with a
mean of 50 is a statistician's scale; a game scale puts the floor where a weak card is still
a card. It costs nothing in honesty because the map is linear — every gap survives in
proportion, which is the whole of A44's argument against percentile.

Anchored at the **2nd and 99.8th percentiles** [corrected 2026-08-02, A74 — the high anchor
is now the **99th** percentile, not the 99.8th, after counting showed 82% of the deck reading
70-79 under the old anchor; see CLAUDE.md's A74 for the measurement] of blended merit, not at min and max: Gayle
2011 and 2012 sit far clear of the field, and anchoring on them compressed **55%** of all
seasons into a five-point band. On percentiles the spread is **19/40/26/11/3/1%** across the
six bands and four seasons reach 100. Integer rounding was asked for and makes ties common —
about **56 seasons per point** — which is accepted: a drafter sees fifteen cards at a time,
and the ordering underneath survives in `rated_per_ball`.

Top of the resulting list: Gayle 2011 and 2012 (100), de Villiers 2016 and 2015 (100),
Marsh 2008 (99), Russell 2019 (99), Kohli 2016 (98), Warner 2016 (98), Bumrah 2024 (98).

### 7.9 Versatility, and the top of the scale

**Migration 012. A view replacement, as 010 and 011 were.**

**[A59] An all-rounder is worth more than the sum of his two impacts, and the reason is the
DRAFT rather than the cricket.** A55 made batting and bowling add, which was the missing
half; it is still not the whole of what a dual contributor is worth. The template is what a
drafter actually solves — 15 cards into `keeper 1 / opener 2 / top_order 2 /
middle_or_finisher 3 / bowler 5 / open 2` — and a card filling a batting band *and* the
bowler slot relieves two constraints with one pick. Narine 2024 can be slotted at `opener`,
`bowler` or `open`; a pure opener of equal merit can only ever be one of those. None of that
flexibility appears in a sum of per-ball impacts.

Priced at **ALLROUNDER_RUNS = 5.0** runs per match at a full double share, scaled
continuously:

```
share = least(bat_balls_per_match / 18, 1) * least(bowl_balls_per_match / 24, 1)
```

**Continuous, not a threshold, and that is the point.** A cut at "three overs bowled and
batting enough" qualified **38 of the 123** player-seasons rated in both disciplines and put
a cliff through a continuum — Watson 2015 missed by **0.6 balls a match** while Watson 2011
cleared it. A33's floors and A43's cohort threshold are defensible because each sits in a
wide empty gap in the evidence; this quantity has no gap, so a threshold would be an
arbitrary line rather than a rule about evidence.

The two references are the full shares: **18 balls faced** is three overs and is also the
league's mean batting exposure (18.7), and **24 balls bowled** is four overs, the hard
maximum. A player doing both jobs completely scores 1.0; a specialist scores 0. Like A57
this is a declared game-design term and is written down as one.

Effect: Narine 2024 goes 95 to **99** at a full share of 1.00, Watson 2008 to **98** at 0.90,
Russell 2019 holds **99** at 0.58. Five of the top twenty are now dual contributors.

**[A60] The scale tops out at 99, not 100.** 100 reads as a perfect card and nothing in
nineteen seasons is perfect. Purely a display decision: the map stays linear on blended
merit and only the upper anchor moves, so every gap survives in proportion exactly as A58
requires. **Five seasons reach 99** — Gayle 2011 and 2012, Russell 2019, Narine 2024, de
Villiers 2016.

**A structural consequence of A54 worth recording rather than correcting.** A bowler may
bowl at most **24 balls** in a match; a top-order batter faces up to **43.6**. Bowling
exposure is therefore capped at about **55%** of batting's ceiling. That is true of the game
itself, not an artefact, so it is not adjusted for — and A59 partly offsets it for players
who do both.

## 8. Validation

Runnable scripts in `/validation` with clear pass/fail output.

1. Every delivery's `runs_batter + runs_extras` equals the source `runs.total`.
2. **[Replaced 2026-07-30 — the original was tautological.]** It read "every person
   referenced in `deliveries` and `appearances` exists in `people`", which five foreign
   keys already enforce; Postgres refuses the write, so the check could never fail. It is
   replaced by the two things the schema does *not* enforce: every batter, bowler,
   non-striker and dismissed player has an `appearances` row **for that same match**, and
   every `franchise_season_id` on a match's deliveries and appearances is one of that
   match's own two teams. Comparisons use `is distinct from`, not `not in`, because
   `team_a_fs_id` is nullable and `not in` would return null and read as a pass.
3. Legal balls per completed over equals 6, except where the innings ended mid-over and
   except the eight overs the source itself flags under A14. The exemption is only worth
   as much as the set it exempts, so the check also asserts the flagged set in the
   database is exactly the eight whitelisted overs, and that none of them holds six legal
   balls.
4. Every match maps to exactly two distinct franchise-seasons, both belonging to the
   match's season year.
5. No franchise-season contains a player with zero appearances for it.
6. Super over deliveries are excluded from every derived stat table.
7. Reconstruct and print full scorecards for five specific matches spread across 2008,
   2013, 2016, 2020 and 2024, to be checked by hand against public scorecards. **This is
   the check that actually matters — make the output readable.**
8. **Career leaderboards.** `--leaderboards` prints the top 10 run scorers and wicket
    takers for the plausibility read this check was specified for. Printing cannot fail, so
    the automated half asserts the three things a human skimming ten names would not notice:
    **Kohli leads the run scorers** (the only external anchor here — deliberately an
    ordering and not a total, because the margin is over 2,000 runs and no revision to a
    recent season can reorder it, whereas an exact figure goes stale; A22's lesson is that
    the claim must be the thing actually checked); **no name appears twice**, which is where
    a split `person_id` surfaces as one player wearing two rows and no foreign key can see
    it; and **every dismissal kind is classified** as bowler-credited or not, so a kind
    Cricsheet adds later cannot fall between the two sets and vanish from the wicket column.
    Currently Kohli 9,336 runs, Chahal 233 wickets, 10 kinds all classified. Chahal's lead
    is 7 wickets and is **not** asserted, for the same robustness reason.
9. No franchise-season rating uses data from any other season, any other competition, or
   any career aggregate except as a shrinkage prior. **Written 2026-07-31.** The wording
   predates there being TWO shrinkage stages — the band prior (A46) and A57's career blend
   — so the check polices the exception rather than restating it. It recounts `balls` from
   each rating's own franchise-season, and asserts A57's blend lies inside
   `[merit, career_merit]`: a convex combination can pull a season toward a player's other
   seasons and can never carry it past them, which is precisely what makes the career term
   shrinkage rather than leakage. **Falsifiable:** balls from a career aggregate fails
   **1,767 of 1,812** rows; a blend escaping its endpoints fails **1,016**.

**[A2] Checks 10 and 11 replaced.** The originals were tautological: any within-cell or
within-season standardisation forces those means flat *by construction*, so they could
never fail. These two bracket the shrinkage constant from opposite sides.

10. **Under-shrinkage test.** For players with consecutive seasons, does the shrunk
    season-N rating predict season-N+1 per-ball impact **better** than the raw season-N
    figure does? If not, shrinkage is too weak.
11. **Over-shrinkage test.** Measure within-player variance of ratings across a career. If
    a player's ratings have collapsed toward a flat line, shrinkage is too strong and the
    Kohli-2016-vs-2022 contrast the whole design rests on has been destroyed.

12. **[Rewritten 2026-07-30, A40. The original was a per-slot count and asked the wrong
    question.]** It read "every franchise-season has enough draftable players after the
    §7.3 gate to fill every slot type in the template", which cannot answer feasibility: a
    drafter is not served every franchise-season, they are served fifteen of them one at a
    time, and dedupe on `person_id` removes a player from every other card they appear on.
    Feasibility is a property of that sequence. **The check now runs the draft** — 300
    seeded drafts against each of three drafter policies — and fails if any of them
    strands. It reports the guarantee-off failure rate beside the result, because that is
    the number that says whether the guarantee is the safety net SPEC calls it. It reads
    the template from `etl.feasibility` so a retune moves the check with it — which it
    already did once, when A40 merged `middle` and `finisher`. Currently passes on the
    merged template: 100% completion on the guarantee, **0%/13%/20%** without it, thinnest
    slot now **`keeper` at 126 of 166**, which is `keepers_by_season.csv` and check 19's
    business rather than the template's. **Verified falsifiable** — emptying the finisher
    pool or the keeper pool strands 30 of 30 drafts while the unmodified deck completes 30
    of 30. Property-based on purpose: it follows the template rather than pinning today's
    numbers, so the assertion survives a retune and the reported figures move with it.

13. **[A2] Cohort offset era-drift diagnostic.** Check whether the pooled cohort offsets
    drift across eras. Pooling assumes "finisher" means the same thing in 2010 and 2024,
    and the Impact Player rule may have broken that. **If drift is large for a specific
    cohort, split that cohort's offset by era.** **Written 2026-07-31 and PASSES.** Three
    eras, split at 2015 and at 2023 for the Impact Player rule. Not a tautology:
    `centred_per_ball` is centred within *season*, which forces the league flat across
    seasons and does **not** force a cohort flat, so a cohort drifting while its league
    does not is exactly the signal. Fails only when drift is both real (3 sampling SEs) and
    material (0.05 runs/ball, roughly A41's entire cohort spread). Largest observed drift is
    **0.055 runs/ball for batting/middle**, and every cohort sits under 3 SE — closest are
    batting/opener at 0.89 of the bar and batting/middle at 0.78. **The material bar is
    currently inert**, recorded in the code the same way check 6 records its inert clause.

14. **[A15] Innings-skew diagnostic.** Compare mean rating for players whose deliveries
    skew to second innings against those who skew to first. **Systematic separation means
    the single first-innings state table is mispricing chases**, and the response is a
    second state table for innings 2 bucketed by required rate. This diagnostic is the
    trigger for building it. Do not build it in advance.

15. **[A19] `legal_ball` reproduces the source's own `actual_delivery`** for every
    delivery. This is the only check in the list that tests our reading of the source
    against the source's own answer rather than against internal consistency, so a
    mistake in classifying wides and no-balls cannot hide behind agreeing with itself.
    Currently exact on all 295,732 deliveries.

16. **Scheduled length is null only where §4.5/A17 says it must be.** Exactly 6 innings,
    all named in the parser's warnings. A null appearing anywhere else means the
    derivation rule has silently stopped matching the archive.

17. **[A22] Balls faced reproduces published strike rates.** Hand-entered reference
    figures for three 2016 season aggregates — Kohli 973 off 640, Warner 848 off 560,
    de Villiers 687 off 407. The two candidate predicates differ only by the no-balls a
    batter faced, which is small but never zero across a season, so a wrong predicate
    cannot coincidentally satisfy all three. This is the check that caught A22.

18. **[A4/6.4] An innings that lost ten wickets used exactly eleven batters.** The wicket
    count comes from `player_out_id`, a column the batting-position rule never reads, so
    this predicts the size of the order from outside the rule rather than restating it.
    Dropping the `non_striker` scan, or letting an Impact Player entrant take a twelfth
    position, both break it. Currently exact on all 227 all-out innings. Carries a bounds
    assertion on `squad_members` in the same check: no stored position outside 1–11.

19. **[A24] Every franchise-season offers at least one keeper.** *Recorded here 2026-07-30;
    it had been implemented since SPEC 6 and listed nowhere, which is exactly the drift this
    document exists to prevent.* This is a check on the deck rather than on the parse: the
    keeper slot in the XI has to be filled from somewhere, so a squad offering nobody to
    fill it is a card that breaks the game, not a statistic that is slightly off.
    **PASSES at 166 of 166 as of 2026-07-31** (A52). It failed at 140 for as long as
    `keepers_by_season.csv` was unfilled, and that failure was the block being visible
    rather than a defect — the last 26 squads took no stumping all season, so nothing
    inside the archive could ever have settled them.

20. **[A31] The state model covers all 80 states and reconciles with a fresh recount.** Two
    claims. *Legibility:* a state the archive has never seen is stored with `faced = 0`, not
    left out, because an absent row and a zero row mean different things to the simulator —
    "this table does not cover that" against "cricket has never produced that" — and only
    one is true, so the table says which instead of leaving it inferred from a missing key.
    *Freshness:* the fitting set is re-derived straight from `deliveries` and the stored
    totals must match, which is what makes this more than a restatement of the loader. It
    fails if the state model is left stale behind a reload, or refitted through a filter that
    has drifted from A28's `innings_scheduled_balls = 120`. Currently 80 of 80 states, 5 as
    explicit zeroes, 146,159 balls faced, 161 runs-remaining states.

**Check 6 came off SKIP on 2026-07-30**, when migration 007 gave it a derived table to
examine. It had been written to turn into a hard FAIL in exactly that moment rather than a
silent pass, and it did. Its assertion is now that **no delivery is both a super over and
inside the fitting set** — aggregation destroys provenance, so the exclusion has to be
checked where it happens, not on the aggregate. It also reports that `not is_super_over` is
**carrying no weight**: a super over has `innings_no >= 3` and a null
`innings_scheduled_balls`, so `innings_no = 1` and `innings_scheduled_balls = 120` each
exclude all 175 of them unaided. That is A21 working as designed — a positive test for a
known value excludes nulls by construction — and the redundancy is reported rather than
trimmed so nobody mistakes the explicit clause for the thing doing the work.

---

## 9. Out of scope for this phase

**The simulator and the draft loop were the first two entries on this list and were moved
off it on 2026-07-30.** Recorded rather than quietly deleted, because it is a change to the
phasing this document set. The reason is that §7.4's normalisation, A46's prior and A35's
`k` were every one of them settled on internal evidence, and a rating that nothing consumes
cannot be judged: the only external test available is whether the numbers play a match that
looks like cricket. They are specified in §10. Nothing in `etl` imports `game`, so the
dependency runs one way and the pipeline is exactly as testable as it was.

- Any web UI or Next.js app. **The API's session model is now specified — see §11** — but
  nothing is built.
- Authentication, leaderboards, daily challenge. **Deliberately deferred, not rejected**:
  §11's design is chosen partly because all three are additive to it.
- Any AI or LLM integration
- Any franchise logo, crest, kit colour or player photograph. IPL and BCCI marks are
  aggressively enforced and player likeness rights are live litigation territory in India.
  **Text only.**

## 10. The match engine

`uv run python -m game` drafts two squads off the real deck, picks two XIs and plays twenty
overs a side. It is the consumer §7's ratings were missing. It reads the stored §7.1 grids
and the `player_season_rating` view; it fits nothing and writes nothing.

### 10.1 The outcome space

A ball resolves to one of eight things: a dismissal, or 0-6 off the bat. That is the whole
archive — 32 fives off overthrows and no sevens (§7.1's outcome guard) — so the space is
measured, not chosen, and it is the same seven columns `state_ball_outcomes` stores plus
the dismissal the same row counts.

Wides are drawn **before** the delivery and are not balls faced, so they do not advance the
over. Every other extra is a flat per-ball rate charged to the team and not to either
player. Both rates come from the §7.1 fitting set: **0.0343 wides per ball faced at 1.193
runs each**, and **0.0296 non-wide extras per ball faced**. They are league constants
because no rating in §7 is an extras rating — inventing a per-player one would be A23's
error in a new place.

### 10.2 The tilt

The state model gives a distribution per (over × bucketed wickets). A rating is runs per
ball above the league. The engine has to turn "this batter is +0.15 and this bowler is
+0.08" into a distribution, and it does it by **exponential tilting**:

```
q(x) = p(x) · e^(θx) / Z,   θ solved so that   E_q[x] = E_p[x] + bat − bowl
```

This is the minimum-KL distribution with the mean requested, which is the property that
matters: **it adds no information beyond the mean**, so the state's own shape decides how a
rating splits between more runs and fewer dismissals rather than a hand-written rule
deciding it. The dismissal sits in the same distribution at value `−wicket_cost(state)`, so
one θ moves both halves in the proportion the state implies. Solved by bisection on θ, which
needs no starting point because the tilted mean is strictly increasing in θ.

Three consequences worth stating, all pinned by `tests/test_simulator.py`:

- **θ = 0 is the identity.** With every rating at league average the engine reduces exactly
  to the state model, which is what makes §10.5 a real test rather than a tautology.
- **A zero stays zero.** The tilt multiplies, so it cannot invent a five off the bat in a
  state that has never produced one. An additive adjustment would not have this property.
- **An unreachable target saturates** at the nearest extreme instead of diverging.

### 10.3 Four named simplifications

Named because each is a place the engine is knowingly not cricket, and an unnamed
simplification is indistinguishable from a bug:

1. **No partnership, no fatigue, no set batter.** A batter's rating is the same on ball 1
   as on ball 40. §7 rates a season, not an innings, so there is no per-innings shape to
   consume yet.
2. **No captaincy.** `choose_bowler` gives the next over to whoever has bowled least, best
   bowler breaking the tie, and never twice running. A real captain saves his best for the
   death. Bowling order is a tactical layer and adding one before the engine underneath it
   has been read would bury the engine's behaviour under a policy's.
3. **No chase pressure.** The second innings uses the same first-innings grids and stops
   at the target. §7.1 fitted first innings only (A15) and there is no second-innings table
   to consume; A15 says one gets built if check 14 fires.
4. **Extras are a league constant** (§10.1).

### 10.4 The XI

Eleven from fifteen: the keeper first, then the five best bowlers, then the best bats.
**Five bowlers is the count rather than a preference** — twenty overs at a maximum of four
each is exactly five. The attack is *capped* at five for the same reason: `choose_bowler`
equalises workload, so an XI holding seven bowlers would give all seven three overs and hand
the best bowler in the side a quarter of his allocation, flattening exactly the rating
differences the draft is about.

**The four-overseas rule is enforced on the KNOWN count and nowhere else.** A23 left 314
people with a NULL nationality, so the engine enforces a lower bound and reports how far
above it the truth could sit — `legal`, `CANNOT BE CERTIFIED` with the interval, or `NO
LEGAL XI EXISTS`. Both sides of a repair read A23 the same way: an overseas player may only
be swapped out for a **known domestic** one. Swapping in an unknown lowers the count this
code can see without lowering the count that matters.

Measured over 400 drafted XIs *while `nationality.csv` was empty*, known-domestic-only
against unknown-eligible: **legal 127 vs 118, uncertain 179 vs 224, no legal XI 94 vs 58**.
The strict rule certifies *more* XIs, not fewer — the loose one was manufacturing
uncertainty by design. **Since the file was filled (A51) the same 400 XIs read 312 legal, 0
uncertain, 88 with no legal XI**, so the uncertainty branch is now unexercised in practice
and the rule above is what keeps it that way rather than something to delete: the 13
disputed rows are still open and any future archive revision reintroduces unknowns.

**[A61] CLOSED 2026-07-31.** The 88 (94 before the nationality fill) were a real finding and
not an engine bug: **§1.1's draft template carried no nationality constraint at all**, so the
deck could deal a squad that could not be fielded. The draft now caps the **squad** at four
known-overseas players.

Capping the squad is stricter than the rule it enforces — a real franchise carries more
overseas players than it fields — and it is chosen anyway **because it makes legality
structural**: with four or fewer in a squad of fifteen, every eleven drawn from it is legal,
so no XI-selection path has to be trusted to find the legal one. Caps of five and six also
produce zero illegal XIs today, but only by relying on the selector, which is the
sequence-property mistake A40 already made once by counting slots instead of running the
draft.

Measured by running the draft: **400 of 400 XIs legal, 0 uncertain, 0 impossible**, and cap
4 had the highest rational guarantee-off completion of the three candidates — **99.0%**
against 98.4% at five and 97.6% at six. The cost is that the deal-time guarantee is no
longer inert: it fires on about **1.5% of rational drafts** against 0.0% before, worst
single pick one to two re-draws. That is a cap binding occasionally, which is what a cap is
for, and A40's firing table now records it as the one sanctioned increase.

`enforce_overseas` in the engine stays as a second net rather than being deleted. A revised
archive can reintroduce unknown nationalities, and A49's asymmetric repair rule is the only
thing that would keep the count honest if it did.

### 10.5 `--validate`, and what it does not cover

`uv run python -m game --validate` plays league-average innings — every rating zero, so
every θ zero — and compares them against the same 1,218 first innings §7.1 was fitted on.
3,000 innings, seed 7:

| | simulated | archive |
|---|---|---|
| mean total | **169.5** | **169.4** |
| of which extras | 8.5 | 8.5 |
| mean wickets | 6.0 | 6.1 |
| all out | 7.7% | 7.1% |
| **SD of totals** | **27.1** | **33.2** |

The first four are the check passing. **The fifth is the engine failing and it is recorded
rather than tuned away**: simulated innings are ~18% too tightly bunched. The cause is not
mysterious and is the same simplification four times over — every innings is played by
league-average players on a league-average pitch, so the between-team, between-venue and
between-day variation that widens the real distribution is absent by construction. Widening
it with a fudge factor would fit the number while making the engine less honest, and the
real repair is ratings on both sides, which is what the game supplies.

`--validate` is the engine's own arithmetic against the archive. It cannot check the tilt,
because at league average the tilt is the identity: `tests/test_simulator.py` is the other
half and covers what no aggregate can — that the tilt hits the mean it was asked for, that a
better batter both scores more and is dismissed less, and that the XI rules hold.

## 11. The session model

**[A62] Sessions are STATELESS and seed-based. Nothing is stored, and no account is needed
to play.** Ratified 2026-07-31, before any web code exists, because it determines the schema
and the API shape and is far cheaper to decide than to retrofit.

### 11.1 What forces the question

`run_draft` is a closed loop today: fifteen picks start to finish, with the drafter supplied
as a function (`rational`, `naive`, `random`). Nothing waits for anything. A website replaces
that function with a person, so the loop has to be broken open across fifteen HTTP requests —
and the partly-drafted squad has to exist somewhere between pick 3 and pick 4. That
"somewhere" is the session model.

### 11.2 The decision, and the property it rests on

The client carries **the seed and the choice indices made so far**. Each request replays the
draft from scratch and serves the next deal. The state string is `7-0.3.14` — seed, then
choices, in the open. **[A73, 2026-08-02] Each choice now carries a slot too**
(`7-3:4.0:12`, index then slot per move), since a pick is final the instant it lands rather
than arranged afterward — the "why unsigned" argument below is unchanged, it is exactly as
true of `index:slot` pairs as it was of bare indices.

**[Corrected 2026-07-31 while building it] The state is NOT signed.** This section first
called for an HMAC. Writing the replay showed there is nothing for a signature to protect:
every choice is an index into options the server itself dealt, so replay either lands on a
legal pick or the index is out of range and the state is refused. There is no privilege to
forge — a player can already ask for any seed, and the score is computed by the server from
the state rather than submitted alongside it. A signature would have protected nothing while
reading as though it protected something, which is worse than leaving it off. **It goes back
on the day a result outlives the request that produced it**, because a leaderboard would let
a client submit a state it never played.

Parsing is strict: an empty segment is malformed, not an omitted choice. `7-1..2` and
`7-1.2` must not be the same session, or two URLs silently mean one game and the first looks
like it lost a pick. Same instinct as A23 — a string we cannot read is not one to guess at.

This rests on the draft being fully deterministic given a seed and a sequence of choices,
which was **verified rather than assumed** before the design was ratified: **200 of 200
drafts replayed exactly** — same squad, same slot assignment, and the same sequence of
franchise-seasons dealt. The match is deterministic from the same seed too, so a whole
session reproduces end to end.

The RNG stream *does* depend on the choices, because a pick changes which cards remain
eligible and therefore how many re-draws the guarantee performs. That is why the choices are
part of the state and the seed alone is not enough.

**The state is a seed and twelve small `index:slot` pairs** (A73 — was fifteen bare
integers). It fits in a URL.

### 11.3 Why this one

- **No storage, no accounts, no migration.** The first playable version needs none of the
  three, and nothing in this section adds a table.
- **A URL is a reproducible game.** `/draft/8f3a2c` deals the same fifteen cards to everyone,
  forever. That is a sharing mechanic for free, and it is the same determinism the test suite
  already depends on.
- **Replay is cheap.** A full fifteen-pick draft replays in **3.3 ms**; a whole fifteen-request
  draft costs roughly **25 ms of CPU in total**. The deck is **1,689 cards over 166
  franchise-seasons** and loads once at boot in **3.8 s**, so it is held in memory and never
  re-queried per request.

### 11.4 The surface

Five routes, in `web/app.py`, and **nothing in them decides anything about cricket**. The
overseas cap, the deal-time guarantee, the five-bowler attack and the four-overseas XI are
all reached by calling `etl.feasibility` and `game`, never by restating them.

| route | |
|---|---|
| `GET /api/health` | deck size, for a load balancer |
| `POST /api/draft?seed=` | start a game; omit the seed for a fresh one |
| `GET /api/draft/{state}` | the current deal, or the finished twelve |
| `POST /api/draft/{state}/pick` | `{"index": n, "slot": s}` — **[A73] index into the deal's options, slot 1-11 or 12 (Impact); one move, final the instant it lands, no separate place route** |
| `GET /api/twelve/{state}` | the arranged twelve, and its overseas status |
| `GET /api/season/{state}` | fourteen league matches, the table, the playoffs |

`web/session.py` does the work, and it **reuses `run_draft` verbatim** rather than
reimplementing the loop: the human is injected as a policy that replays recorded choices and
then raises to pause. A second implementation of the draft would be a second place for A61's
cap and A40's guarantee to drift, which is what A19 refuses for columns and what check 12's
`TEMPLATE` import refuses for the template.

The match continues the session's **live** rng — player's draft, then opponent, then the
innings — which is the order `game.__main__` uses. Re-seeding for the match would have given
the API a different game from the CLI for the same seed with nothing to notice it; a test
pins it by comparing against exactly that mistake.

Measured end to end against the real deck: **1,689 cards over 166 franchise-seasons**, a
first deal of 24 options, a full fifteen-pick draft, a legal XI, and a scoreboard. The same
state returns an identical match on repeated requests, and the same seed deals identical
cards to different players.

### 11.5 What is deferred, and why it stays cheap

**Daily challenge** and **login** are explicitly later stages. Both are additive here rather
than a rewrite: a daily challenge is one shared seed per date instead of a random one, and
accounts only become necessary when a result has to outlive the request that produced it. A
`results` table arrives with the leaderboard and not before.

**[A102] Login CLOSED 2026-08-12.** `accounts` and `game_results`/`game_result_players`
(migrations 026-027) are exactly that `results` table, arriving ahead of a full leaderboard
rather than with one — see A102 in the decisions log for the full shape: email+password,
stdlib-only hashing and cookie signing, no login wall, and a save that is an explicit
client action gated on a unique `(account_id, source, natural_key)` constraint, never a
side effect of a poll or reload (A62 stands for every anonymous route unchanged). The
leaderboard itself, and the daily-challenge question below, remain open.

**The one thing to settle before the leaderboard, not after:** whether a deal is random per
player or shared per day. Random-per-player is played once; shared-per-day is compared and
returned to. It costs nothing now and is awkward to retrofit once URLs are in circulation.

## 12. The season

**[A63] The game is a SEASON, not a match.** One match was never the game: a drafted eleven
that wins once and loses thirteen had a good night, not a good side, and only a league
separates the two. Fourteen league matches, a table, then the playoffs.

### 12.1 The opposition is historical

Nine real franchise-seasons — Mumbai 2018, Kolkata 2024 — each fielding the best legal
eleven that squad could actually put out, drawn uniformly over the 166 exactly as the deck
is (A10). Better than nine synthetic drafts on its own terms, and it is also **the only
opposition this archive can vouch for**: every one of those elevens really took the field.

### 12.2 Fourteen matches on ten sides

Ten teams at fourteen matches each is 70 fixtures, and it needs each side to play five
opponents twice and four once. That makes the "plays twice" relation a **5-regular graph**,
which circular distances 1, 2 and 5 give exactly: two neighbours either side, plus the team
directly opposite. Distance 5 is the antipode and so names one opponent rather than two —
5 = 2 + 2 + 1 — which is the arithmetic a test derives rather than assumes.

It is also what a real fixture list does: near rivals twice, the rest once. Symmetric, so
no side gets an easier draw.

### 12.3 The table

Two points a win, one a tie. **Net run rate charges a side bowled out the FULL twenty
overs**, which is the competition's own rule and not a rounding detail: without it, 60 all
out in 12 overs scores a better rate than 60 for 4 in 20, and a side could improve its net
run rate by collapsing. Pinned by a test from both directions.

### 12.4 The playoffs are the IPL's, not a bracket

Top four. Qualifier 1 (1 v 2), Eliminator (3 v 4), then **Qualifier 1's loser drops into
Qualifier 2 rather than out**, and the winner of that meets Qualifier 1's winner in the
final.

Reproducing that is most of what the league table is *for*. Under a straight semi-final
bracket, finishing first and finishing third are nearly equivalent; under this one, the top
two get a second life and the table is worth playing for.

### 12.5 What the deal shows

**The whole squad, not the takeable part of it.** A franchise-season is about twenty men, so
a list of only the pickable ones misrepresents the squad — Chennai 2010 would read as ten
players, and you could not see that it had Dhoni once your keeper was named. Everyone else is
greyed with the reason: *already drafted*, *overseas quota full*, or *no place open*.

**[Corrected 2026-08-01 — this section originally described a fourth reason, "the numbers,"**
**for an "unrated man," using McCullum's 92-ball 2008 as the example: "unrated... under A33's**
**hundred-ball floor."** A65 made that false — McCullum's 2008 is rated 83 and is a real Card
today, not a greyed-out plain record. A33's floor still exists, but only as an input to
*how* a season is estimated (its own evidence vs. a prior), never to *whether* it gets a
rating at all. **A71 finished the job**: the 4 of 3,337 squad members with literally zero
balls in either discipline — not "below a floor," but no evidence whatsoever — are rated too,
from their own career elsewhere or, lacking one, a shared arbitrary floor. There is no
"unrated man" case left in the archive as it stands today; the fourth greyed-out reason is
dormant rather than deleted, kept for the day a revised archive reintroduces a squad member
with no assignable discipline at all (A27's shape, not A33's).

Every squad member reaching a franchise-season's deal is therefore now a real `Card` — see
CLAUDE.md's decisions log, A65–A71, for the mechanism.

## 13. Season analysis

**[A104, added 2026-08-16.]** The post-tournament chart set, available in a completed solo
season and in a completed **`league` room only**. Read-only, on its own route in both cases.

**It required no schema change and no new storage, and that was verified before anything was
built.** Every number on the screen is aggregated from output the engine already produces:
`Result` carries two `Innings`, and each `Innings` carries an `over_log` of `OverSnapshot`s
— over index, bowler, and that over's own runs and wickets — built for §12's reveal
animation and then discarded. That log is Manhattan data. `game/analysis.py` reads it in
**1.4 ms over 74 fixtures**, against a season replay of ~2.7s that is already paid.

### 13.1 Phases

Exactly SPEC §4.8's own rule, not a definition invented for this screen: death is the final
25% of the innings' scheduled overs, `(scheduled_balls * 3 // 4) // 6`, which is over index
15 in a full twenty. Powerplay is the first six, middle is the rest. A different split here
would put the charts quietly at odds with every rating drawn on them.

### 13.2 The divisor a Manhattan needs

Aggregated across a tournament, an over's bar must be divided by **how many innings reached
it**, not by the fixture count — measured on a real season, only **100 of 148** innings
reach over 20. Without that divisor the last over reads as a collapse when it is really that
a third of the innings had already ended. `OverBar.innings` carries it.

### 13.3 Volume floors on the rate leaderboards

Economy and strike rate need a floor or they are won by whoever bowled two overs. The floor
is **half a league campaign (7 of 14 matches) at §12's own reference exposures** — 18 balls
faced and 24 bowled per match — rather than a round number. An earlier pass used 90/120 and
returned a best strike rate off 94 balls, under seven a match; at the derived floor the same
season returns a 367-ball campaign. Runs and wickets are totals, so they need no floor.

### 13.4 What it cannot show

Recorded here and in the module docstring so nobody looks for it: **spin against pace**
(`people.bowling_style` is NULL for all 816 people — a data-entry gap, not a modelling one,
parked deliberately), **dismissal kinds** (§10's outcome space is runs 0-6 plus wicket),
**boundaries and dot balls** (a batting card tallies runs and balls; `over_log` is per over),
and **wagon wheels** (nothing models shot direction).

### 13.5 Why not `final` or `cup`

A `final` is one match and a `cup` is three. A Manhattan over three innings is a scorecard
drawn sideways, and the phase splits would read as a claim about form when they would
actually be a coin toss. The route refuses both formats rather than drawing them.
