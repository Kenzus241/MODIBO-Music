"""Interface utilisateur Discord : Components V2, embeds de file et boutons de contrôle."""

import time

import discord

from config import HUD_COLOR
from state import get_guild_state, pause_track, reset_guild_playback, resume_track


def format_duration(seconds) -> str:
    """Formate une durée en secondes vers MM:SS ou H:MM:SS."""
    seconds = max(0, int(seconds or 0))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def get_elapsed_seconds(musique: dict) -> int:
    """Calcule le temps de lecture écoulé en tenant compte des pauses."""
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
    """Construit la barre de progression textuelle."""
    if duration <= 0:
        return "🔘" + "━" * (length - 1)
    ratio = min(max(elapsed / duration, 0.0), 1.0)
    pos = min(int(ratio * (length - 1)), length - 1)
    return "━" * pos + "🔘" + "━" * (length - 1 - pos)


def can_send_embeds(channel, guild) -> bool:
    """Vérifie si le bot a la permission d'envoyer des embeds dans le salon."""
    if not channel or not guild or not guild.me:
        return False
    return channel.permissions_for(guild.me).embed_links


def build_added_to_queue_embed(musique: dict, position: int, guild_id: int) -> discord.Embed:
    """Construit l'embed de confirmation lors de l'ajout d'un titre en file d'attente."""
    state = get_guild_state(guild_id)
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
    embed.set_footer(text=f"Total en attente : {len(state.queue)} titre(s)")
    return embed


def build_queue_embed(guild_id: int, include_current: bool = False) -> discord.Embed:
    """Construit l'embed de la file d'attente (utilisé quand aucune musique ne joue ou en éphémère)."""
    state = get_guild_state(guild_id)
    current = state.current_track if include_current else None
    queue = state.queue

    embed = discord.Embed(color=HUD_COLOR)

    if current:
        track_url = current.get('webpage_url') or current['url']
        embed.add_field(
            name="🎵 En cours",
            value=f"**[{current['titre']}]({track_url})** - `{format_duration(current.get('duree', 0))}`",
            inline=False,
        )

    if not queue:
        embed.description = "Aucune musique dans la file d'attente."
        return embed

    embed.title = "📜 File d'attente"
    lines = []
    max_items = 10
    for idx, track in enumerate(queue[:max_items], start=1):
        track_url = track.get('webpage_url') or track['url']
        lines.append(
            f"`{idx}.` **[{track['titre']}]({track_url})** - `{format_duration(track.get('duree', 0))}` • {track.get('added_by', 'Inconnu')}"
        )

    if len(queue) > max_items:
        lines.append(f"\n*... et {len(queue) - max_items} autre(s) titre(s)*")

    total_duration = sum(int(t.get('duree') or 0) for t in queue)
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{len(queue)} titre(s) en attente • Durée totale : {format_duration(total_duration)}")
    return embed


def build_queue_text(guild_id: int) -> str:
    """Construit le texte de la file d'attente pour le conteneur Components V2."""
    queue = get_guild_state(guild_id).queue
    if not queue:
        return "Aucune musique dans la file d'attente."

    lines = ["**📜 File d'attente**"]
    max_items = 10
    for idx, track in enumerate(queue[:max_items], start=1):
        track_url = track.get('webpage_url') or track['url']
        lines.append(
            f"`{idx}.` **[{track['titre']}]({track_url})** - `{format_duration(track.get('duree', 0))}` • {track.get('added_by', 'Inconnu')}"
        )

    if len(queue) > max_items:
        lines.append(f"*... et {len(queue) - max_items} autre(s) titre(s)*")

    total_duration = sum(int(t.get('duree') or 0) for t in queue)
    lines.append(f"-# {len(queue)} titre(s) en attente • Durée totale : {format_duration(total_duration)}")
    return "\n".join(lines)


