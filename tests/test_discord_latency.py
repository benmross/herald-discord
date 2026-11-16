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
import discord_ingest as ingest
import discord_latency as latency


class DeliveryTiming(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)
        self.addCleanup(self.con.close)
        self.start = dt.datetime(2026, 11, 11, 18, 30, tzinfo=dt.timezone.utc)
        self.clock = patch.object(latency, "now", return_value=self.start).start()
        self.addCleanup(patch.stopall)
        self.rules = {"9": {"guild_id": "7"}}
        patch.object(latency.config, "get", side_effect=lambda key, default=None:
                     self.rules if key == "discord.watch_channels" else default).start()
        latency.start_observation(self.con)

    def message(self, mid="1", *, delay=2, sent_seconds=10, **extra):
        sent = self.start + dt.timedelta(seconds=sent_seconds)
        self.clock.return_value = sent + dt.timedelta(seconds=delay)
        event = {"type": "create", "id": mid, "channel_id": "9", "guild_id": "7",
                 "guild": "Club", "channel": "announcements", "author": {"name": "Ada"},
                 "content": "A message", "ts": sent.isoformat()}
        event.update(extra)
        ingest.ingest(self.con, [event])

    def receipts(self):
        return self.con.execute("SELECT * FROM facts WHERE source=? AND kind='receipt'", (latency.SOURCE,)).fetchall()

    def report(self):
        return latency.report(self.con, at=self.start + dt.timedelta(hours=24))

    def test_first_write_survives_edits_reloads_deletes_and_duplicate_delivery(self):
        self.message()
        original = dict(self.receipts()[0])
        self.message(delay=3600, content="History reload")
        self.message(delay=7200, type="update", content="Edited")
        ingest.ingest(self.con, [{"type": "delete", "id": "1", "channel_id": "9"}])
        self.assertEqual(dict(self.receipts()[0]), original)
        data = json.loads(original["data"])
        self.assertEqual(data["delay_seconds"], 2)
        self.assertEqual(data["message_url"], "https://discord.com/channels/7/9/1")
        # Even two callers both claiming a new message cannot replace the first receipt.
        latency.record(self.con, "9:1", self.start.isoformat(), data,
                       already_known=False, event_type="create")
        self.assertEqual(dict(self.receipts()[0]), original)

    def test_report_counts_significant_delays_and_preserves_evidence(self):
        for mid, delay in enumerate((1, 2, 299, 300, 900), 1):
            self.message(str(mid), delay=delay)
        report = self.report()
        self.assertEqual(report["messages_measured"], 5)
        self.assertEqual(report["significant_delays"], 2)
        self.assertEqual(report["median_seconds"], 299)
        self.assertEqual(report["p95_seconds"], 900)
        self.assertEqual(report["max_seconds"], 900)
        self.assertEqual(report["slowest_messages"][0]["message_url"], "https://discord.com/channels/7/9/5")
        self.assertEqual(report["by_channel"][0]["significant_delays"], 2)

    def test_old_history_and_missing_or_future_send_times_do_not_fake_delays(self):
        self.message("old", sent_seconds=-3600, delay=3700)
        self.message("unknown", ts=None)
        self.message("naive", ts="2026-11-11T18:30:10")
        self.message("skew", delay=-2)
        report = self.report()
        self.assertEqual(report["older_messages_first_seen"], 1)
        self.assertEqual(report["missing_send_time"], 2)
        self.assertEqual(report["negative_clock_delays"], 1)
        self.assertEqual(report["messages_measured"], 0)
        self.assertIsNone(report["max_seconds"])

    def test_existing_messages_are_not_backfilled_with_invented_first_arrivals(self):
        db.put_fact(self.con, "discord", "message", external_id="9:1", ts=self.start.isoformat(),
                    body="Already in the ledger", data={"guild_id": "7", "channel_id": "9"})
        self.message()
        self.assertEqual(self.receipts(), [])

    def test_scope_is_selected_guild_and_channel_without_dms(self):
        self.message("other-channel", channel_id="10")
        self.message("wrong-guild", guild_id="8")
        self.message("dm", dm=True)
        self.assertEqual(self.receipts(), [])

    def test_restart_preserves_observation_boundary_and_report_is_readonly(self):
        first = latency.start_observation(self.con)
        self.clock.return_value += dt.timedelta(days=1)
        self.assertEqual(latency.start_observation(self.con), first)
        self.con.commit()
        self.con.execute("PRAGMA query_only=ON")
        self.assertEqual(self.report()["observation_started_at"], first["started_at"])


if __name__ == "__main__":
    unittest.main()
