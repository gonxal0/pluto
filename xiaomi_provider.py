#!/usr/bin/env python3
"""Refresh the Xiaomi TV+ M3U from the publicly maintained upstream playlist.

Xiaomi TV+ does not publish a documented playlist export API. The source below
is a community-maintained list; this script mirrors its current entries without
rewriting their stream URLs, so upstream geo limits and playback behavior remain
in effect.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SOURCE_URL = "https://www.apsattv.com/xiaomi.m3u"
OUTPUT = Path(__file__).with_name("xiaomi.m3u")
USER_AGENT = "Mozilla/5.0 (compatible; PlutoPlaylistUpdater/1.0)"


def fetch_playlist() -> str:
    request = Request(
        SOURCE_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "audio/x-mpegurl, text/plain, */*"},
    )

    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"Upstream returned HTTP {response.status}.")
            body = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not download the Xiaomi TV+ source: {exc}") from exc

    try:
        content = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RuntimeError("The Xiaomi TV+ source is not valid UTF-8.") from exc

    return content.replace("\r\n", "\n").replace("\r", "\n")


def validate_playlist(content: str) -> int:
    lines = [line.strip() for line in content.splitlines() if line.strip()]

    if not lines or lines[0] != "#EXTM3U":
        raise RuntimeError("The Xiaomi TV+ source has no #EXTM3U header.")

    channel_count = sum(line.startswith("#EXTINF:") for line in lines)
    stream_count = sum(
        not line.startswith("#") and line.startswith(("http://", "https://"))
        for line in lines
    )

    if channel_count == 0:
        raise RuntimeError("The Xiaomi TV+ source contains no channels.")
    if stream_count < channel_count:
        raise RuntimeError(
            f"Playlist has {channel_count} channels but only {stream_count} HTTP(S) streams."
        )

    return channel_count


def write_atomically(content: str) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{OUTPUT.name}.",
        suffix=".tmp",
        dir=OUTPUT.parent,
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as target:
            target.write(content)
        os.replace(temp_name, OUTPUT)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> None:
    content = fetch_playlist()
    channel_count = validate_playlist(content)
    write_atomically(content)
    print(f"Updated {OUTPUT.name}: {channel_count} channels from {SOURCE_URL}")


if __name__ == "__main__":
    main()
