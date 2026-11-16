import datetime as dt
import json
import pathlib
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parents[1] / "lib"),
               str(pathlib.Path.home() / "herald" / "lib")]
from herald import db
import discord_events as events
import discord_ingest
import discord_watch as watch


class Watch(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)
        self.addCleanup(self.con.close)
        self.rule = {"9": {"guild_id": "7", "events": True, "opportunities": True}}
        values = {"discord.watch_channels": self.rule, "discord.exclude_terms": ["cmsc132"]}
        self.setting = patch.object(watch.config, "get", side_effect=lambda key, default=None:
                                    values.get(key, default))
        self.setting.start()
        self.addCleanup(self.setting.stop)

    def message(self, **changes):
        event = dict(type="create", id="1", channel_id="9", guild_id="7", guild="Club",
                     channel="announcements", author={"name": "Ada"},
                     ts=dt.datetime.now(dt.timezone.utc).isoformat(), content="Meeting tomorrow at 6 PM")
        event.update(changes)
        discord_ingest.ingest(self.con, [event])

    def rows(self, kind):
        return self.con.execute("SELECT * FROM facts WHERE source=? AND kind=?", (watch.SOURCE, kind)).fetchall()

    def test_only_selected_channels_and_guilds_are_routed(self):
        self.message()
        self.message(id="2", channel_id="10")
        self.message(id="3", guild_id="another")
        self.message(id="4", dm=True)
        self.message(id="5", content="CMSC 132 coursework")
        watch.collect(self.con)
        self.assertEqual(len(self.rows("event_message")), 1)
        self.assertEqual(len(self.rows("opportunity_lead")), 1)
        meta = json.loads(self.rows("event_message")[0]["data"])
        self.assertTrue(meta["private"])
        self.assertEqual(meta["url"], "https://discord.com/channels/7/9/1")

    def test_embeds_and_image_only_announcements_are_not_discarded(self):
        self.message(content="", embeds=[{"title": "Event", "description": "Friday", "url": "https://example.org"}])
        self.message(id="2", content="", attachments=["flyer.jpg"], attachment_urls=["https://cdn.discordapp.com/flyer.jpg"])
        watch.collect(self.con)
        self.assertEqual(len(self.rows("event_message")), 2)
        self.assertTrue(any("Friday" in r["body"] for r in self.rows("event_message")))

    def test_deleted_or_unselected_message_loses_its_derived_leads(self):
        self.message()
        watch.collect(self.con)
        discord_ingest.ingest(self.con, [dict(type="delete", id="1", channel_id="9")])
        watch.collect(self.con)
        self.assertEqual(self.rows("opportunity_lead"), [])
        self.assertEqual(self.rows("event_message"), [])

    def test_inventory_is_a_snapshot_without_message_bodies(self):
        discord_ingest.ingest(self.con, [dict(type="inventory", guilds=[{"id": "7", "name": "Club"}], channels=[])])
        row = self.con.execute("SELECT * FROM facts WHERE source='discord' AND kind='inventory'").fetchone()
        self.assertEqual(json.loads(row["data"])["guilds"][0]["name"], "Club")
        self.assertEqual(self.con.execute("SELECT count(*) FROM facts WHERE kind='message'").fetchone()[0], 0)

    def test_read_once_edited_again_and_bad_output_does_not_consume_work(self):
        self.message()
        watch.collect(self.con)
        messages = events.unread(self.con)
        prompt, ids = events.prompt(messages)
        self.assertIn("Meeting tomorrow", prompt)
        event = dict(title="Workshop", start="2026-11-12T18:00:00-05:00", end="", location="",
                     host="Club", description="Workshop", url="", all_day=False, cancelled=False)
        answer = {"messages": [{"id": "M0", "reason": "Workshop announced.", "events": [event], "opportunity_text": ""}]}
        bad = json.loads(json.dumps(answer))
        bad["messages"][0]["events"][0]["start"] = "2026-11-12T18:00:00"
        with self.assertRaises(ValueError):
            events.store(self.con, bad, ids, "test")
        self.assertEqual(len(events.unread(self.con)), 1)
        self.assertEqual(events.store(self.con, answer, ids, "test"), (1, 1))
        self.assertEqual(events.unread(self.con), [])
        self.message(type="update", content="Meeting moved to 7 PM")
        watch.collect(self.con)
        self.assertEqual(len(events.unread(self.con)), 1)
        # An answer to the old version cannot consume the newer announcement.
        events.store(self.con, answer, ids, "test")
        self.assertEqual(len(events.unread(self.con)), 1)

    def test_unanswered_image_is_delayed_but_an_edit_bypasses_backoff(self):
        self.message(attachments=["flyer.jpg"])
        watch.collect(self.con)
        _, ids = events.prompt(events.unread(self.con))
        self.assertEqual(events.store(self.con, {"messages": []}, ids, "test"), (0, 0))
        self.assertEqual(events.unread(self.con), [])
        self.assertEqual(self.rows("read"), [])
        self.message(type="update", content="The event is November 12 at 6 PM")
        watch.collect(self.con)
        self.assertEqual(len(events.unread(self.con)), 1)

    def test_raw_edit_before_the_next_collector_invalidates_a_model_answer(self):
        self.message()
        watch.collect(self.con)
        _, ids = events.prompt(events.unread(self.con))
        self.message(type="update", content="Changed while the model was reading")
        answer = {"messages": [{"id": "M0", "reason": "No event", "events": [], "opportunity_text": ""}]}
        self.assertEqual(events.store(self.con, answer, ids, "test"), (0, 0))
        self.assertEqual(self.rows("read"), [])


if __name__ == "__main__":
    unittest.main()
