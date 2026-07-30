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

**Slot template. [A6 — supersedes the original template]**

| Slot | Count |
|---|---|
| Wicketkeeper | 1 |
| Opener (1–2) | 2 |
| Top order (3–4) | 2 |
| Middle (5–6) | 2 |
| Finisher (7–8) | 1 |
| Pace bowler | 3 |
| Spin bowler | 2 |
| Open (any) | 2 |

Keeper is a **role, orthogonal to batting band** — the keeper slot is filled by any keeper
regardless of where they bat. Batting-band slots apply to specialist batters. Bowlers fill
bowling slots regardless of their batting band, which is why `tail` gets no slot.

Bands are the §6.4 canonical bands. All-rounders may fill either a batting or a bowling
slot, decided by the drafter at pick time.

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
Excluding it would date the game immediately. Two conditions attach:

1. **Cricsheet is contributor-driven and recent matches get revised after the fact.**
   The archive SHA256 is recorded in `data/manifest.json`, and the 2026 files must be
   **re-verified against a fresh download before ratings are finalised**.
2. If any 2026 playing condition materially changed scoring, within-season normalisation
   (§7.4) absorbs it — but **flag it** if that season's baseline sits far off trend
   rather than letting it pass silently.

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

**6.1 Nationality and overseas status. [A3]** Build a person → national-team map from the
men's **Test, ODI and T20I** archives combined. T20I alone misses anyone whose caps
predate the format or came only in longer forms.

Anyone not found defaults provisionally to Indian with
`nationality_source = 'default'`. **Uncapped overseas players exist in the IPL and will
still default to Indian — this is the exact failure mode that breaks the four-overseas
rule.** So the defaulted list needs **full review, not a spot check**. Print it **sorted
by matches played descending** so the ones that matter surface first.

`etl/overrides/nationality.csv` is authoritative over the derivation.

**6.2 Wicketkeepers.** Mine `wicket_kind = 'stumped'` for positive signals. That misses
keepers who never stumped anyone. Produce the derived list, then complete
`etl/overrides/keepers.csv` by hand (~50–70 players, tractable).

**6.3 Pace versus spin.** Not derivable from the data at all. Generate
`etl/overrides/bowling_style.csv` pre-populated with every person who has bowled ≥30 legal
deliveries, style column blank. **The CSV is the only source of truth.** Until filled,
`bowling_style` stays NULL and pace/spin splits return NULL, never a guess.

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
franchise-season.

**6.6 Roles.** Derive per franchise-season, not per career:

- `keeper` if flagged as a keeper and they kept for that squad
- `bowler` if they bowled a meaningful share of team overs and faced few balls
- `batter` if they faced a meaningful share of balls and bowled little
- `allrounder` if both

Thresholds are named constants at the top of the file. Show the resulting distribution.
Something like 40% all-rounders means the thresholds are wrong. The slot template depends
on this being sane.

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
depending on anyone remembering to exclude it. Both predicates are kept — the
`was_reduced` clause preserves the ratified rule below, and the `= 120` clause makes the
null safe.

That leaves **149,697 deliveries** across 20 overs × 4 wicket buckets — about **1,871 per
cell**. The earlier ~145,000 was an estimate; this is counted.

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
  wicket down. The difference is the cost of a wicket there.

A batter's contribution on a delivery is the actual outcome minus the expected outcome for
that exact state, with dismissals priced by the wicket cost. Sum across a season, divide
by balls faced for a per-ball impact figure. Same for bowlers with signs reversed.

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

**7.3 Draftability gate.** Exclude below a minimum threshold, provisionally four matches
played. Stored as a constant. Excluded players stay in `squad_members` for completeness,
they just do not appear as a pick.

**7.4 Normalise within season and within cohort. [A2]**

*Season:* IPL scoring rates have risen substantially since 2008. An absolute scale makes
recent seasons strictly better and nobody will ever want a 2009 squad, which destroys the
deck's variety.

*Cohort:* score openers against openers, finishers against finishers, death bowlers
against death bowlers. Never against the whole population.

**Method:** normalise within season globally, then apply **cohort offsets estimated pooled
across all seasons** (partial pooling). Do **not** z-score independently within each
(season × cohort) cell — cells like "tail batters, 2011" may hold four players after the
§7.3 gate, and standardising four observations produces noise.

Map the result to a 0–100 display scale. A great finisher should surface at 92 as a
finisher, not be compressed to 61 for facing fewer balls than an opener.

**7.5 Do not fold volume into the rating.** Opportunity is not quality. Balls faced belongs
in the draft interface as context and in the simulator as batting position, not as a
penalty inside the number.

**7.6 Keep display and simulation separate.** The 0–100 rating is for the UI. The simulator
consumes the underlying per-ball parameters, also season-derived and shrunk. The simulator
must not read the display rating. Store both.

**7.7 Show the output before moving on.** Print the top 20 franchise-season entries by
rating **within each position band and each bowling usage phase**. Lists full of unknowns
mean shrinkage is too weak; lists of only the six most famous names mean it is too strong.

---

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
8. Print the top 10 career run scorers and top 10 wicket takers for plausibility.
9. No franchise-season rating uses data from any other season, any other competition, or
   any career aggregate except as a shrinkage prior.

**[A2] Checks 10 and 11 replaced.** The originals were tautological: any within-cell or
within-season standardisation forces those means flat *by construction*, so they could
never fail. These two bracket the shrinkage constant from opposite sides.

10. **Under-shrinkage test.** For players with consecutive seasons, does the shrunk
    season-N rating predict season-N+1 per-ball impact **better** than the raw season-N
    figure does? If not, shrinkage is too weak.
11. **Over-shrinkage test.** Measure within-player variance of ratings across a career. If
    a player's ratings have collapsed toward a flat line, shrinkage is too strong and the
    Kohli-2016-vs-2022 contrast the whole design rests on has been destroyed.

12. Every franchise-season has enough draftable players after the §7.3 gate to fill every
    slot type in the template. Flag any that do not — the deal-time guarantee depends on it.

13. **[A2] Cohort offset era-drift diagnostic.** Check whether the pooled cohort offsets
    drift across eras. Pooling assumes "finisher" means the same thing in 2010 and 2024,
    and the Impact Player rule may have broken that. **If drift is large for a specific
    cohort, split that cohort's offset by era.**

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

---

## 9. Out of scope for this phase

- The simulator or any probability model
- The draft loop
- Any web UI, Next.js app or API route
- Authentication, leaderboards, daily challenge
- Any AI or LLM integration
- Any franchise logo, crest, kit colour or player photograph. IPL and BCCI marks are
  aggressively enforced and player likeness rights are live litigation territory in India.
  **Text only.**
