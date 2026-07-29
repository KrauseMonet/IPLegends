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

No IPL, BCCI, or franchise trademarks, crests, kit designs, or player photographs
are used anywhere in this project. Text only.