def build_now_playing_container(musique: dict, guild_id: int, is_paused: bool = False) -> discord.ui.Container:
    """Construit le conteneur principal Now Playing avec miniature YouTube et barre de progression."""
    state = get_guild_state(guild_id)
    duree = int(musique.get('duree') or 0)
    elapsed = get_elapsed_seconds(musique)
    loop_status = "On 🔁" if state.loop_enabled else "Off"
    likes_count = len(musique.get('likes', set()))

    status_title = "⏸️ En pause" if is_paused else "Now playing"
    accent = discord.Color.orange() if is_paused else HUD_COLOR

    track_url = musique.get('webpage_url') or musique['url']
    progress_line = f"`{format_duration(elapsed)}` {build_progress_bar(elapsed, duree)} `{format_duration(duree)}`"

    footer_parts = [f"Volume : {state.volume}%", f"Boucle : {loop_status}"]
    if likes_count:
        footer_parts.append(f"❤️ {likes_count}")

    text = (
        f"**{status_title}**\n"
        f"**[{musique['titre']}]({track_url})** - `{format_duration(duree)}`\n"
        f"> Demandé par {musique.get('added_by', 'Inconnu')}\n"
        f"{progress_line}\n"
        f"-# {' • '.join(footer_parts)}"
    )

    if musique.get('thumbnail'):
        body = discord.ui.Section(
            discord.ui.TextDisplay(text),
            accessory=discord.ui.Thumbnail(media=musique['thumbnail']),
        )
    else:
        body = discord.ui.TextDisplay(text)

    return discord.ui.Container(body, accent_colour=accent)


def build_queue_container(guild_id: int) -> discord.ui.Container:
    """Construit le second conteneur affichant la file d'attente."""
    return discord.ui.Container(
        discord.ui.TextDisplay(build_queue_text(guild_id)),
        accent_colour=HUD_COLOR,
    )


