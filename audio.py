"""Extraction YouTube, streaming yt-dlp et tampon audio PCM."""

import asyncio
import queue
import subprocess
import sys
import threading

import discord
import yt_dlp

from config import (
    MAX_TRACK_DURATION_SECONDS,
    YDL_OPTIONS,
    YTDLP_JS_RUNTIME_NAME,
    YTDLP_JS_RUNTIME_PATH,
)


def spawn_ytdlp_stream(target_url: str) -> subprocess.Popen:
    """Lance yt-dlp en flux vers stdout pour éviter les coupures de stream."""
    cmd = [
        sys.executable,
        '-m',
        'yt_dlp',
        '--quiet',
        '--no-warnings',
        '--no-playlist',
        '-f',
        'bestaudio[acodec=opus]/bestaudio/best',
    ]
    if YTDLP_JS_RUNTIME_PATH:
        cmd.extend(['--js-runtimes', f"{YTDLP_JS_RUNTIME_NAME.lower()}:{YTDLP_JS_RUNTIME_PATH}"])
    cmd.extend(['--remote-components', 'ejs:github', '-o', '-', target_url])
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


async def search_track(query: str, added_by: str) -> dict | None:
    """Recherche une musique via yt-dlp et retourne ses métadonnées prêtes pour la file."""
    search_query = f"ytsearch5:{query} audio"
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = await asyncio.to_thread(ydl.extract_info, search_query, download=False)

    chosen = None
    if info and 'entries' in info:
        for entry in info['entries']:
            if entry and entry.get('duration', 0) < MAX_TRACK_DURATION_SECONDS:
                chosen = entry
                break
    elif info and info.get('url'):
        chosen = info

    if not chosen:
        return None

    return {
        'url': chosen['url'],
        'titre': chosen['title'],
        'duree': chosen.get('duration', 0),
        'thumbnail': chosen.get('thumbnail'),
        'webpage_url': chosen.get('webpage_url') or chosen.get('original_url'),
        'http_headers': chosen.get('http_headers') or {},
        'added_by': added_by,
        'likes': set(),
    }


class BufferedAudioSource(discord.AudioSource):
    """Tampon PCM en arrière-plan (0.5s à 2s) pour éviter les micro-coupures et l'accélération initiale."""

    def __init__(self, inner: discord.AudioSource, max_frames: int = 100, prebuffer_frames: int = 25):
        self._inner = inner
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max_frames)
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._prebuffer_frames = prebuffer_frames
        self._has_audio = False
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self) -> None:
        count = 0
        try:
            while not self._stop_event.is_set():
                frame = self._inner.read()
                if not frame:
                    break
                self._has_audio = True
                while not self._stop_event.is_set():
                    try:
                        self._queue.put(frame, timeout=0.1)
                        count += 1
                        if count >= self._prebuffer_frames:
                            self._ready_event.set()
                        break
                    except queue.Full:
                        continue
        finally:
            self._ready_event.set()
            while not self._stop_event.is_set():
                try:
                    self._queue.put(b'', timeout=0.1)
                    break
                except queue.Full:
                    continue

    def wait_ready(self) -> bool:
        """Attend que le pré-tampon soit rempli ou que le flux se termine."""
        self._ready_event.wait()
        return self._has_audio

    def read(self) -> bytes:
        while not self._stop_event.is_set():
            try:
                return self._queue.get(timeout=0.5)
            except queue.Empty:
                if not self._thread.is_alive():
                    return b''
        return b''

    def is_opus(self) -> bool:
        return self._inner.is_opus()

    def cleanup(self) -> None:
        self._stop_event.set()
        self._inner.cleanup()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
