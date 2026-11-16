from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import discord_media as media


class Images(unittest.TestCase):
    def test_only_exact_https_cdn_hosts_are_requested_without_redirects(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(media.config, "RAW", Path(folder)), \
                patch.object(media.requests, "get") as get:
            urls = ["https://cdn.discordapp.com.evil.invalid/image.png", "http://cdn.discordapp.com/image.png",
                    "https://cdn.discordapp.com:bad/image.png", "https://cdn.discordapp.com:8443/image.png"]
            self.assertEqual(media.images({"meta": {"attachment_urls": urls}}), ([], 4))
            get.assert_not_called()
            buf = BytesIO()
            Image.new("RGB", (20, 20), "red").save(buf, "PNG")
            response = get.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_content.return_value = [buf.getvalue()]
            saved, failed = media.images({"meta": {"attachment_urls": ["https://cdn.discordapp.com/attachments/a/image.png?ex=one"]}})
            self.assertEqual((len(saved), failed), (1, 0))
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
            self.assertNotIn("headers", get.call_args.kwargs)
            get.reset_mock()
            saved_again, _ = media.images({"meta": {"attachment_urls": ["https://cdn.discordapp.com/attachments/a/image.png?ex=two"]}})
            self.assertEqual(saved, saved_again)
            get.assert_not_called()
