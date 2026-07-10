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

MY_GUILD = discord.Object(id=os.getenv('SERV_ID'))
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

#Déclaration des fonctions

def get_playback_lock(guild_id):
    if guild_id not in playback_locks:
        playback_locks[guild_id] = asyncio.Lock()
    return playback_locks[guild_id]

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

    if not check_inactivity.is_running():
        check_inactivity.start()

    try:
        bot.tree.copy_global_to(guild=MY_GUILD)
        syncro = await bot.tree.sync(guild=MY_GUILD)
        print(f"Synchro INSTANTANÉE réussie : {len(syncro)} commandes sur ton serveur.")

    except Exception as error:
        print(f"Erreur de synchro: {error}")



@bot.tree.command(name="play_music", description="Joue la musique demandée")
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

    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        try:
            info = ydl.extract_info(recherche_amelioree, download=False)
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
            duree_min = video_choisie.get('duration', 0) / 60
        except Exception as e:
            return await interaction.followup.send(f"Erreur : {e}")

    guild_id = interaction.guild.id
    async with get_playback_lock(guild_id):
        channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if vc is None:
            vc = await channel.connect()

        if guild_id not in queues:
            queues[guild_id] = []

        queues[guild_id].append({
            'url': url, 
            'titre': titre, 
            'duree': duree_min
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
        url = musique['url']
        titre = musique['titre']
        duree = musique['duree']

        source = await discord.FFmpegOpusAudio.from_probe(url, **FFMPEG_OPTIONS)

        if vc.is_playing() or vc.is_paused():
            queues[guild_id].insert(0, musique)
            return
        
        vc.play(source, after=lambda e: bot.loop.create_task(play_next(interaction)))
        
        message = f"🎶 En cours : **{titre}**\nDurée : **{duree:.2f} min** 🎶"
        
        try:
            await interaction.followup.send(message)
        except:
            await interaction.channel.send(message)



@bot.tree.command(name="pause", description="Met la musique en pause")

async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸  Musique mise en pause.")
    else:
        await interaction.response.send_message("Rien n'est en cours de lecture.", ephemeral=True)



@bot.tree.command(name="skip", description="passe la music actuel")

async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("▶▶  SKIP !")
    else:
        await interaction.response.send_message("Rien n'est en cours de lecture")



@bot.tree.command(name="resume", description="Reprend la musique")

async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶  La musique reprend !")
    else:
        await interaction.response.send_message("La musique n'est pas en pause.", ephemeral=True)



0##LANCEMENT DU BOT


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
