"""Turn the events the Vencord plugin posts into facts.

Kept apart from the HTTP server so it can be tested without a socket. A message
is a source fact keyed on channel and message id, so a retried batch upserts the
same rows. An edit overwrites the body; a delete keeps the body and marks the
row, because that a message existed and was removed is itself information.
"""

from __future__ import annotations

import json

from herald import db
import discord_latency

NAME = "discord"


def _title(ev: dict) -> str:
    author = (ev.get("author") or {}).get("name") or "?"
    where = ev.get("channel") or ev.get("channel_id")
    if ev.get("dm"):
        return f"{author} in {where}"
    guild = ev.get("guild")
    return f"{author} in {guild} #{where}" if guild else f"{author} in #{where}"


def ingest(con, events: list[dict]) -> int:
    n = 0
    for ev in events:
        if ev.get("type") == "inventory":
            if isinstance(ev.get("guilds"), list) and isinstance(ev.get("channels"), list):
                db.put_fact(con, NAME, "inventory", external_id="client", ts=ev.get("ts"),
                            title="Servers and channels cached by the Discord client",
                            data={"guilds": ev["guilds"], "channels": ev["channels"],
                                  "coverage": "client cache only; no history requests"})
            continue
        kind, mid, cid = ev.get("type"), ev.get("id"), ev.get("channel_id")
        if not (kind and mid and cid):
            continue
        ext = f"{cid}:{mid}"
        if kind == "delete":
            con.execute(
                "UPDATE facts SET data = json_set(COALESCE(data,'{}'),'$.deleted',1) "
                "WHERE source=? AND kind='message' AND external_id=?", (NAME, ext))
            n += 1
            continue
        if kind not in ("create", "update") or ev.get("content") is None:
            continue
        # An edit event can arrive without an author or a timestamp.
        old = con.execute(
            "SELECT ts, title, data FROM facts WHERE source=? AND kind='message' "
            "AND external_id=?", (NAME, ext)).fetchone()
        prior = json.loads(old["data"]) if old and old["data"] else {}
        ts = ev.get("ts") or (old["ts"] if old else None)
        title = _title(ev) if ev.get("author") else (old["title"] if old else _title(ev))
        data = {**prior,
                "guild_id": ev.get("guild_id") or prior.get("guild_id"),
                "guild": ev.get("guild") or prior.get("guild"),
                "channel_id": cid,
                "channel": ev.get("channel") or prior.get("channel"),
                "author": ev.get("author") or prior.get("author"),
                "dm": bool(ev.get("dm", prior.get("dm"))),
                "attachments": ev.get("attachments") or prior.get("attachments"),
                "attachment_urls": ev.get("attachment_urls") or prior.get("attachment_urls"),
                "reply_to": ev.get("reply_to") or prior.get("reply_to"),
                "embeds": ev.get("embeds", prior.get("embeds", [])),
                "edited": kind == "update" or prior.get("edited", False)}
        db.put_fact(con, NAME, "message", external_id=ext, ts=ts, title=title,
                    body=ev["content"], data=data)
        discord_latency.record(con, ext, ts, data, already_known=old is not None, event_type=kind)
        n += 1
    con.commit()
    return n
