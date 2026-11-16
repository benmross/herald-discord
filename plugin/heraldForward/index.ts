import { definePluginSettings } from "@api/Settings";
import definePlugin, { OptionType, PluginNative } from "@utils/types";
import { ChannelStore, GuildStore, UserStore } from "@webpack/common";

const Native = VencordNative.pluginHelpers.HeraldForward as PluginNative<typeof import("./native")>;

const settings = definePluginSettings({
    url: { type: OptionType.STRING, description: "Herald ingest URL", default: "" },
    token: { type: OptionType.STRING, description: "Herald bearer token", default: "" },
});

const MAX_QUEUE = 5000;
const FLUSH_MS = 2000;
let queue: any[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
let inventoryTimer: ReturnType<typeof setTimeout> | null = null;

// Inspect only stores the client already loaded. Never request guilds, channels,
// history, members or subscriptions from Discord, and never mark messages read.
function inventory() {
    const guilds = Object.values(GuildStore.getGuilds()).map(g => ({ id: g.id, name: g.name }));
    const channels = guilds.flatMap(g =>
        Object.values(ChannelStore.getMutableGuildChannelsForGuild(g.id))
            .filter(c => c.name && c.name !== "___hidden___").map(c => ({
            id: c.id, name: c.name, guild_id: g.id, type: c.type, parent_id: c.parent_id,
        })));
    enqueue({ type: "inventory", guilds, channels, ts: new Date().toISOString() });
}

function queueInventory() {
    if (!inventoryTimer) inventoryTimer = setTimeout(() => {
        inventoryTimer = null;
        inventory();
    }, 15000);
}

function describe(channelId: string, guildId?: string) {
    const channel: any = ChannelStore.getChannel(channelId);
    const dm = !!channel && (channel.type === 1 || channel.type === 3);
    let name: string | undefined = channel?.name;
    if (dm && !name) {
        name = (channel.recipients ?? [])
            .map((id: string) => (UserStore.getUser(id) as any)?.globalName ?? UserStore.getUser(id)?.username)
            .join(", ");
    }
    return { dm, channel: name, guild: guildId ? GuildStore.getGuild(guildId)?.name : undefined };
}

function toEvent(type: string, message: any, guildId?: string) {
    const channelId = message.channel_id ?? message.channelId;
    const author = message.author && {
        id: message.author.id,
        name: message.author.globalName ?? message.author.global_name ?? message.author.username,
    };
    const ts = message.timestamp?.toISOString?.() ?? message.timestamp;
    return {
        type, id: message.id, channel_id: channelId, guild_id: guildId ?? message.guild_id,
        ...describe(channelId, guildId ?? message.guild_id),
        author, content: message.content, ts,
        attachments: (message.attachments ?? []).map((a: any) => a.filename),
        attachment_urls: (message.attachments ?? []).map((a: any) => a.url).filter(Boolean),
        embeds: (message.embeds ?? []).map((e: any) => ({
            title: e.title, description: e.description, url: e.url,
            fields: (e.fields ?? []).map((f: any) => ({ name: f.name, value: f.value })),
            image: e.image?.url, thumbnail: e.thumbnail?.url,
        })),
        reply_to: message.message_reference?.message_id,
    };
}

function enqueue(ev: any) {
    if (queue.length >= MAX_QUEUE) queue.shift();
    queue.push(ev);
    if (!timer) timer = setTimeout(flush, FLUSH_MS);
}

async function flush() {
    timer = null;
    if (!queue.length || !settings.store.url || !settings.store.token) return;
    const batch = queue;
    queue = [];
    const res = await Native.post(settings.store.url, settings.store.token, JSON.stringify({ events: batch }));
    if (res.status !== 200) {
        // Keep the batch for the next attempt, newest events last.
        queue = batch.concat(queue).slice(-MAX_QUEUE);
        timer = setTimeout(flush, 15000);
    }
}

export default definePlugin({
    name: "HeraldForward",
    description: "Forwards every message this client receives to a Herald server.",
    authors: [{ name: "Herald", id: 0n }],
    settings,
    start: queueInventory,
    stop() {
        if (inventoryTimer) clearTimeout(inventoryTimer);
        inventoryTimer = null;
        if (timer) clearTimeout(timer);
        timer = null;
    },
    flux: {
        GUILD_CREATE: queueInventory,
        GUILD_DELETE: queueInventory,
        CHANNEL_CREATE: queueInventory,
        CHANNEL_UPDATE: queueInventory,
        CHANNEL_DELETE: queueInventory,
        MESSAGE_CREATE({ message, optimistic, guildId }: any) {
            if (optimistic || !message) return;
            enqueue(toEvent("create", message, guildId));
        },
        // History the client pages in by itself, as someone scrolls a channel.
        // This never asks Discord for anything; it keeps what was already fetched.
        LOAD_MESSAGES_SUCCESS({ channelId, messages }: any) {
            const guildId = (ChannelStore.getChannel(channelId) as any)?.guild_id;
            for (const message of messages ?? []) {
                if (message.content != null) enqueue(toEvent("create", { ...message, channel_id: channelId }, guildId));
            }
        },
        MESSAGE_UPDATE({ message, guildId }: any) {
            if (!message || message.content == null) return;
            enqueue(toEvent("update", message, guildId));
        },
        MESSAGE_DELETE({ id, channelId, guildId }: any) {
            enqueue({ type: "delete", id, channel_id: channelId, guild_id: guildId });
        },
    },
});
