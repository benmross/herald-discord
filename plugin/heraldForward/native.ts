import { IpcMainInvokeEvent } from "electron";

// Runs in Discord's main process, because the renderer's content security
// policy blocks requests to arbitrary hosts.
export async function post(_: IpcMainInvokeEvent, url: string, token: string, body: string) {
    try {
        const res = await fetch(url, {
            method: "POST",
            headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
            body,
        });
        return { status: res.status };
    } catch (e) {
        return { status: 0, error: String(e) };
    }
}
