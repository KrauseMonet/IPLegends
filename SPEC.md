# IPL Draft Game — Phase 1 (Data Pipeline)

Source of truth for this phase. Amendments ratified 2026-07-29 are folded in and marked
**[A1]**–**[A8]**. Where an amendment supersedes the original brief, only the amended
text remains.

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
eligible season. Kochi Tuskers Kerala played one season, Gujarat Lions two, Pune Warriors
three — this is a real case, not an edge condition.

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

**4.2 Season labels.** `info.season` is inconsistent — sometimes `2011`, sometimes
`2007/08` or `2020/21`. **Derive `season_year` from match dates.** Store the raw label
alongside for reference only.

**4.3 Franchise renames.** Build a `franchise_id` stable across renames, but store the
era-correct `display_name` per season.

| Names over time | Treatment |
|---|---|
| Royal Challengers Bangalore → Royal Challengers Bengaluru | Same franchise |
| Delhi Daredevils → Delhi Capitals | Same franchise |
| Kings XI Punjab → Punjab Kings | Same franchise |
| Rising Pune Supergiants → Rising Pune Supergiant | Same franchise (dropped "s") |
| Deccan Chargers, Sunrisers Hyderabad | **Separate** franchises |
| Gujarat Lions, Gujarat Titans | **Separate** franchises |

Do not guess at any mapping not listed. Extract the distinct team-name-by-year list from
the data first and confirm the rest by hand.

**4.4 Super overs.** Appear as additional innings. Exclude from all player statistics.
Flag the match.

**4.5 Rain-reduced matches.** `info.overs` is not always 20, and DLS can leave the two
innings of one match with **different** lengths. Derive scheduled overs **per innings**,
not per match.

**4.6 Abandoned and no-result matches.** May contain zero or partial innings. Must not
break the parser or skew per-match averages.

**4.7 Delivery numbering.** Wides and no-balls produce extra deliveries within an over.
Preserve the true sequence in `ball_no` and store a `legal_ball` flag so balls faced and
overs bowled compute correctly.

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

**`deliveries.innings_scheduled_overs`** — added so the per-innings death boundary (§4.8)
and the §7.1 full-length fitting filter are both computable without re-deriving from the
match. *Pending ratification.*

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
6+**, optionally split by innings. Not the three display phases.

**[A5] Fitting set: full-length 20-over innings only.** Over numbers are not comparable
across innings of different lengths, so reduced innings must not enter the fit. Score
players in reduced innings by mapping to the nearest equivalent state **via balls
remaining**.

For each state compute:
- Expected runs off the bat per ball
- Dismissal probability per ball
- Expected runs remaining in the innings, at that state and at that state with one more
  wicket down. The difference is the cost of a wicket there.

A batter's contribution on a delivery is the actual outcome minus the expected outcome for
that exact state, with dismissals priced by the wicket cost. Sum across a season, divide
by balls faced for a per-ball impact figure. Same for bowlers with signs reversed.

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
2. Every person referenced in `deliveries` and `appearances` exists in `people`.
3. Legal balls per completed over equals 6, except where the innings ended mid-over.
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
