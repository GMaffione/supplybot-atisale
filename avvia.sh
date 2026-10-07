#!/usr/bin/env bash
# Avvio di SupplyBot su macOS / Linux: i quattro servizi in background, Ctrl+C li ferma tutti
cd "$(dirname "$0")"
[ -f .env ] || { echo "ERRORE: manca il file .env (copia .env.example e inserisci la chiave)."; exit 1; }
[ -d .venv ] || { echo "ERRORE: esegui prima ./installa.sh"; exit 1; }
trap 'kill 0' EXIT
.venv/bin/chroma run --path backend/chroma_db --port 8001 > /dev/null 2>&1 &
(cd data_agent && ../.venv/bin/python -m uvicorn app:app --port 8000) &
(cd backend && npm start) &
(cd frontend && npm run dev) &
sleep 12
echo "SupplyBot pronto su http://localhost:5173 (Ctrl+C per fermare tutto)"
wait
