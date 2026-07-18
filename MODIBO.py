### IMPORTTATION DES MODULES
import asyncio
import os
import sys

import discord
from dotenv import load_dotenv
from discord.ext import commands
from discord import app_commands
import yt_dlp
from discord.ext import tasks

load_dotenv()

##INITIALIASTION DU BOT


#Déclaration des variables

intents = discord.Intents.all()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=discord.Intents.all())
client = discord.Client(intents=intents)

SERV_ID = os.getenv('SERV_ID', '').strip()
MY_GUILD = discord.Object(id=int(SERV_ID)) if SERV_ID else None
YTDLP_JS_RUNTIME = os.getenv('YTDLP_JS_RUNTIME', 'node')
YTDLP_JS_RUNTIME_NAME, YTDLP_JS_RUNTIME_PATH = [*YTDLP_JS_RUNTIME.split(':', 1), None][:2]

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch',
    'ignoreerrors': True,
    'js_runtimes': {YTDLP_JS_RUNTIME_NAME.lower(): {'path': YTDLP_JS_RUNTIME_PATH}},
    'remote_components': ['ejs:github'],
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

inactivity_counts = {}

queues = {}
playback_locks = {}
current_tracks = {}
loop_modes = {}
volumes = {}
music_control_view_registered = False

#Déclaration des fonctions

def get_playback_lock(guild_id):
    if guild_id not in playback_locks:
        playback_locks[guild_id] = asyncio.Lock()
    return playback_locks[guild_id]


def format_duration(seconds):
    seconds = int(seconds or 0)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def build_progress_bar():
    return "🔘━━━━━━━━━━━━━━━━━━━━"


def build_now_playing_embed(musique, guild_id):
    duree = musique.get('duree', 0)
    queue_size = len(queues.get(guild_id, []))
    volume = volumes.get(guild_id, 100)
    loop_status = "On" if loop_modes.get(guild_id, False) else "Off"

    embed = discord.Embed(
        title="Now playing",
        color=discord.Color(0x808080),
    )
    embed.description = (
        f"**[{musique['titre']}]({musique.get('webpage_url') or musique['url']})**\n"
        f"• Added by {musique.get('added_by', 'Inconnu')}\n\n"
        f"Queue Size: `{queue_size}` · Volume: `{volume}%` · Loop: `{loop_status}`\n"
        f"`0:00` {build_progress_bar()} `{format_duration(duree)}`"
    )

    if musique.get('thumbnail'):
        embed.set_thumbnail(url=musique['thumbnail'])

    return embed


def build_now_playing_text(musique, guild_id):
    duree = musique.get('duree', 0)
    queue_size = len(queues.get(guild_id, []))
    volume = volumes.get(guild_id, 100)
    loop_status = "On" if loop_modes.get(guild_id, False) else "Off"

    return (
        "### Now playing\n"
        f"**{musique['titre']}**\n"
        f"• Added by {musique.get('added_by', 'Inconnu')}\n"
        f"• Queue Size: `{queue_size}` · Volume: `{volume}%` · Loop: `{loop_status}`\n\n"
        f"`0:00` {build_progress_bar()} `{format_duration(duree)}`"
    )

def build_now_playing_embed(musique, guild_id):
    duree = musique.get('duree', 0)
    queue_size = len(queues.get(guild_id, []))
    volume = volumes.get(guild_id, 100)
    loop_status = "On" if loop_modes.get(guild_id, False) else "Off"

    embed = discord.Embed(
        color=discord.Color(0x5865F2) 
    )
    
    embed.description = (
        f"✅ Added **[{musique['titre']}]({musique.get('webpage_url') or musique['url']})**\n"
        f"`{format_duration(duree)}` to the queue.\n\n"
        f"• Added by {musique.get('added_by', 'Inconnu')}\n"
        f"• Queue Size: `{queue_size}` · Volume: `{volume}%` · Loop: `{loop_status}`\n\n"
        f"`0:00` {build_progress_bar()} `{format_duration(duree)}`"
    )

    if musique.get('thumbnail'):
        embed.set_thumbnail(url=musique['thumbnail'])

    return embed

