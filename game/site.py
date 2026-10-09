"""The site's name and address, in one place. [A187]

The address used to be typed into SEVEN files (every game's share line, the daily's, the
flashback and puzzle scripts, the keep-warm workflow), so moving the site meant finding them
all and a missed one would have shared a dead link. Python reads it from here; the one place
the browser needs it is `SITE_ORIGIN` in `web/static/common.js`, and a test fails if the two
ever disagree.

Share links name the REAL site rather than whatever host a page happens to be served from
(A129's reasoning): a result pasted into a chat must work for whoever reads it, and a link
built from the request would carry a preview deployment's address into other people's messages.
"""

SITE_NAME = "Fine Leg XI"
SITE_HOST = "finelegxi.in"
SITE_ORIGIN = f"https://{SITE_HOST}"
