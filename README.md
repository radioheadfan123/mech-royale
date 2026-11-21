# mech-royale

Bot for the Builders Buildathon Mech Royale track. `bot.py` plays, `world.py` rasterizes the map for line of sight and steering. Check: `python3 check/check.py -- python3 -u bot.py`.

## Credit: giddy (Go bot)

The `giddy/` folder is giddygiddyboy's Mech Royale bot, used under its MIT License (kept unchanged in `giddy/LICENSE`, Copyright (c) 2026 giddygiddyboy).

- Original: https://github.com/justnau/giddygiddyboy-mech-royale (now 404), commit `e19e3c8d45d980df35c2fa5318353aaeb7c515bd`
- Copied from the vendored copy at https://github.com/charliescull/mech-royale-bot, folder `drivers/giddy/` (go.mod, cmd/, mech/)
- The Dockerfile builds it with `golang:1.26.1-bookworm` pinned by digest into a `scratch` image.

Every change we make on top of it is listed in this section and in the git history of `giddy/`.
- G7 longer retreat (ours): after the lose rule fires, retreat for 6 s instead of 1.5 s, so regen can start.
- G8 retreat until healed (ours): on top of G7, keep retreating up to 20 s after losing while under 70% HP.
