# Stdlib-only Python bot. Match runs it with no network, read-only root, 1 CPU, 512 MB, map at /arena.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
COPY mechfight.py world.py bot.py ./
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
