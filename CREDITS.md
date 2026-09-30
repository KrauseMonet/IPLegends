# Credits and data licensing

## Cricsheet

All ball-by-ball match data in this project comes from **[Cricsheet](https://cricsheet.org/)**,
created and maintained by **Stephen Rushe**.

Cricsheet data is made available under the
**[Open Data Commons Open Database License (ODbL) v1.0](https://opendatacommons.org/licenses/odbl/1-0/)**.

### What the ODbL requires of us

The ODbL is a share-alike licence for databases. Three obligations attach:

1. **Attribution.** Any public use of this database, or of works produced from it,
   must credit Cricsheet and Stephen Rushe.
2. **Share-alike.** If we publicly distribute a database *derived from* Cricsheet
   data — which includes the normalised tables and the derived statistics in this
   repository — that derived database must also be offered under the ODbL.
3. **Keep it open.** We may not use technical measures that restrict others from
   using the database in ways the licence permits.

### Practical consequence for this project

Serving a *game* built on top of this data does not by itself trigger redistribution;
the ODbL distinguishes a "Produced Work" (the game, its ratings shown on screen)
from the "Derivative Database" (our tables). Produced Works require attribution only.

**However**, if we ever expose a public data export, a bulk API, or publish the
derived dataset, the share-alike obligation attaches to the whole derived database.
That decision needs a deliberate sign-off, not an accident.

Attribution must appear in the shipped UI regardless.

## Not used

This project does **not** scrape or ingest data from ESPNcricinfo, Transfermarkt,
Howstat, or any comparable site. Their terms of use prohibit it.

No kit designs or player photographs are used anywhere in this project.

**[Corrected 2026-09-30 -- this section used to say no franchise crests were used, and
that is no longer true.]** The site now shows the franchises' crests, era by era, to
identify the teams. **There is no licence for them**: the site owner decided to use them
on the basis that this is a non-commercial fan site -- no advertising (Google AdSense was
removed in the same change) and no betting -- having been told plainly that IPL/BCCI
permission could not be confirmed and that each crest belongs to its franchise's owner.
Fifteen come from Wikipedia's file pages and three were supplied by the owner; every one
is listed with its source in `assets/crests/SOURCES.md`. The footer on every page says
the site is unofficial and that team names and logos belong to their owners. If a
rights-holder objects, removing them is one command: delete the entries in
`web/crests.py` and every surface falls back to the old initials badge.
