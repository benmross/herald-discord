"""Private Discord event readings, merged by the normal campus review pipeline."""
import json
from herald import db
from umd_scrape.common import make_candidate


def collect(cfg, window_start, window_end, known_keys=()):
    with db.session() as con:
        rows = con.execute("SELECT e.* FROM facts e JOIN facts m ON m.source=e.source "
            "AND m.kind='event_message' AND m.external_id=json_extract(e.data,'$.message_key') "
            "WHERE e.source='discord_watch' AND e.kind='event' AND e.ts>=? AND e.ts<? "
            "AND coalesce(json_extract(e.data,'$.superseded'),0)=0",
            (window_start.isoformat()[:10], window_end.isoformat()[:10])).fetchall()
    out = []
    for r in rows:
        d = json.loads(r["data"])
        out.append(make_candidate(r["external_id"], r["title"], r["ts"], d.get("end") or None,
            "discord", url=d.get("url") or d["message_url"], location=d.get("location") or "",
            host=d.get("host") or d.get("guild") or "", all_day=d["all_day"],
            description=(r["body"] or "") + f"\n\nPrivate source: {d['guild']} #{d['channel']}\n{d['message_url']}",
            status=d["status"]))
    return out
