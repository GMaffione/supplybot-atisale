#!/bin/sh
# Avvio della versione online (dentro il container): prima il data agent, poi il back end.
# Il data agent ascolta solo su 127.0.0.1: dall'esterno si raggiunge unicamente il back end.
set -e

cd /app/data_agent
/opt/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 &

cd /app/backend
exec node src/server.js
