# Stdlib-only Python bot. Match runs it with no network, read-only root, 1 CPU, 512 MB, map at /arena.
FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py world.py bot.py ./
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
