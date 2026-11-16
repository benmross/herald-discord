"""Immutable first-write timing for received messages in monitored channels.

These observations cannot detect a message that never reaches this client.
They deliberately do not treat a later edit or history reload as first arrival.
"""
from __future__ import annotations

import datetime as dt
import json
import math

from herald import config

SOURCE = "discord_delivery"
DEFAULT_SIGNIFICANT_SECONDS = 300


def now():
    return dt.datetime.now(dt.timezone.utc)


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value)
        return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _insert_once(con, kind, key, at, title, data):
    # put_fact is an upsert; timing must survive retries, including concurrent
    # duplicate batches. The shared facts uniqueness constraint is sufficient.
    con.execute("INSERT OR IGNORE INTO facts "
                "(source,kind,external_id,ts,title,data,ingested_at) VALUES(?,?,?,?,?,?,?)",
                (SOURCE, kind, key, at, title, json.dumps(data, sort_keys=True), at))


def start_observation(con):
    started = now().isoformat(timespec="milliseconds")
    _insert_once(con, "state", "observation", started, "Discord delivery observation",
                 {"started_at": started, "significant_seconds": DEFAULT_SIGNIFICANT_SECONDS,
                  "channels_at_start": len(config.get("discord.watch_channels", {}) or {}),
                  "scope": "First ledger writes of monitored messages; no independent missing-message census"})
    return json.loads(con.execute("SELECT data FROM facts WHERE source=? AND kind='state' "
                                  "AND external_id='observation'", (SOURCE,)).fetchone()[0])


def record(con, key, sent_at, data, *, already_known, event_type):
    if already_known:
        # Existing ingested_at values may have been overwritten by old edits;
        # reconstructing an earlier first arrival from them would invent data.
        return
    channels = config.get("discord.watch_channels", {}) or {}
    channel_id = str(data.get("channel_id"))
    rule = channels.get(channel_id)
    if (not rule or data.get("dm")
            or str(rule.get("guild_id")) != str(data.get("guild_id"))):
        return
    start_observation(con)
    first = now()
    sent = timestamp(sent_at)
    at = first.isoformat(timespec="milliseconds")
    mid = key.split(":")[-1]
    _insert_once(con, "receipt", key, at, f"{data.get('guild')} #{data.get('channel')}", {
        "message_key": key, "guild_id": data.get("guild_id"), "guild": data.get("guild"),
        "channel_id": channel_id, "channel": data.get("channel"),
        "message_url": f"https://discord.com/channels/{data['guild_id']}/{channel_id}/{mid}",
        "sent_at": sent.isoformat(timespec="milliseconds") if sent else None,
        "first_ingested_at": at,
        "delay_seconds": round((first - sent).total_seconds(), 3) if sent else None,
        "first_event_type": event_type,
    })


def report(con, *, hours=24, significant_seconds=DEFAULT_SIGNIFICANT_SECONDS, at=None):
    if not math.isfinite(hours) or hours <= 0 or not math.isfinite(significant_seconds) or significant_seconds <= 0:
        raise ValueError("hours and significant_seconds must be positive finite numbers")
    at = at or now()
    row = con.execute("SELECT data FROM facts WHERE source=? AND kind='state' "
                      "AND external_id='observation'", (SOURCE,)).fetchone()
    state = json.loads(row[0]) if row else {}
    started = timestamp(state.get("started_at"))
    since = max(at - dt.timedelta(hours=hours), started) if started else at - dt.timedelta(hours=hours)
    measured, older, invalid, skew = [], 0, 0, 0
    for row in con.execute("SELECT data FROM facts WHERE source=? AND kind='receipt'", (SOURCE,)):
        data = json.loads(row[0])
        first, sent = timestamp(data.get("first_ingested_at")), timestamp(data.get("sent_at"))
        if not first or not since <= first <= at:
            continue
        if not sent or data.get("delay_seconds") is None:
            invalid += 1
        elif started and sent < started:
            older += 1
        elif data["delay_seconds"] < 0:
            skew += 1
        else:
            measured.append(data)
    delays = sorted(d["delay_seconds"] for d in measured)
    late = sorted((d for d in measured if d["delay_seconds"] >= significant_seconds),
                  key=lambda d: d["delay_seconds"], reverse=True)

    def percentile(p):
        return delays[max(0, math.ceil(p * len(delays)) - 1)] if delays else None

    by_channel = {}
    for data in measured:
        entry = by_channel.setdefault(data["channel_id"], {"guild": data.get("guild"),
            "channel": data.get("channel"), "messages": 0, "significant_delays": 0, "max_seconds": 0})
        entry["messages"] += 1
        entry["significant_delays"] += int(data["delay_seconds"] >= significant_seconds)
        entry["max_seconds"] = max(entry["max_seconds"], data["delay_seconds"])
    return {"observation_started_at": state.get("started_at"), "window_start": since.isoformat(),
            "window_end": at.isoformat(), "significant_seconds": significant_seconds,
            "messages_measured": len(measured), "significant_delays": len(late),
            "median_seconds": percentile(0.5), "p95_seconds": percentile(0.95),
            "max_seconds": delays[-1] if delays else None,
            "older_messages_first_seen": older, "missing_send_time": invalid, "negative_clock_delays": skew,
            "by_channel": list(by_channel.values()), "slowest_messages": sorted(measured,
                key=lambda d: d["delay_seconds"], reverse=True)[:20],
            "limitations": "Only received messages are measurable. Zero delays is not proof that no messages were permanently missed. "
                "Messages sent before observation began are counted separately. This measures ledger arrival, not later AI/calendar processing."}
