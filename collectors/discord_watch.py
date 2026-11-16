#!/usr/bin/env python
"""Route selected club messages already in the ledger, without contacting Discord."""
import os
import pathlib
import shutil
import sys

EXT = pathlib.Path(__file__).resolve().parents[1]
ROOT = os.environ.get("HERALD_ROOT") or str(pathlib.Path(shutil.which("herald")).resolve().parents[1])
sys.path[:0] = [str(EXT / "lib"), str(pathlib.Path(ROOT) / "lib")]
from herald import collector
from discord_watch import collect as route_messages

NAME = "discord_watch"
REQUIRES = ("discord",)
CADENCE_MINUTES = 25


def collect(con):
    return route_messages(con)

if __name__ == "__main__":
    collector.main(NAME, collect)
