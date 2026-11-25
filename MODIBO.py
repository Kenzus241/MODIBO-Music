### IMPORTATION DES MODULES
import asyncio
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

## INITIALISATION DU BOT

intents = discord.Intents.all()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

SERV_ID = os.getenv('SERV_ID', '').strip()
MY_GUILD = discord.Object(id=int(SERV_ID)) if SERV_ID else None
YTDLP_JS_RUNTIME = os.getenv('YTDLP_JS_RUNTIME', 'node')
YTDLP_JS_RUNTIME_NAME, YTDLP_JS_RUNTIME_PATH = [*YTDLP_JS_RUNTIME.split(':', 1), None][:2]
if not YTDLP_JS_RUNTIME_PATH:
    YTDLP_JS_RUNTIME_PATH = shutil.which(YTDLP_JS_RUNTIME_NAME.lower())

YDL_OPTIONS = {
    'format': 'bestaudio[acodec=opus]/bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'ignoreerrors': True,
    'js_runtimes': {YTDLP_JS_RUNTIME_NAME.lower(): {'path': YTDLP_JS_RUNTIME_PATH}} if YTDLP_JS_RUNTIME_PATH else {},
    'remote_components': ['ejs:github'],
}

FFMPEG_OPTIONS = {
    'before_options': '-thread_queue_size 1024',
    'options': '-vn',
}

inactivity_counts = {}
queues = {}
playback_locks = {}
current_tracks = {}
loop_modes = {}
volumes = {}
skip_flags = set()
active_ytdlp_procs = {}
music_control_view_registered = False


def stop_ytdlp_process(guild_id: int):
    proc = active_ytdlp_procs.pop(guild_id, None)
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


def spawn_ytdlp_stream(target_url: str) -> subprocess.Popen:
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


def get_playback_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in playback_locks:
        playback_locks[guild_id] = asyncio.Lock()
    return playback_locks[guild_id]


