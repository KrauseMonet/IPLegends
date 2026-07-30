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

Below a floor a player-season is **not rated at all** — not shrunk, not floored, simply not
draftable from that franchise-season. This is the honest form: below the floor there is not
enough signal to rate the season, so we do not pretend to. Since the deck is franchise-season
grain, the consequence is a player you cannot draft from that specific team-year, which is
the correct outcome rather than a loss. Excluded players stay in `squad_members` for
completeness, they just do not appear as a pick.

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

*First read, post-A33 floors:* the lists pass. Purple Cap winners surface in the right
cohorts across the whole span (Tanvir 2008, RP Singh 2009, Malinga 2011, Faulkner 2013,
Bhuvneshwar 2017, Tahir 2019, Harshal Patel 2021) alongside Gayle 2011, Sehwag 2011, de
Villiers four times, Russell 2019, Karthik 2022 and 2024, Tewatia 2020, and Muralitharan,
Kumble and Warne in the spin-era seasons — famous names, but not *only* famous names.

**One cohort is too thin to normalise and it is recorded now rather than discovered in
§7.4: `death` bowling has 8 rateable seasons, 3 of them DJ Bravo.** This is exactly the
condition A2 gave for refusing per-cell z-scoring, and it arrives one section early. Two
consequences. §7.4's cohort offsets must be pooled across seasons for this cohort or it
will be normalised against essentially one player's career. And **check 12 (slot coverage
after the §7.3 gate) must be written to fail on this**, not to tolerate it — if the deck
cannot field a death bowler from most franchise-seasons, that is a draft-legality problem
and not a presentation one. Do not resolve it by lowering the bowling floor; A33 measured
that floor and 150 is where the bowling list stops moving.

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
    **Currently FAILS at 140 of 166** and correctly so — it is blocked on
    `keepers_by_season.csv` being hand-filled, and the failure is the block being visible.

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

- The simulator or any probability model
- The draft loop
- Any web UI, Next.js app or API route
- Authentication, leaderboards, daily challenge
- Any AI or LLM integration
- Any franchise logo, crest, kit colour or player photograph. IPL and BCCI marks are
  aggressively enforced and player likeness rights are live litigation territory in India.
  **Text only.**
