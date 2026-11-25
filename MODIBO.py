"""Point d'entrée principal (cerveau) du bot Discord MODIBO Music."""

import asyncio
import sys
import time

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands, tasks

from audio import BufferedAudioSource, search_track, spawn_ytdlp_stream
from config import FFMPEG_OPTIONS, INACTIVITY_LIMIT_MINUTES, MY_GUILD, get_discord_token
from hud import (
    MusicControlView,
    build_added_to_queue_embed,
    build_queue_embed,
    can_send_embeds,
    send_now_playing,
)
from state import (
    all_guild_states,
    get_guild_state,
    pause_track,
    reset_guild_playback,
    resume_track,
    stop_ytdlp_process,
)

intents = discord.Intents.all()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)
music_control_view_registered = False


def register_music_control_view() -> None:
    """Enregistre la vue persistante une seule fois pour gérer les clics après redémarrage."""
    global music_control_view_registered
    if not music_control_view_registered:
        bot.add_view(MusicControlView(bot))
        music_control_view_registered = True


@tasks.loop(seconds=5)
async def update_now_playing_hud() -> None:
    """Actualise périodiquement la barre de progression du HUD en cours de lecture."""
    for guild_id, state in list(all_guild_states().items()):
        if not state.now_playing_message:
            continue

        guild = bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None
        if not vc or not state.current_track:
            state.now_playing_message = None
            continue
        if not vc.is_playing():
            continue

        channel_id, message_id = state.now_playing_message
        channel = bot.get_channel(channel_id)
        if not channel:
            continue

        view = MusicControlView(bot, guild_id=guild_id, is_paused=False)
        try:
            await channel.get_partial_message(message_id).edit(view=view)
        except discord.NotFound:
            state.now_playing_message = None
        except discord.HTTPException:
            pass


@tasks.loop(minutes=1)
async def check_inactivity() -> None:
    """Vérifie l'inactivité du bot et déconnecte après le délai configuré sans lecture."""
    for guild in bot.guilds:
        vc = guild.voice_client
        if not vc:
            continue

        state = get_guild_state(guild.id)
        if not vc.is_playing() and not vc.is_paused():
            state.inactivity_count += 1
            if state.inactivity_count >= INACTIVITY_LIMIT_MINUTES:
                reset_guild_playback(state)
                await vc.disconnect()
                print(f"Déconnexion pour inactivité : {guild.name}")
        else:
            state.inactivity_count = 0