def format_duration(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def get_elapsed_seconds(musique: dict) -> int:
    started_at = musique.get('started_at')
    if not started_at:
        return 0
    paused_total = musique.get('paused_total', 0.0)
    paused_at = musique.get('paused_at')
    reference = paused_at if paused_at is not None else time.monotonic()
    elapsed = int(max(0.0, reference - started_at - paused_total))
    duration = int(musique.get('duree') or 0)
    return min(elapsed, duration) if duration > 0 else elapsed


def build_progress_bar(elapsed: int, duration: int, length: int = 18) -> str:
    if duration <= 0:
        return "🔘" + "━" * (length - 1)
    ratio = min(max(elapsed / duration, 0.0), 1.0)
    pos = min(int(ratio * (length - 1)), length - 1)
    return "━" * pos + "🔘" + "━" * (length - 1 - pos)


def build_now_playing_embed(musique: dict, guild_id: int, is_paused: bool = False) -> discord.Embed:
    duree = int(musique.get('duree') or 0)
    elapsed = get_elapsed_seconds(musique)
    queue_size = len(queues.get(guild_id, []))
    volume = volumes.get(guild_id, 100)
    loop_status = "Activée 🔁" if loop_modes.get(guild_id, False) else "Désactivée"
    likes_count = len(musique.get('likes', set()))

    status_title = "⏸️ Musique en pause" if is_paused else "🎵 En cours de lecture"
    embed_color = discord.Color.orange() if is_paused else discord.Color(0x5865F2)

    embed = discord.Embed(
        title=status_title,
        color=embed_color,
    )
    track_url = musique.get('webpage_url') or musique['url']
    embed.description = (
        f"**[{musique['titre']}]({track_url})**\n"
        f"Demandé par {musique.get('added_by', 'Inconnu')}\n\n"
        f"`{format_duration(elapsed)}` {build_progress_bar(elapsed, duree)} `{format_duration(duree)}`"
    )

    embed.add_field(name="File d'attente", value=f"`{queue_size}` titre(s)", inline=True)
    embed.add_field(name="Volume", value=f"`{volume}%`", inline=True)
    embed.add_field(name="Boucle", value=f"`{loop_status}`", inline=True)

    if musique.get('thumbnail'):
        embed.set_thumbnail(url=musique['thumbnail'])

    footer_text = f"MODIBO Music • ❤️ {likes_count} j'aime" if likes_count else "MODIBO Music • Utilisez les boutons ci-dessous"
    embed.set_footer(text=footer_text)
    return embed


def build_now_playing_text(musique: dict, guild_id: int, is_paused: bool = False) -> str:
    duree = int(musique.get('duree') or 0)
    elapsed = get_elapsed_seconds(musique)
    queue_size = len(queues.get(guild_id, []))
    volume = volumes.get(guild_id, 100)
    loop_status = "Activée" if loop_modes.get(guild_id, False) else "Désactivée"
    header = "⏸️ Musique en pause" if is_paused else "🎵 En cours de lecture"

    return (
        f"### {header}\n"
        f"**{musique['titre']}**\n"
        f"• Demandé par {musique.get('added_by', 'Inconnu')}\n"
        f"• File : `{queue_size}` · Volume : `{volume}%` · Boucle : `{loop_status}`\n\n"
        f"`{format_duration(elapsed)}` {build_progress_bar(elapsed, duree)} `{format_duration(duree)}`"
    )


def build_added_to_queue_embed(musique: dict, position: int, guild_id: int) -> discord.Embed:
    duree = int(musique.get('duree') or 0)
    track_url = musique.get('webpage_url') or musique['url']
    embed = discord.Embed(
        title="✅ Ajouté à la file d'attente",
        description=f"**[{musique['titre']}]({track_url})**",
        color=discord.Color.green(),
    )
    embed.add_field(name="Position", value=f"`#{position}`", inline=True)
    embed.add_field(name="Durée", value=f"`{format_duration(duree)}`", inline=True)
    embed.add_field(name="Demandé par", value=musique.get('added_by', 'Inconnu'), inline=True)
    if musique.get('thumbnail'):
        embed.set_thumbnail(url=musique['thumbnail'])
    embed.set_footer(text=f"Total en attente : {len(queues.get(guild_id, []))} titre(s)")
    return embed


def build_queue_embed(guild_id: int) -> discord.Embed:
    current = current_tracks.get(guild_id)
    queue = queues.get(guild_id, [])

    embed = discord.Embed(
        title="📜 File d'attente",
        color=discord.Color(0x5865F2),
    )

    if current:
        track_url = current.get('webpage_url') or current['url']
        embed.add_field(
            name="🎵 En cours",
            value=f"**[{current['titre']}]({track_url})** (`{format_duration(current.get('duree', 0))}`)",
            inline=False,
        )

    if not queue:
        embed.description = "Aucun titre en attente." if current else "La file d'attente est vide."
        return embed

    lines = []
    max_items = 10
    for idx, track in enumerate(queue[:max_items], start=1):
        lines.append(f"`{idx}.` **{track['titre']}** (`{format_duration(track.get('duree', 0))}`) — {track.get('added_by', 'Inconnu')}")

    if len(queue) > max_items:
        lines.append(f"\n*... et {len(queue) - max_items} autre(s) titre(s)*")

    total_duration = sum(int(t.get('duree') or 0) for t in queue)
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{len(queue)} titre(s) en attente • Durée totale : {format_duration(total_duration)}")
    return embed


def can_send_embeds(channel, guild) -> bool:
    if not channel or not guild or not guild.me:
        return False
    return channel.permissions_for(guild.me).embed_links


async def send_now_playing(interaction: discord.Interaction, musique: dict, guild_id: int, view: discord.ui.View):
    if can_send_embeds(interaction.channel, interaction.guild):
        embed = build_now_playing_embed(musique, guild_id)
        try:
            return await interaction.followup.send(embed=embed, view=view)
        except discord.HTTPException:
            return await interaction.channel.send(embed=embed, view=view)

    message = build_now_playing_text(musique, guild_id)
    try:
        return await interaction.followup.send(content=message, view=view)
    except discord.HTTPException:
        return await interaction.channel.send(content=message, view=view)


def build_control_response_kwargs(guild_id: int, vc, channel, guild, view: discord.ui.View) -> dict:
    musique = current_tracks.get(guild_id)
    is_paused = bool(vc and vc.is_paused())
    if not musique:
        return {"content": "### 🎵 Lecteur\nAucune musique en cours.", "embed": None, "view": view}
    if can_send_embeds(channel, guild):
        return {"content": None, "embed": build_now_playing_embed(musique, guild_id, is_paused=is_paused), "view": view}
    return {"content": build_now_playing_text(musique, guild_id, is_paused=is_paused), "embed": None, "view": view}


class MusicControlView(discord.ui.View):
    def __init__(self, bot_instance, guild_id: int | None = None, is_paused: bool = False):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self._sync_button_states(guild_id, is_paused)

    def _sync_button_states(self, guild_id: int | None, is_paused: bool):
        self.play_pause_button.label = "Reprendre" if is_paused else "Pause"
        self.play_pause_button.emoji = "▶️" if is_paused else "⏸️"
        self.play_pause_button.style = discord.ButtonStyle.green if is_paused else discord.ButtonStyle.secondary

        loop_enabled = bool(guild_id and loop_modes.get(guild_id, False))
        self.loop_button.style = discord.ButtonStyle.primary if loop_enabled else discord.ButtonStyle.secondary
        self.loop_button.label = "Boucle : On" if loop_enabled else "Boucle : Off"

    async def _refresh_message(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        vc = interaction.guild.voice_client if interaction.guild else None
        is_paused = bool(vc and vc.is_paused())
        self._sync_button_states(guild_id, is_paused)

        if guild_id and current_tracks.get(guild_id):
            kwargs = build_control_response_kwargs(guild_id, vc, interaction.channel, interaction.guild, self)
            await interaction.response.edit_message(**kwargs)
        else:
            await interaction.response.edit_message(view=self)

    @discord.ui.button(
        label="Pause",
        style=discord.ButtonStyle.secondary,
        emoji="⏸️",
        custom_id="music_control:pause_resume",
        row=0,
    )
    async def play_pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client if interaction.guild else None
        guild_id = interaction.guild.id if interaction.guild else None
        if not vc:
            return await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

        musique = current_tracks.get(guild_id)
        if vc.is_playing():
            vc.pause()
            if musique and musique.get('paused_at') is None:
                musique['paused_at'] = time.monotonic()
            await self._refresh_message(interaction)
        elif vc.is_paused():
            vc.resume()
            if musique and musique.get('paused_at') is not None:
                musique['paused_total'] = musique.get('paused_total', 0.0) + (time.monotonic() - musique.pop('paused_at'))
            await self._refresh_message(interaction)
        else:
            await interaction.response.send_message("Rien n'est en cours de lecture.", ephemeral=True)

    @discord.ui.button(
        label="Skip",
        style=discord.ButtonStyle.secondary,
        emoji="⏭️",
        custom_id="music_control:skip",
        row=0,
    )
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client if interaction.guild else None
        guild_id = interaction.guild.id if interaction.guild else None
        if vc and (vc.is_playing() or vc.is_paused()):
            if guild_id:
                skip_flags.add(guild_id)
            vc.stop()
            await interaction.response.send_message("⏭️ Musique passée !", ephemeral=True)
        else:
            await interaction.response.send_message("Rien à passer.", ephemeral=True)

    @discord.ui.button(
        label="Stop",
        style=discord.ButtonStyle.danger,
        emoji="⏹️",
        custom_id="music_control:stop",
        row=0,
    )
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client if interaction.guild else None
        guild_id = interaction.guild.id if interaction.guild else None

        if guild_id:
            queues[guild_id] = []
            current_tracks.pop(guild_id, None)
            inactivity_counts[guild_id] = 0
            skip_flags.discard(guild_id)
            stop_ytdlp_process(guild_id)

        for item in self.children:
            item.disabled = True

        if vc:
            await vc.disconnect()
            stopped_embed = discord.Embed(
                title="⏹️ Lecture arrêtée",
                description="Le bot a quitté le salon vocal et la file d'attente a été vidée.",
                color=discord.Color.red(),
            )
            if can_send_embeds(interaction.channel, interaction.guild):
                await interaction.response.edit_message(content=None, embed=stopped_embed, view=self)
            else:
                await interaction.response.edit_message(content="⏹️ Bot déconnecté et file d'attente vidée.", embed=None, view=self)
        else:
            await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

    @discord.ui.button(
        label="Like",
        style=discord.ButtonStyle.secondary,
        emoji="❤️",
        custom_id="music_control:like",
        row=0,
    )
    async def like_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id if interaction.guild else None
        musique = current_tracks.get(guild_id) if guild_id else None
        if not musique:
            return await interaction.response.send_message("❤️ Titre liké !", ephemeral=True)

        likes = musique.setdefault('likes', set())
        if interaction.user.id in likes:
            likes.remove(interaction.user.id)
        else:
            likes.add(interaction.user.id)
        await self._refresh_message(interaction)

    @discord.ui.button(
        label="Boucle : Off",
        style=discord.ButtonStyle.secondary,
        emoji="🔁",
        custom_id="music_control:loop",
        row=1,
    )
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id if interaction.guild else None
        if not guild_id:
            return await interaction.response.send_message("Action impossible hors serveur.", ephemeral=True)

        loop_modes[guild_id] = not loop_modes.get(guild_id, False)
        await self._refresh_message(interaction)

    @discord.ui.button(
        label="Vol -",
        style=discord.ButtonStyle.secondary,
        emoji="🔉",
        custom_id="music_control:vol_down",
        row=1,
    )
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._adjust_volume(interaction, -10)

    @discord.ui.button(
        label="Vol +",
        style=discord.ButtonStyle.secondary,
        emoji="🔊",
        custom_id="music_control:vol_up",
        row=1,
    )
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._adjust_volume(interaction, 10)

    @discord.ui.button(
        label="File",
        style=discord.ButtonStyle.secondary,
        emoji="📜",
        custom_id="music_control:queue",
        row=1,
    )
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id if interaction.guild else None
        if not guild_id:
            return await interaction.response.send_message("Action impossible hors serveur.", ephemeral=True)
        embed = build_queue_embed(guild_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _adjust_volume(self, interaction: discord.Interaction, delta: int):
        guild_id = interaction.guild.id if interaction.guild else None
        vc = interaction.guild.voice_client if interaction.guild else None
        if not guild_id:
            return await interaction.response.send_message("Action impossible hors serveur.", ephemeral=True)

        new_vol = max(0, min(150, volumes.get(guild_id, 100) + delta))
        volumes[guild_id] = new_vol
        if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = new_vol / 100.0
        await self._refresh_message(interaction)


def register_music_control_view():
    global music_control_view_registered
    if not music_control_view_registered:
        bot.add_view(MusicControlView(bot))
        music_control_view_registered = True


@tasks.loop(minutes=1)
async def check_inactivity():
    """Vérifie l'inactivité du bot et déconnecte après 6 minutes sans lecture."""
    for guild in bot.guilds:
        vc = guild.voice_client
        if vc:
            if not vc.is_playing() and not vc.is_paused():
                inactivity_counts[guild.id] = inactivity_counts.get(guild.id, 0) + 1
                if inactivity_counts[guild.id] >= 6:
                    stop_ytdlp_process(guild.id)
                    await vc.disconnect()
                    current_tracks.pop(guild.id, None)
                    print(f"Déconnexion pour inactivité : {guild.name}")
                    inactivity_counts[guild.id] = 0
            else:
                inactivity_counts[guild.id] = 0


@bot.event
async def on_ready():
    """Initialise le bot et synchronise les commandes slash."""
    print(f"Connecté en tant que {bot.user.name}")
    register_music_control_view()

    if not check_inactivity.is_running():
        check_inactivity.start()

    try:
        syncro = await bot.tree.sync(guild=MY_GUILD)
        commandes = ', '.join(command.name for command in bot.tree.get_commands(guild=MY_GUILD))
        print(f"Commandes chargées localement : {commandes}")
        print(f"Synchro INSTANTANÉE réussie : {len(syncro)} commandes sur ton serveur.")
    except discord.HTTPException as error:
        print(f"Erreur de synchro: {error}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandNotFound):
        message = (
            "Commande pas encore reconnue par le bot après la synchro. "
            "Redémarre le bot, recharge Discord avec Ctrl+R, puis retape la commande."
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return

    raise error


@bot.tree.command(name="play_music", description="Joue la musique demandée", guild=MY_GUILD)
@app_commands.describe(recherche="Le nom de la musique ou le lien YouTube")
async def play_music(interaction: discord.Interaction, recherche: str):
    """Recherche et lance ou ajoute une musique à la file d'attente."""
    if interaction.user.voice is None:
        return await interaction.response.send_message("Tu dois être dans un salon vocal pour ça !", ephemeral=True)

    await interaction.response.defer()

    recherche_amelioree = f"ytsearch5:{recherche} audio"

    try:
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, recherche_amelioree, download=False)

        video_choisie = None
        if info and 'entries' in info:
            for entry in info['entries']:
                if entry and entry.get('duration', 0) < 600:
                    video_choisie = entry
                    break
        elif info and info.get('url'):
            video_choisie = info

        if not video_choisie:
            return await interaction.followup.send("❌ Musique introuvable ou trop longue (max 10 min).")

        url = video_choisie['url']
        titre = video_choisie['title']
        duree = video_choisie.get('duration', 0)
        thumbnail = video_choisie.get('thumbnail')
        webpage_url = video_choisie.get('webpage_url') or video_choisie.get('original_url')
        http_headers = video_choisie.get('http_headers') or {}
    except yt_dlp.utils.DownloadError as e:
        return await interaction.followup.send(f"❌ Erreur lors de la recherche : {e}")

    guild_id = interaction.guild.id
    async with get_playback_lock(guild_id):
        register_music_control_view()
        channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if vc is None:
            vc = await channel.connect()

        if guild_id not in queues:
            queues[guild_id] = []

        track = {
            'url': url,
            'titre': titre,
            'duree': duree,
            'thumbnail': thumbnail,
            'webpage_url': webpage_url,
            'http_headers': http_headers,
            'added_by': interaction.user.mention,
            'likes': set(),
        }
        queues[guild_id].append(track)

        if not vc.is_playing() and not vc.is_paused():
            await play_next_unlocked(interaction)
        else:
            position = len(queues[guild_id])
            if can_send_embeds(interaction.channel, interaction.guild):
                embed = build_added_to_queue_embed(track, position, guild_id)
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(f"✅ Ajouté à la file d'attente (`#{position}`) : **{titre}**")


async def play_next(interaction: discord.Interaction):
    async with get_playback_lock(interaction.guild.id):
        await play_next_unlocked(interaction)


async def play_next_unlocked(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    if not vc or vc.is_playing() or vc.is_paused():
        return

    was_skipped = guild_id in skip_flags
    skip_flags.discard(guild_id)

    previous_track = current_tracks.get(guild_id)
    if loop_modes.get(guild_id, False) and previous_track and not was_skipped:
        requeued = dict(previous_track)
        requeued.pop('started_at', None)
        requeued.pop('paused_at', None)
        requeued['paused_total'] = 0.0
        queues.setdefault(guild_id, []).insert(0, requeued)

    if guild_id not in queues or len(queues[guild_id]) == 0:
        current_tracks.pop(guild_id, None)
        return

    musique = queues[guild_id].pop(0)
    volume_level = volumes.get(guild_id, 100) / 100.0
    stop_ytdlp_process(guild_id)
    stream_target = musique.get('webpage_url') or musique['url']
    ytdlp_proc = spawn_ytdlp_stream(stream_target)
    active_ytdlp_procs[guild_id] = ytdlp_proc
    raw_source = discord.FFmpegPCMAudio(
        ytdlp_proc.stdout,
        pipe=True,
        before_options=FFMPEG_OPTIONS['before_options'],
        options=FFMPEG_OPTIONS['options'],
    )
    buffered_source = BufferedAudioSource(raw_source)
    has_audio = await asyncio.to_thread(buffered_source.wait_ready)
    if not has_audio:
        buffered_source.cleanup()
        stop_ytdlp_process(guild_id)
        return

    if vc.is_playing() or vc.is_paused():
        buffered_source.cleanup()
        stop_ytdlp_process(guild_id)
        queues[guild_id].insert(0, musique)
        return

    musique['started_at'] = time.monotonic()
    musique['paused_total'] = 0.0
    musique.pop('paused_at', None)
    current_tracks[guild_id] = musique

    source = discord.PCMVolumeTransformer(buffered_source, volume=volume_level)

    def after_playback(_err):
        stop_ytdlp_process(guild_id)
        bot.loop.create_task(play_next(interaction))

    vc.play(source, after=after_playback)

    view = MusicControlView(bot, guild_id=guild_id, is_paused=False)
    await send_now_playing(interaction, musique, guild_id, view)


@bot.tree.command(name="music_controls", description="Affiche le menu de contrôle de la musique", guild=MY_GUILD)
async def music_controls(interaction: discord.Interaction):
    register_music_control_view()
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    is_paused = bool(vc and vc.is_paused())
    view = MusicControlView(bot, guild_id=guild_id, is_paused=is_paused)

    musique = current_tracks.get(guild_id)
    if not musique:
        return await interaction.response.send_message(
            "### 🎵 Lecteur\nAucune musique en cours.",
            ephemeral=True,
        )

    kwargs = build_control_response_kwargs(guild_id, vc, interaction.channel, interaction.guild, view)
    await interaction.response.send_message(**{k: v for k, v in kwargs.items() if v is not None})


@bot.tree.command(name="queue", description="Affiche la file d'attente", guild=MY_GUILD)
async def show_queue(interaction: discord.Interaction):
    embed = build_queue_embed(interaction.guild.id)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="loop", description="Active ou désactive la lecture en boucle", guild=MY_GUILD)
async def toggle_loop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    loop_modes[guild_id] = not loop_modes.get(guild_id, False)
    etat = "activée 🔁" if loop_modes[guild_id] else "désactivée"
    await interaction.response.send_message(f"Boucle {etat}.")


@bot.tree.command(name="volume", description="Règle le volume du bot (0 à 150%)", guild=MY_GUILD)
@app_commands.describe(niveau="Volume en pourcentage (0 à 150)")
async def set_volume(interaction: discord.Interaction, niveau: app_commands.Range[int, 0, 150]):
    guild_id = interaction.guild.id
    volumes[guild_id] = niveau
    vc = interaction.guild.voice_client
    if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = niveau / 100.0
    await interaction.response.send_message(f"🔊 Volume réglé sur **{niveau}%**.")


@bot.tree.command(name="pause", description="Met la musique en pause", guild=MY_GUILD)
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        musique = current_tracks.get(interaction.guild.id)
        if musique and musique.get('paused_at') is None:
            musique['paused_at'] = time.monotonic()
        await interaction.response.send_message("⏸️ Musique mise en pause.")
    else:
        await interaction.response.send_message("Rien n'est en cours de lecture.", ephemeral=True)


@bot.tree.command(name="skip", description="Passe la musique actuelle", guild=MY_GUILD)
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        skip_flags.add(interaction.guild.id)
        vc.stop()
        await interaction.response.send_message("⏭️ Musique passée !")
    else:
        await interaction.response.send_message("Rien n'est en cours de lecture.", ephemeral=True)


@bot.tree.command(name="resume", description="Reprend la musique", guild=MY_GUILD)
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        musique = current_tracks.get(interaction.guild.id)
        if musique and musique.get('paused_at') is not None:
            musique['paused_total'] = musique.get('paused_total', 0.0) + (time.monotonic() - musique.pop('paused_at'))
        await interaction.response.send_message("▶️ La musique reprend !")
    else:
        await interaction.response.send_message("La musique n'est pas en pause.", ephemeral=True)


@bot.tree.command(name="stop", description="Arrête la musique et déconnecte le bot", guild=MY_GUILD)
async def stop_command(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    queues[guild_id] = []
    current_tracks.pop(guild_id, None)
    inactivity_counts[guild_id] = 0
    skip_flags.discard(guild_id)
    stop_ytdlp_process(guild_id)

    if vc:
        await vc.disconnect()
        await interaction.response.send_message("⏹️ Lecture arrêtée et bot déconnecté.")
    else:
        await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)


## LANCEMENT DU BOT


def get_discord_token() -> str:
    token = os.getenv('DISCORD_TOKEN', '').strip()

    if not token:
        print('Aucun token Discord trouvé. Ajoute DISCORD_TOKEN dans le fichier .env avec le token de ton bot Discord.')
        sys.exit(1)

    if token.isdigit():
        print("Le token Discord fourni semble invalide. Utilise le token du bot Discord, pas l'ID de l'application.")
        sys.exit(1)

    return token


if __name__ == '__main__':
    try:
        bot.run(get_discord_token())
    except discord.LoginFailure as exc:
        print(f'Échec de connexion Discord : {exc}')
        sys.exit(1)
