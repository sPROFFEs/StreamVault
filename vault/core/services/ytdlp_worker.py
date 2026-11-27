"""Isolated yt-dlp worker. Private request inputs enter through stdin."""
import json
import sys
import time

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class Quiet:
    def debug(self, message):
        pass

    warning = error = debug


class RateLimitedYoutubeDL(YoutubeDL):
    def dl(self, name, info, subtitle=False, test=False):
        original_rate = self.params["ratelimit"]
        formats = info.get("requested_formats") or [info]
        segmented = any(fmt.get("protocol") in {"m3u8_native", "http_dash_segments", "ism"}
                        for fmt in formats)
        if segmented:
            fragments = self.params.get("concurrent_fragment_downloads", 1)
            self.params["ratelimit"] = max(1, original_rate // fragments)
        try:
            return super().dl(name, info, subtitle, test)
        finally:
            self.params["ratelimit"] = original_rate


def main():
    request = json.load(sys.stdin)
    options = request["options"]
    last_emit = 0

    def progress(data):
        nonlocal last_emit
        now = time.monotonic()
        if data["status"] == "downloading" and now - last_emit < .5:
            return
        last_emit = now
        keys = ("status", "downloaded_bytes", "total_bytes", "total_bytes_estimate", "speed", "eta")
        print(json.dumps({key: data.get(key) for key in keys}), flush=True)

    def processing(data):
        if data["status"] == "started":
            print('{"status":"processing"}', flush=True)

    options.update(progress_hooks=[progress], postprocessor_hooks=[processing], logger=Quiet())
    try:
        with RateLimitedYoutubeDL(options) as ydl:
            return 1 if ydl.download([request["url"]]) else 0
    except DownloadError:
        # DownloadError messages can contain URLs or authentication headers.
        return 1


if __name__ == "__main__":
    sys.exit(main())
