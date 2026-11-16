"""Route only selected, already received club messages. No Discord connection."""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from herald import config, db

SOURCE = "discord_watch"


def text(body, data):
    parts = [body or ""]
    for embed in data.get("embeds") or []:
        parts.extend(str(embed.get(k) or "") for k in ("title", "description", "url"))
        parts.extend(f"{f.get('name', '')}: {f.get('value', '')}" for f in embed.get("fields") or [])
    return "\n".join(p for p in parts if p.strip())


def version(body, data):
    return hashlib.sha256(json.dumps([body, data.get("attachments"),
                                     data.get("attachment_urls"), data.get("embeds")],
                                    sort_keys=True).encode()).hexdigest()[:20]


def collect(con):
    channels = config.get("discord.watch_channels", {}) or {}
    excluded = config.get("discord.exclude_terms", []) or []
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    readings = {r["external_id"]: json.loads(r["data"] or "{}") for r in con.execute(
        "SELECT external_id,data FROM facts WHERE source=? AND kind='read'", (SOURCE,))}
    leads, events = [], []
    for row in con.execute("SELECT * FROM facts WHERE source='discord' AND kind='message' "
                           "AND ts >= ? ORDER BY ts", (since,)):
        data = json.loads(row["data"] or "{}")
        rule = channels.get(str(data.get("channel_id")))
        if not rule or data.get("dm") or data.get("deleted"):
            continue
        if str(data.get("guild_id")) != str(rule.get("guild_id")):
            continue
        body = text(row["body"], data)
        if not body.strip() and not data.get("attachments"):
            continue
        # discord.exclude_terms keeps matching messages out of every downstream
        # model prompt, e.g. a course whose material must never reach one.
        # Compared without case or spaces, so "CMSC 132" matches "cmsc132".
        squashed = body.lower().replace(" ", "")
        if any(t.lower().replace(" ", "") in squashed for t in excluded if t):
            continue
        guild, channel = data.get("guild"), data.get("channel")
        mid = row["external_id"].split(":")[-1]
        url = f"https://discord.com/channels/{data['guild_id']}/{data['channel_id']}/{mid}"
        meta = {**data, "source_fact": row["id"], "from": f"{guild} #{channel}",
                "url": url, "private": True, "fingerprint": version(body, data)}
        record = dict(external_id=row["external_id"], ts=row["ts"], title=row["title"],
                      body=body, data=meta)
        if rule.get("opportunities"):
            reading = readings.get(row["external_id"], {})
            image_text = reading.get("opportunity_text") if reading.get("fingerprint") == meta["fingerprint"] else ""
            lead = dict(record)
            if image_text:
                lead["body"] += "\n\nReading of opportunity details in the announcement and images:\n" + image_text
            leads.append(lead)
        if rule.get("events"):
            events.append(record)
    db.replace_facts(con, SOURCE, "opportunity_lead", leads)
    db.replace_facts(con, SOURCE, "event_message", events)
    con.commit()
    return {"channels": len(channels), "leads": len(leads), "event_messages": len(events),
            "_sync": {"complete": False, "reason": "passive client delivery only; "
                      "silent or unloaded channels and past history may be missing"}}
