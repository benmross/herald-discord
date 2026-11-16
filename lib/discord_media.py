"""Save images from already received attachment URLs, with no account credentials."""
import hashlib
from io import BytesIO
from urllib.parse import urlsplit

import requests
from PIL import Image

from herald import config

HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
MAX_BYTES = 10_000_000


def images(message):
    meta = message["meta"]
    urls = list(meta.get("attachment_urls") or [])
    urls += [e["image"] for e in meta.get("embeds") or [] if e.get("image")]
    folder = config.RAW / "discord-images"
    folder.mkdir(parents=True, exist_ok=True)
    expected = sum(str(n).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
                   for n in (meta.get("attachments") or [])[:4])
    saved, failed = [], max(0, expected - len(urls[:4]))
    for url in urls[:4]:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (ValueError, TypeError):
            failed += 1
            continue
        if (parsed.scheme != "https" or parsed.hostname not in HOSTS or port not in (None, 443)
                or parsed.username or parsed.password
                or not parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
            failed += 1
            continue
        # Ignore expiring query signatures in the cache key; contents of one
        # Discord attachment path are immutable.
        key = hashlib.sha256((parsed.hostname + parsed.path).encode()).hexdigest()
        path = folder / (key + ".jpg")
        try:
            if not path.exists():
                with requests.get(url, timeout=15, stream=True, allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise ValueError("attachment unavailable")
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("attachment too large")
                        chunks.append(chunk)
                with Image.open(BytesIO(b"".join(chunks))) as im:
                    if im.width * im.height > 20_000_000:
                        raise ValueError("attachment dimensions too large")
                    im.thumbnail((1600, 1600))
                    temp = path.with_suffix(".tmp")
                    im.convert("RGB").save(temp, "JPEG", quality=85)
                    temp.replace(path)
            saved.append(str(path))
        except (OSError, ValueError, requests.RequestException, Image.DecompressionBombError):
            failed += 1
    return saved, failed
