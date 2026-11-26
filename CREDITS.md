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

## Auction prices [A317]

The archive holds no prices, so the Almanack's auction pages take every sale -- player, buying
franchise, base price, price paid -- from Wikipedia's season pages ("List of YYYY Indian Premier
League personnel changes"), read on 25 November 2026. Only facts are taken (who was sold, to whom,
for what); no Wikipedia text is reproduced, and each auction page names Wikipedia as its source.
Wikipedia's text is CC BY-SA 4.0; facts themselves are not copyrightable, and the attribution is
given anyway.

For 2021 to 2026 every sale was then checked by hand against the IPL's own sold lists on
iplt20.com, read in a browser like any visitor would (the site's data API was not used). That
check corrected six prices and added two sales Wikipedia's tables had missed; each correction is a
row in `etl/overrides/auction_history.csv` with the source named in its `note`. **The IPL site's
terms of use were not reviewed before reading it**; the figures taken from it are eight
individually cited facts, not a copy of its lists.

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
is listed with its source in `assets/crests/SOURCES.md`.

**[Changed 2026-10-24 -- the "no advertising" basis above no longer holds.]** The owner
chose to put Google AdSense back on the site while keeping the crests, having been told
that ads take away the non-commercial basis described above and that AdSense's own review
may object to pages built around other owners' logos. The crests are still unlicensed;
the risk is accepted rather than resolved. The footer on every page says
the site is unofficial and that team names and logos belong to their owners. If a
rights-holder objects, removing them is one command: delete the entries in
`web/crests.py` and every surface falls back to the old initials badge.
