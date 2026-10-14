# Immagine per la versione online di SupplyBot (Render, piano gratuito).
# Per stare nei limiti del piano gratuito i servizi girano nello stesso container,
# ma restano processi separati che comunicano via HTTP, come in locale:
#   - data agent Python (FastAPI) su 127.0.0.1:8000, raggiungibile solo dall'interno
#   - back end Node (orchestratore) sulla porta pubblica, che serve anche la pagina React
#   - l'indice del manuale sta su Chroma Cloud (in locale: server ChromaDB sul PC)
# Nessuna chiave nell'immagine: arrivano come variabili d'ambiente impostate su Render.

# --- 1. Compilazione del front end React -------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. Immagine finale ------------------------------------------------------------------
FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

# Python del data agent in un ambiente virtuale (solo le librerie del servizio)
RUN python3 -m venv /opt/venv
COPY data_agent/requirements-server.txt /app/data_agent/requirements-server.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/data_agent/requirements-server.txt

# Back end Node (solo dipendenze di produzione)
COPY backend/package.json backend/package-lock.json /app/backend/
RUN cd /app/backend && npm ci --omit=dev

# Codice, dati e front end compilato
COPY backend/ /app/backend/
COPY data_agent/ /app/data_agent/
COPY data/ /app/data/
COPY deploy/ /app/deploy/
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# Si esegue come utente non amministratore; il data agent deve poter scrivere i grafici
RUN mkdir -p /app/data_agent/charts && chown -R node:node /app/data_agent
USER node

ENV NODE_ENV=production \
    PORT=10000 \
    DATA_AGENT_URL=http://127.0.0.1:8000 \
    SANDBOX_TIMEOUT=120 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONUNBUFFERED=1 \
    NODE_OPTIONS=--max-old-space-size=256
EXPOSE 10000
CMD ["sh", "/app/deploy/avvia-online.sh"]