class MusicControlView(discord.ui.LayoutView):
    """HUD persistant Components V2 regroupant lecture en cours, file et boutons de contrôle."""

    def __init__(self, bot_instance, guild_id: int | None = None, is_paused: bool = False, stopped: bool = False):
        super().__init__(timeout=None)
        self.bot = bot_instance

        self.play_pause_button = self._make_button("Pause", discord.ButtonStyle.secondary, "⏸️", "music_control:pause_resume", self.play_pause_callback)
        self.skip_button = self._make_button("Skip", discord.ButtonStyle.secondary, "⏭️", "music_control:skip", self.skip_callback)
        self.stop_button = self._make_button("Stop", discord.ButtonStyle.danger, "⏹️", "music_control:stop", self.stop_callback)
        self.like_button = self._make_button("Like", discord.ButtonStyle.secondary, "❤️", "music_control:like", self.like_callback)
        self.loop_button = self._make_button("Boucle : Off", discord.ButtonStyle.secondary, "🔁", "music_control:loop", self.loop_callback)
        self.volume_down_button = self._make_button("Vol -", discord.ButtonStyle.secondary, "🔉", "music_control:vol_down", self.volume_down_callback)
        self.volume_up_button = self._make_button("Vol +", discord.ButtonStyle.secondary, "🔊", "music_control:vol_up", self.volume_up_callback)
        self.queue_button = self._make_button("File", discord.ButtonStyle.secondary, "📜", "music_control:queue", self.queue_callback)

        self._rebuild_layout(guild_id, is_paused, stopped=stopped)

    @staticmethod
    def _make_button(label: str, style: discord.ButtonStyle, emoji: str, custom_id: str, callback) -> discord.ui.Button:
        btn = discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=custom_id)
        btn.callback = callback
        return btn

    def _sync_button_states(self, guild_id: int | None, is_paused: bool, stopped: bool = False) -> None:
        self.play_pause_button.label = "Reprendre" if is_paused else "Pause"
        self.play_pause_button.emoji = "▶️" if is_paused else "⏸️"
        self.play_pause_button.style = discord.ButtonStyle.green if is_paused else discord.ButtonStyle.secondary

        loop_enabled = bool(guild_id and get_guild_state(guild_id).loop_enabled)
        self.loop_button.style = discord.ButtonStyle.primary if loop_enabled else discord.ButtonStyle.secondary
        self.loop_button.label = "Boucle : On" if loop_enabled else "Boucle : Off"

        for btn in (
            self.play_pause_button,
            self.skip_button,
            self.stop_button,
            self.like_button,
            self.loop_button,
            self.volume_down_button,
            self.volume_up_button,
            self.queue_button,
        ):
            btn.disabled = stopped

    def _rebuild_layout(self, guild_id: int | None, is_paused: bool, stopped: bool = False) -> None:
        self.clear_items()
        self._sync_button_states(guild_id, is_paused, stopped=stopped)

        if stopped:
            self.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(
                        "**⏹️ Lecture arrêtée**\nLe bot a quitté le salon vocal et la file d'attente a été vidée."
                    ),
                    accent_colour=discord.Color.red(),
                )
            )
        else:
            musique = get_guild_state(guild_id).current_track if guild_id else None
            if musique:
                self.add_item(build_now_playing_container(musique, guild_id, is_paused=is_paused))
                self.add_item(build_queue_container(guild_id))
            else:
                self.add_item(
                    discord.ui.Container(
                        discord.ui.TextDisplay("**🎵 Lecteur**\nAucune musique en cours."),
                        accent_colour=HUD_COLOR,
                    )
                )

        self.add_item(
            discord.ui.ActionRow(
                self.play_pause_button,
                self.skip_button,
                self.stop_button,
                self.like_button,
            )
        )
        self.add_item(
            discord.ui.ActionRow(
                self.loop_button,
                self.volume_down_button,
                self.volume_up_button,
                self.queue_button,
            )
        )

    async def _refresh_message(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild.id if interaction.guild else None
        vc = interaction.guild.voice_client if interaction.guild else None
        is_paused = bool(vc and vc.is_paused())
        self._rebuild_layout(guild_id, is_paused)
        await interaction.response.edit_message(view=self)

    async def play_pause_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client if interaction.guild else None
        guild_id = interaction.guild.id if interaction.guild else None
        if not vc or not guild_id:
            return await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

        state = get_guild_state(guild_id)
        if pause_track(vc, state) or resume_track(vc, state):
            await self._refresh_message(interaction)
        else:
            await interaction.response.send_message("Rien n'est en cours de lecture.", ephemeral=True)

    async def skip_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client if interaction.guild else None
        guild_id = interaction.guild.id if interaction.guild else None
        if vc and (vc.is_playing() or vc.is_paused()):
            if guild_id:
                get_guild_state(guild_id).skip_requested = True
            vc.stop()
            await interaction.response.send_message("⏭️ Musique passée !", ephemeral=True)
        else:
            await interaction.response.send_message("Rien à passer.", ephemeral=True)

    async def stop_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client if interaction.guild else None
        guild_id = interaction.guild.id if interaction.guild else None

        if guild_id:
            reset_guild_playback(get_guild_state(guild_id))

        if vc:
            await vc.disconnect()
            self._rebuild_layout(guild_id, False, stopped=True)
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

    async def like_callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        musique = get_guild_state(guild_id).current_track if guild_id else None
        if not musique:
            return await interaction.response.send_message("❤️ Titre liké !", ephemeral=True)

        likes = musique.setdefault('likes', set())
        if interaction.user.id in likes:
            likes.remove(interaction.user.id)
        else:
            likes.add(interaction.user.id)
        await self._refresh_message(interaction)

    async def loop_callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        if not guild_id:
            return await interaction.response.send_message("Action impossible hors serveur.", ephemeral=True)

        state = get_guild_state(guild_id)
        state.loop_enabled = not state.loop_enabled
        await self._refresh_message(interaction)

    async def volume_down_callback(self, interaction: discord.Interaction):
        await self._adjust_volume(interaction, -10)

    async def volume_up_callback(self, interaction: discord.Interaction):
        await self._adjust_volume(interaction, 10)

    async def queue_callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        if not guild_id:
            return await interaction.response.send_message("Action impossible hors serveur.", ephemeral=True)
        embed = build_queue_embed(guild_id, include_current=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _adjust_volume(self, interaction: discord.Interaction, delta: int):
        guild_id = interaction.guild.id if interaction.guild else None
        vc = interaction.guild.voice_client if interaction.guild else None
        if not guild_id:
            return await interaction.response.send_message("Action impossible hors serveur.", ephemeral=True)

        state = get_guild_state(guild_id)
        state.volume = max(0, min(150, state.volume + delta))
        if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume / 100.0
        await self._refresh_message(interaction)


async def send_now_playing(interaction: discord.Interaction, guild_id: int, view: discord.ui.LayoutView):
    """Envoie le HUD de lecture et mémorise le message pour l'actualisation temps réel."""
    try:
        msg = await interaction.followup.send(view=view)
    except discord.HTTPException:
        msg = await interaction.channel.send(view=view)
    get_guild_state(guild_id).now_playing_message = (msg.channel.id, msg.id)
    return msg
