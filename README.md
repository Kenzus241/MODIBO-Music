# MODIBO (Mozart discord Bot)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Linting: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## 📖 Résumer du Projet
le but du projet **MODIBO** est de créer un bot discord qui lance de la musique dépendant des commande tapé par l'utilisateur

## 🛠️ Technical Stack
Le projet est développer en **Python** en utilisant les librairies suivante:
* **librairie discord:** `discord.py`
* **qualité du code:** `ruff`

## 📁 Structure du répertoire
Le répertoire est organiser de la manière suivante:

| File | Description |
| :--- | :--- |
| `MODIBO.py` | main fonction du projet|
| `README` | le fichier d'information du Projet|
| `requirements.txt` | le ficheir avec toute les dépendances à installer pour que le projet fonctionne|

## 🚀 Installation and Usage

### 1. Prerequisites
Cloner le répertoire et installer les dépendances:
```bash
git clone <your-repo-url>
cd MODIBO-Music
pip install -r requirements.txt
```

### 2. Configuration
Créer un fichier `.env` à la racine du projet avec votre token Discord et l'identifiant de votre serveur :
```env
SERV_ID=your_server_id_here
DISCORD_TOKEN=your_discord_bot_token_here
```

> Le token doit être celui du bot Discord, pas l'identifiant de l'application ni une simple valeur numérique.

Si vous voulez un exemple de configuration, vous pouvez copier le fichier `.env.example` fourni avec le projet.
