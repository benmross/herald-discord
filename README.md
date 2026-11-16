# herald-discord

A [Herald](https://github.com/benmross/herald) extension. Messages from every
Discord server and DM your own client can see, kept as `source='discord',
kind='message'` facts, with selected club channels read for events and
opportunities.

```bash
herald ext install https://github.com/benmross/herald-discord
herald ext setup discord
```

Pieces: `bin/herald-discord-webhook` (listens on `discord.webhook_bind`, port
8791, bearer token `discord.webhook_token`), `lib/discord_ingest.py` (events to
facts), and `plugin/heraldForward/` (a Vencord user plugin that forwards the
client's MESSAGE_CREATE, MESSAGE_UPDATE and MESSAGE_DELETE events).

## Installing the plugin

On a machine that runs Discord:

1. Build Vencord from source with `plugin/heraldForward` copied to
   `src/userplugins/heraldForward`, using `pnpm buildStandalone`, which makes
   the `vencordDesktop*` files Vesktop loads. Point Vesktop at `dist` with
   `vencordDir` in `~/.config/vesktop/state.json` (not settings.json).
2. In the plugin's settings set the URL to `http://<webhook address>:8791/ingest`
   and the token to the value of `discord.webhook_token`.

The plugin also forwards any history the client pages in itself
(LOAD_MESSAGES_SUCCESS), so scrolling a channel backfills it, and attachment CDN
URLs are kept in `data.attachment_urls`. Those URLs are signed and expire.

Messages arrive only while that client is running and online. In very large
servers Discord may not deliver every channel to a client that is not looking
at it.

## Selected club announcements

`discord.watch_channels` explicitly selects channel IDs and their guild IDs,
with `events` and `opportunities` flags. `herald collect discord_watch` routes
only those already received messages into private event messages and opportunity
leads. DMs, deleted messages and messages containing any of
`discord.exclude_terms` are excluded.
Every source message retains its Discord link. Edits are read again.

`herald cycle discord_events` reads new announcements together in one bounded
session, including attachment images and embed text, using `discord.read_engine`
or the install's chosen engine. It makes no call when nothing changed. Its
timer runs twice an hour. Images use already received Discord CDN links, without
account credentials or redirects. Missing image details are left for retry.
The initial backlog is bounded by `discord.read_limit` (default 100).

With the [umd](https://github.com/benmross/herald-umd) extension, event
readings enter its campus review through `lib/discord_source.py`, listed in
`umd.extra_sources`, and `discord.after_events_collect` set to `umd_events`
publishes new ones straight away.
Opportunity leads enter the ordinary opportunities collector and Scout. Image
readings preserve stated eligibility and deadlines without assuming eligibility.
Private source content stays marked private, including when merged into a
public listing. A message deletion is not evidence of an event cancellation.

The plugin also forwards server/channel metadata from its existing cache and
embeds delivered with messages. It does not fetch history, add subscriptions,
join servers, send, react or mark messages read. Coverage is always partial:
the client must be online, and Discord need not deliver unloaded channels.
No user token is read or used by the integration. The existing Vesktop/Vencord
client itself is outside Discord's supported clients; passive delivery cannot
be represented as a guarantee against account enforcement.

## Delivery delay observation

`discord_delivery/state` records when observation began. For each newly stored
message in a monitored guild/channel, `discord_delivery/receipt` permanently
records the original Discord send time, first ledger write time and delay.
Duplicates, edits, history reloads and deletion cannot replace that first write.
Existing messages are not backfilled with guessed first-arrival times.

Run `herald-discord-latency --hours 24` to get measured message counts, median,
95th percentile, maximum delay, per-channel counts and links to the slowest
messages. The default significant delay is five minutes; change the report's
threshold with `--significant-seconds`. All timings are retained regardless
of that threshold. Reports are local, read-only and make no model calls.

Newly imported history sent before observation began is counted separately,
as are unknown send times and negative delays indicating clock inconsistency.
Zero measured delays cannot prove that no messages were permanently missed:
there is no independent census of what the client never received. This measures
arrival in the ledger, separately from scheduled AI and calendar processing.
It changes no Discord notification, subscription or channel-loading behavior.
