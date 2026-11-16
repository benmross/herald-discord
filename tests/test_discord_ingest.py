import pathlib, sys, unittest, sqlite3
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
ROOT = pathlib.Path.home() / "herald"
sys.path.insert(0, str(ROOT / "lib"))


class IngestTest(unittest.TestCase):
    def setUp(self):
        from herald import db
        self.db = db
        # Ingestion only needs a database. A scratch HERALD_HOME would also
        # repoint the live checkout's ledger symlink through ensure_dirs().
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(db.SCHEMA)
        self.addCleanup(self.con.close)
        import discord_ingest
        self.ing = discord_ingest

    def row(self):
        return self.con.execute("SELECT * FROM facts WHERE source='discord'").fetchall()

    def test_create_edit_delete(self):
        ev = {"type": "create", "id": "1", "channel_id": "9", "guild": "G", "channel": "general",
              "author": {"id": "5", "name": "Ada"}, "content": "hi", "ts": "2026-10-20T14:00:00+00:00"}
        self.ing.ingest(self.con, [ev, ev])
        self.assertEqual(len(self.row()), 1)
        self.ing.ingest(self.con, [{"type": "update", "id": "1", "channel_id": "9", "content": "hi!"}])
        r = self.row()[0]
        self.assertEqual(r["body"], "hi!")
        self.assertEqual(r["title"], "Ada in G #general")
        self.assertEqual(r["ts"], "2026-10-20T14:00:00+00:00")
        self.ing.ingest(self.con, [{"type": "delete", "id": "1", "channel_id": "9"}])
        self.assertIn('"deleted":1', self.row()[0]["data"])
        self.assertEqual(self.row()[0]["body"], "hi!")


if __name__ == "__main__":
    unittest.main()
