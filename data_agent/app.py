"""
Microservizio FastAPI del data agent.

Endpoint:
  GET  /health           -> stato del servizio
  POST /analyze          -> {"domanda": "..."} -> sintesi, grafici, passi del ragionamento
  GET  /charts/{nome}    -> immagine PNG di un grafico generato

Avvio (dalla cartella data_agent):  uvicorn app:app --port 8000
"""

import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agente_dati import CARTELLA_GRAFICI, DataAgent

app = FastAPI(title="SupplyBot Data Agent", version="1.0")
agente = DataAgent(verbose=True)  # verbose: il ragionamento si vede nel terminale del servizio


class Richiesta(BaseModel):
    domanda: str = Field(..., min_length=3, max_length=2000)


@app.get("/health")
def health():
    return {"stato": "ok", "modello": agente.modello}


@app.post("/analyze")
def analyze(richiesta: Richiesta):
    try:
        return agente.analizza(richiesta.domanda)
    except Exception as e:  # errore dell'API OpenAI o imprevisto: messaggio chiaro al back end
        raise HTTPException(status_code=502, detail=f"Errore del data agent: {type(e).__name__}: {e}") from e


@app.get("/charts/{nome}")
def chart(nome: str):
    # solo nomi generati da noi: niente percorsi arbitrari
    if not re.fullmatch(r"grafico_[0-9a-f]{10}\.png", nome):
        raise HTTPException(status_code=400, detail="Nome file non valido")
    percorso = CARTELLA_GRAFICI / nome
    if not percorso.exists():
        raise HTTPException(status_code=404, detail="Grafico non trovato")
    return FileResponse(percorso, media_type="image/png")
