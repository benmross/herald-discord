#!/usr/bin/env python
"""Read new monitored announcements in one session. --dry-run makes no model call."""
import fcntl
import os
import pathlib
import shutil
import subprocess
import sys

EXT = pathlib.Path(__file__).resolve().parents[1]
ROOT = os.environ.get("HERALD_ROOT") or str(pathlib.Path(shutil.which("herald")).resolve().parents[1])
sys.path[:0] = [str(EXT / "lib"), str(pathlib.Path(ROOT) / "lib")]
from herald import config, db, think
import discord_events as events
import discord_media
from discord_watch import collect as route_messages


def main():
    config.RAW.mkdir(parents=True, exist_ok=True)
    with (config.RAW / "discord-events.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Discord reading is already running")
            return 0
        with db.session() as con:
            messages = events.unread(con, int(config.get("discord.read_limit", 100)))
        print(f"discord_events: {len(messages)} unread announcements", flush=True)
        if not messages or "--dry-run" in sys.argv:
            return 0
        for message in messages:
            message["images"], message["unavailable_images"] = discord_media.images(message)
        prompt, ids = events.prompt(messages)
        engine = config.get("discord.read_engine") or config.get("engines.default_engine")
        model = config.get("discord.read_model") or config.get(f"engines.{engine}.model")
        result = think.think(prompt, label="cycle:discord_events", engine=engine, model=model,
            effort=config.get("discord.read_effort") or config.get(f"engines.{engine}.effort"),
            cwd=config.RAW / "discord-images", allowed_tools=["Read"],
            permission_mode="default", json_schema=events.SCHEMA, timeout=1500)
        if not result.ok:
            print(f"Discord reading failed: {result.error}", file=sys.stderr)
            return 1
        with db.session() as con:
            read, found = events.store(con, result.json(), ids, result.model or model)
            route_messages(con)
        print(f"discord_events: {read} read, {found} events", flush=True)
        # discord.after_events_collect names a collector that publishes events,
        # such as a campus calendar, so new readings reach it straight away.
        after = config.get("discord.after_events_collect")
        if found and after:
            return subprocess.run(["herald", "collect", str(after)], cwd=ROOT).returncode
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