def can_send_embeds(channel, guild):
    if not channel or not guild or not guild.me:
        return False

    return channel.permissions_for(guild.me).embed_links


async def send_now_playing(interaction, musique, guild_id, view):
    
    if can_send_embeds(interaction.channel, interaction.guild):
        embed = build_now_playing_embed(musique, guild_id)
        try:
            return await interaction.followup.send(embed=embed, view=view)
        except:
            return await interaction.channel.send(embed=embed, view=view)

    message = build_now_playing_text(musique, guild_id)
    try:
        return await interaction.followup.send(content=message, view=view)
    except:
        return await interaction.channel.send(content=message, view=view)

class MusicControlView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Pause",
        style=discord.ButtonStyle.grey,
        emoji="⏸️",
        custom_id="music_control:pause_resume",
    )
    async def play_pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc:
            return await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

        if vc.is_playing():
            vc.pause()
            button.label = "Resume"
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("⏸️ Musique en pause.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            button.label = "Pause"
            button.emoji = "⏸️"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("▶️ Reprise de la musique.", ephemeral=True)
        else:
            await interaction.response.send_message("Rien n'est en cours de lecture.", ephemeral=True)

    @discord.ui.button(
        label="Skip",
        style=discord.ButtonStyle.grey,
        emoji="⏭️",
        custom_id="music_control:skip",
    )
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Musique passée !", ephemeral=True)
        else:
            await interaction.response.send_message("Rien à passer.", ephemeral=True)

    @discord.ui.button(
        label="Stop",
        style=discord.ButtonStyle.grey,
        emoji="⏹️",
        custom_id="music_control:stop",
    )
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client if interaction.guild else None
        guild_id = interaction.guild.id if interaction.guild else None

        if guild_id:
            queues[guild_id] = []
            current_tracks.pop(guild_id, None)
            inactivity_counts[guild_id] = 0

        if vc:
            await vc.disconnect()
            await interaction.response.send_message("⏹️ Bot déconnecté et playlist vidée.", ephemeral=True)
        else:
            await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

    @discord.ui.button(
        label="Like",
        style=discord.ButtonStyle.secondary,
        emoji="❤️",
        custom_id="music_control:like",
    )
    async def like_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❤️ Titre liké !", ephemeral=True)


def register_music_control_view():
    global music_control_view_registered
    if not music_control_view_registered:
        bot.add_view(MusicControlView(bot))
        music_control_view_registered = True

@tasks.loop(minutes=1)

async def check_inactivity():
    '''
    la fonction qui vérifie l'inactivité du bot
    '''
    for guild in bot.guilds:
        vc = guild.voice_client

        if vc:
            if not vc.is_playing() and not vc.is_paused():
                inactivity_counts[guild.id] = inactivity_counts.get(guild.id, 0) + 1

                if inactivity_counts[guild.id] >= 6:
                    await vc.disconnect()
                    print(f"Déconnection pour inactivité {guild.name}")
                    inactivity_counts[guild.id] = 0
            else:
                inactivity_counts[guild.id] = 0

@bot.event

async def on_ready():
    '''
    la fonction qui initialise le lancement du bot
    '''
    print(f"Connecté en tant que {bot.user.name}")
    register_music_control_view()

    if not check_inactivity.is_running():
        check_inactivity.start()

    try:
        syncro = await bot.tree.sync(guild=MY_GUILD)
        commandes = ', '.join(command.name for command in bot.tree.get_commands(guild=MY_GUILD))
        print(f"Commandes chargées localement : {commandes}")
        print(f"Synchro INSTANTANÉE réussie : {len(syncro)} commandes sur ton serveur.")

    except Exception as error:
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
    '''
    la fonction principal qui lance la recherche et initialise le lancement de la musique
        @interaction: permet l'interaction avec l'utilisateur
        @recherche: la recherche éffectuer par l'utilisateur
    '''
    if interaction.user.voice is None:
        return await interaction.response.send_message("Tu dois être dans un salon vocal pour ça !")
    
    await interaction.response.defer()

    recherche_amelioree = f"ytsearch5:{recherche} audio"

    try:
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, recherche_amelioree, download=False)

        video_choisie = None
        if 'entries' in info:
            for entry in info['entries']:
                if entry and entry.get('duration', 0) < 600:
                    video_choisie = entry
                    break
        if not video_choisie:
            return await interaction.followup.send("Musique introuvable ou trop longue.")

        url = video_choisie['url']
        titre = video_choisie['title']
        duree = video_choisie.get('duration', 0)
        thumbnail = video_choisie.get('thumbnail')
        webpage_url = video_choisie.get('webpage_url') or video_choisie.get('original_url')
    except Exception as e:
        return await interaction.followup.send(f"Erreur : {e}")

    guild_id = interaction.guild.id
    async with get_playback_lock(guild_id):
        register_music_control_view()
        channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if vc is None:
            vc = await channel.connect()

        if guild_id not in queues:
            queues[guild_id] = []

        queues[guild_id].append({
            'url': url, 
            'titre': titre, 
            'duree': duree,
            'thumbnail': thumbnail,
            'webpage_url': webpage_url,
            'added_by': interaction.user.mention,
        })

        if not vc.is_playing() and not vc.is_paused():
            await play_next_unlocked(interaction)
        else:
            await interaction.followup.send(f"Ajoute a la playlist : **{titre}**")



