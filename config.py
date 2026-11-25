"""Configuration et constantes globales du bot MODIBO Music."""

import os
import shutil
import sys

import discord
from dotenv import load_dotenv

load_dotenv()

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

HUD_COLOR = discord.Color(0x00A8FC)
MAX_TRACK_DURATION_SECONDS = 600
INACTIVITY_LIMIT_MINUTES = 6


def get_discord_token() -> str:
    """Récupère et valide le token Discord depuis l'environnement."""
    token = os.getenv('DISCORD_TOKEN', '').strip()

    if not token:
        print('Aucun token Discord trouvé. Ajoute DISCORD_TOKEN dans le fichier .env avec le token de ton bot Discord.')
        sys.exit(1)

    if token.isdigit():
        print("Le token Discord fourni semble invalide. Utilise le token du bot Discord, pas l'ID de l'application.")
        sys.exit(1)

    return token