@bot.event
async def on_ready() -> None:
    """Initialise le bot, démarre les tâches de fond et synchronise les commandes slash."""
    print(f"Connecté en tant que {bot.user.name}")
    register_music_control_view()

    if not check_inactivity.is_running():
        check_inactivity.start()
    if not update_now_playing_hud.is_running():
        update_now_playing_hud.start()

    try:
        syncro = await bot.tree.sync(guild=MY_GUILD)
        commandes = ', '.join(command.name for command in bot.tree.get_commands(guild=MY_GUILD))
        print(f"Commandes chargées localement : {commandes}")
        print(f"Synchro INSTANTANÉE réussie : {len(syncro)} commandes sur ton serveur.")
    except discord.HTTPException as error:
        print(f"Erreur de synchro: {error}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
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


async def play_next(interaction: discord.Interaction) -> None:
    """Enchaîne la piste suivante sous verrou serveur."""
    state = get_guild_state(interaction.guild.id)
    async with state.playback_lock:
        await play_next_unlocked(interaction)


async def play_next_unlocked(interaction: discord.Interaction) -> None:
    """Lance la lecture de la prochaine musique dans la file (appelé sous verrou)."""
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    if not vc or vc.is_playing() or vc.is_paused():
        return

    state = get_guild_state(guild_id)
    was_skipped = state.skip_requested
    state.skip_requested = False

    if state.loop_enabled and state.current_track and not was_skipped:
        requeued = dict(state.current_track)
        requeued.pop('started_at', None)
        requeued.pop('paused_at', None)
        requeued['paused_total'] = 0.0
        state.queue.insert(0, requeued)

    if not state.queue:
        state.current_track = None
        return

    musique = state.queue.pop(0)
    stop_ytdlp_process(state)

    stream_target = musique.get('webpage_url') or musique['url']
    state.ytdlp_proc = spawn_ytdlp_stream(stream_target)
    raw_source = discord.FFmpegPCMAudio(
        state.ytdlp_proc.stdout,
        pipe=True,
        before_options=FFMPEG_OPTIONS['before_options'],
        options=FFMPEG_OPTIONS['options'],
    )
    buffered_source = BufferedAudioSource(raw_source)
    has_audio = await asyncio.to_thread(buffered_source.wait_ready)
    if not has_audio:
        buffered_source.cleanup()
        stop_ytdlp_process(state)
        return

    if vc.is_playing() or vc.is_paused():
        buffered_source.cleanup()
        stop_ytdlp_process(state)
        state.queue.insert(0, musique)
        return

    musique['started_at'] = time.monotonic()
    musique['paused_total'] = 0.0
    musique.pop('paused_at', None)
    state.current_track = musique

    source = discord.PCMVolumeTransformer(buffered_source, volume=state.volume / 100.0)

    def after_playback(_err):
        stop_ytdlp_process(state)
        bot.loop.create_task(play_next(interaction))

    vc.play(source, after=after_playback)

    view = MusicControlView(bot, guild_id=guild_id, is_paused=False)
    await send_now_playing(interaction, guild_id, view)


@bot.tree.command(name="play_music", description="Joue la musique demandée", guild=MY_GUILD)
@app_commands.describe(recherche="Le nom de la musique ou le lien YouTube")
async def play_music(interaction: discord.Interaction, recherche: str):
    """Recherche et lance ou ajoute une musique à la file d'attente."""
    if interaction.user.voice is None:
        return await interaction.response.send_message("Tu dois être dans un salon vocal pour ça !", ephemeral=True)

    await interaction.response.defer()

    try:
        track = await search_track(recherche, added_by=interaction.user.mention)
    except yt_dlp.utils.DownloadError as e:
        return await interaction.followup.send(f"❌ Erreur lors de la recherche : {e}")

    if not track:
        return await interaction.followup.send("❌ Musique introuvable ou trop longue (max 10 min).")

    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    async with state.playback_lock:
        register_music_control_view()
        vc = interaction.guild.voice_client or await interaction.user.voice.channel.connect()
        state.queue.append(track)

        if not vc.is_playing() and not vc.is_paused():
            await play_next_unlocked(interaction)
        else:
            position = len(state.queue)
            if can_send_embeds(interaction.channel, interaction.guild):
                await interaction.followup.send(embed=build_added_to_queue_embed(track, position, guild_id))
            else:
                await interaction.followup.send(f"✅ Ajouté à la file d'attente (`#{position}`) : **{track['titre']}**")


@bot.tree.command(name="music_controls", description="Affiche le menu de contrôle de la musique", guild=MY_GUILD)
async def music_controls(interaction: discord.Interaction):
    """Affiche le HUD interactif pour la piste en cours."""
    register_music_control_view()
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    if not state.current_track:
        return await interaction.response.send_message("### 🎵 Lecteur\nAucune musique en cours.", ephemeral=True)

    vc = interaction.guild.voice_client
    view = MusicControlView(bot, guild_id=guild_id, is_paused=bool(vc and vc.is_paused()))
    await interaction.response.send_message(view=view)
    msg = await interaction.original_response()
    state.now_playing_message = (msg.channel.id, msg.id)


@bot.tree.command(name="queue", description="Affiche la file d'attente", guild=MY_GUILD)
async def show_queue(interaction: discord.Interaction):
    """Affiche le HUD complet si une piste joue, sinon l'état de la file."""
    register_music_control_view()
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    if state.current_track:
        vc = interaction.guild.voice_client
        view = MusicControlView(bot, guild_id=guild_id, is_paused=bool(vc and vc.is_paused()))
        await interaction.response.send_message(view=view)
        msg = await interaction.original_response()
        state.now_playing_message = (msg.channel.id, msg.id)
    else:
        await interaction.response.send_message(embed=build_queue_embed(guild_id))


@bot.tree.command(name="loop", description="Active ou désactive la lecture en boucle", guild=MY_GUILD)
async def toggle_loop(interaction: discord.Interaction):
    """Bascule le mode boucle pour le serveur."""
    state = get_guild_state(interaction.guild.id)
    state.loop_enabled = not state.loop_enabled
    etat = "activée 🔁" if state.loop_enabled else "désactivée"
    await interaction.response.send_message(f"Boucle {etat}.")


@bot.tree.command(name="volume", description="Règle le volume du bot (0 à 150%)", guild=MY_GUILD)
@app_commands.describe(niveau="Volume en pourcentage (0 à 150)")
async def set_volume(interaction: discord.Interaction, niveau: app_commands.Range[int, 0, 150]):
    """Modifie le volume de lecture en direct."""
    state = get_guild_state(interaction.guild.id)
    state.volume = niveau
    vc = interaction.guild.voice_client
    if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = niveau / 100.0
    await interaction.response.send_message(f"🔊 Volume réglé sur **{niveau}%**.")


@bot.tree.command(name="pause", description="Met la musique en pause", guild=MY_GUILD)
async def pause(interaction: discord.Interaction):
    """Met la piste en cours en pause."""
    vc = interaction.guild.voice_client
    state = get_guild_state(interaction.guild.id)
    if pause_track(vc, state):
        await interaction.response.send_message("⏸️ Musique en pause !")
    else:
        await interaction.response.send_message("Aucune musique n'est jouée actuellement.", ephemeral=True)


@bot.tree.command(name="skip", description="Passe la musique actuelle", guild=MY_GUILD)
async def skip(interaction: discord.Interaction):
    """Passe immédiatement à la musique suivante."""
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        get_guild_state(interaction.guild.id).skip_requested = True
        vc.stop()
        await interaction.response.send_message("⏭️ Musique passée !")
    else:
        await interaction.response.send_message("Rien à passer.", ephemeral=True)


@bot.tree.command(name="resume", description="Reprend la musique", guild=MY_GUILD)
async def resume(interaction: discord.Interaction):
    """Reprend la lecture mise en pause."""
    vc = interaction.guild.voice_client
    state = get_guild_state(interaction.guild.id)
    if resume_track(vc, state):
        await interaction.response.send_message("▶️ La musique reprend !")
    else:
        await interaction.response.send_message("La musique n'est pas en pause.", ephemeral=True)


@bot.tree.command(name="stop", description="Arrête la musique et déconnecte le bot", guild=MY_GUILD)
async def stop_command(interaction: discord.Interaction):
    """Vide la file, arrête le flux et déconnecte le bot du salon vocal."""
    state = get_guild_state(interaction.guild.id)
    reset_guild_playback(state)

    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("⏹️ Lecture arrêtée et bot déconnecté.")
    else:
        await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)


if __name__ == '__main__':
    try:
        bot.run(get_discord_token())
    except discord.LoginFailure as exc:
        print(f'Échec de connexion Discord : {exc}')
        sys.exit(1)
