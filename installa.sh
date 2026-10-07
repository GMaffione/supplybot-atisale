#!/usr/bin/env bash
# Installazione di SupplyBot su macOS / Linux (equivalente di installa.bat)
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "ERRORE: Python 3.10-3.13 non trovato."; exit 1; }
command -v npm >/dev/null || { echo "ERRORE: Node.js 20+ non trovato."; exit 1; }
echo "[1/3] Ambiente Python e dipendenze del data agent..."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r data_agent/requirements.txt
echo "[2/3] Dipendenze del back end Node..."
(cd backend && npm install)
echo "[3/3] Dipendenze del front end React..."
(cd frontend && npm install)
if [ ! -f .env ]; then cp .env.example .env; echo "Creato .env: inserisci la tua OPENAI_API_KEY prima di avviare."; fi
echo "Installazione completata. Per avviare: ./avvia.sh"
