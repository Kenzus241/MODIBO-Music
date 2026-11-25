"""Gestion centralisée de l'état de lecture par serveur Discord."""

import asyncio
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class GuildMusicState:
    """État audio, file d'attente et HUD associé à un serveur."""

    queue: list[dict] = field(default_factory=list)
    current_track: dict | None = None
    now_playing_message: tuple[int, int] | None = None
    loop_enabled: bool = False
    volume: int = 100
    skip_requested: bool = False
    inactivity_count: int = 0
    ytdlp_proc: subprocess.Popen | None = None
    playback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_guild_states: dict[int, GuildMusicState] = {}


def get_guild_state(guild_id: int) -> GuildMusicState:
    """Retourne l'état du serveur (le crée s'il n'existe pas encore)."""
    if guild_id not in _guild_states:
        _guild_states[guild_id] = GuildMusicState()
    return _guild_states[guild_id]


def all_guild_states() -> dict[int, GuildMusicState]:
    """Retourne le dictionnaire des états actifs par serveur."""
    return _guild_states


def stop_ytdlp_process(state: GuildMusicState) -> None:
    """Termine proprement le sous-processus yt-dlp actif s'il existe."""
    proc = state.ytdlp_proc
    state.ytdlp_proc = None
    if not proc:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    for pipe in (proc.stdout, proc.stderr):
        if pipe:
            pipe.close()


def pause_track(vc, state: GuildMusicState) -> bool:
    """Met la lecture en pause et mémorise l'horodatage de pause."""
    if not vc or not vc.is_playing():
        return False
    vc.pause()
    if state.current_track and state.current_track.get('paused_at') is None:
        state.current_track['paused_at'] = time.monotonic()
    return True


def resume_track(vc, state: GuildMusicState) -> bool:
    """Reprend la lecture et cumule la durée de pause."""
    if not vc or not vc.is_paused():
        return False
    vc.resume()
    if state.current_track and state.current_track.get('paused_at') is not None:
        paused_duration = time.monotonic() - state.current_track.pop('paused_at')
        state.current_track['paused_total'] = state.current_track.get('paused_total', 0.0) + paused_duration
    return True


def reset_guild_playback(state: GuildMusicState) -> None:
    """Réinitialise la file, la piste en cours et le processus audio du serveur."""
    state.queue.clear()
    state.current_track = None
    state.now_playing_message = None
    state.inactivity_count = 0
    state.skip_requested = False
    stop_ytdlp_process(state)