async def play_next(interaction):
    async with get_playback_lock(interaction.guild.id):
        await play_next_unlocked(interaction)



async def play_next_unlocked(interaction):
    guild_id = interaction.guild.id
    if guild_id in queues and len(queues[guild_id]) > 0:
        vc = interaction.guild.voice_client
        if not vc or vc.is_playing() or vc.is_paused():
            return

        musique = queues[guild_id].pop(0)
        current_tracks[guild_id] = musique
        url = musique['url']

        source = await discord.FFmpegOpusAudio.from_probe(url, **FFMPEG_OPTIONS)

        if vc.is_playing() or vc.is_paused():
            queues[guild_id].insert(0, musique)
            return
        
        vc.play(source, after=lambda e: bot.loop.create_task(play_next(interaction)))
        
        view = MusicControlView(bot)
        await send_now_playing(interaction, musique, guild_id, view)



@bot.tree.command(name="music_controls", description="Affiche le menu de contrôle de la musique", guild=MY_GUILD)

async def music_controls(interaction: discord.Interaction):
    register_music_control_view()
    guild_id = interaction.guild.id
    musique = current_tracks.get(guild_id)
    view = MusicControlView(bot)

    if musique:
        message = build_now_playing_text(musique, guild_id)
        if can_send_embeds(interaction.channel, interaction.guild):
            embed = build_now_playing_embed(musique, guild_id)
            return await interaction.response.send_message(content=message, embed=embed, view=view)

        return await interaction.response.send_message(message, view=view)

    await interaction.response.send_message(
        "### Now playing\nAucune musique en cours.",
        view=view,
        ephemeral=True,
    )



@bot.tree.command(name="pause", description="Met la musique en pause", guild=MY_GUILD)

async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸  Musique mise en pause.")
    else:
        await interaction.response.send_message("Rien n'est en cours de lecture.", ephemeral=True)



@bot.tree.command(name="skip", description="passe la music actuel", guild=MY_GUILD)

async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("▶▶  SKIP !")
    else:
        await interaction.response.send_message("Rien n'est en cours de lecture")



@bot.tree.command(name="resume", description="Reprend la musique", guild=MY_GUILD)

async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶  La musique reprend !")
    else:
        await interaction.response.send_message("La musique n'est pas en pause.", ephemeral=True)



##LANCEMENT DU BOT


def get_discord_token():
    token = os.getenv('DISCORD_TOKEN', '').strip()

    if not token:
        print('Aucun token Discord trouvé. Ajoute DISCORD_TOKEN dans le fichier .env avec le token de ton bot Discord.')
        sys.exit(1)

    if token.isdigit():
        print('Le token Discord fourni semble invalide. Utilise le token du bot Discord, pas l\'ID de l\'application.')
        sys.exit(1)

    return token


try:
    bot.run(get_discord_token())
except discord.LoginFailure as exc:
    print(f'Échec de connexion Discord : {exc}')
    sys.exit(1)
