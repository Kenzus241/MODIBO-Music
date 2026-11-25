# MODIBO (Mozart Discord Bot)

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7+-5865F2.svg)](https://discordpy.readthedocs.io/)
[![Linting: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## 📖 Résumé du projet

**MODIBO** est un bot musical Discord en Python permettant de rechercher et diffuser de l'audio YouTube en salon vocal via des commandes slash, avec un HUD interactif moderne (**Discord Components V2**) et une file d'attente par serveur.

## ✨ Fonctionnalités

* **Streaming audio optimisé** : extraction via `yt-dlp` en flux direct vers `ffmpeg` avec pré-tampon PCM en arrière-plan (`BufferedAudioSource`) pour éviter les micro-coupures.
* **HUD moderne (Components V2)** :
  * Miniature YouTube, lien cliquable, durée et demandeur.
  * **Barre de progression actualisée en temps réel** (toutes les 5 secondes).
  * Aperçu de la file d'attente intégré sous la piste en cours.
  * **Boutons interactifs persistants** : `Pause/Reprendre`, `Skip`, `Stop`, `Like`, `Boucle`, `Vol -`, `Vol +`, `File`.
* **Contrôle complet** : file d'attente, mode boucle, volume réglable (0 % à 150 %), déconnexion automatique après 6 minutes d'inactivité.

## 🛠️ Stack technique

* **Langage** : Python 3.10+
* **Bot Discord** : `discord.py` (>= 2.7.1)
* **Audio & Extraction** : `yt-dlp`, `ffmpeg`, `PyNaCl`
* **Configuration** : `python-dotenv`
* **Qualité du code** : `ruff`

## 📁 Structure du répertoire

| Fichier | Rôle |
| :--- | :--- |
| `MODIBO.py` | Point d'entrée principal (cerveau du bot, tâches de fond et commandes slash) |
| `config.py` | Configuration `.env`, options `yt-dlp` / `ffmpeg` et constantes |
| `state.py` | Gestion de l'état de lecture par serveur (`GuildMusicState`) |
| `audio.py` | Recherche YouTube, streaming `yt-dlp` et tampon audio PCM |
| `hud.py` | HUD Components V2, barre de progression, embeds et boutons de contrôle |
| `requirements.txt` | Dépendances Python du projet |
| `README.md` | Documentation du projet |

## 🎵 Commandes Slash

| Commande | Description |
| :--- | :--- |
| `/play_music <recherche>` | Recherche une musique (nom ou lien YouTube) et la joue ou l'ajoute à la file |
| `/music_controls` | Affiche le HUD interactif de contrôle de la musique en cours |
| `/queue` | Affiche la file d'attente (avec le HUD complet si une piste est en cours) |
| `/loop` | Active ou désactive la lecture en boucle |
| `/volume <niveau>` | Règle le volume du bot entre `0` et `150` % |
| `/pause` | Met la lecture en pause |
| `/resume` | Reprend la lecture mise en pause |
| `/skip` | Passe à la musique suivante |
| `/stop` | Arrête la musique, vide la file et déconnecte le bot |

## 🚀 Installation et utilisation

### 1. Prérequis

* **Python 3.10+**
* **FFmpeg** installé et accessible dans le `PATH`
* *(Recommandé pour `yt-dlp`)* Un runtime JavaScript (`node` ou `deno`)

Cloner le dépôt et installer les dépendances :

```bash
git clone <your-repo-url>
cd MODIBO-Music
pip install -r requirements.txt
```

### 2. Configuration

Créer un fichier `.env` à la racine du projet (vous pouvez copier `.env.example` s'il est présent) :

```env
DISCORD_TOKEN=your_discord_bot_token_here
SERV_ID=your_server_id_here
# Optionnel : chemin ou nom du runtime JS pour yt-dlp (ex: node, deno:/chemin/vers/deno)
YTDLP_JS_RUNTIME=node
```

> Le `DISCORD_TOKEN` doit être le token du bot Discord, pas l'identifiant de l'application.

### 3. Lancement

```bash
python3 MODIBO.py
```

### 4. Vérification du code (optionnel)

```bash
ruff check .
```
