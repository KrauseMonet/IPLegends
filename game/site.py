"""The site's name and address, in one place. [A187, held back by A193]

**This file carries the OLD identity on purpose.** The Fine Leg XI rename (A187, finelegxi.in) was
built and committed but not deployed, because the domain did not resolve and every share link would
have been dead. The puzzle games added afterwards (Common Ground, A188) import their name and
address from here, so this file is the one thing that has to change when the rename ships --
`SITE_NAME = "Fine Leg XI"`, `SITE_HOST = "finelegxi.in"` -- alongside reverting A193's hold-back
commit, which restores every page, image and script to the new identity.

The older games (Bingo, Guess the Player, Name the XI, the daily) still spell their share lines
out in their own modules, as they did before A187.
"""

SITE_NAME = "Almanack"
SITE_HOST = "iplegends.vercel.app"
SITE_ORIGIN = f"https://{SITE_HOST}"
