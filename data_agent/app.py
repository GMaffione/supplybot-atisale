"""
Microservizio FastAPI del data agent.

Endpoint:
  GET  /health           -> stato del servizio, del dataset e della cache
  POST /analyze          -> {"domanda": "..."} -> sintesi, grafici, passi del ragionamento
  GET  /charts/{nome}    -> immagine PNG di un grafico generato

All'avvio il dataset viene letto, validato e pulito UNA volta (dataset.py). Se il CSV non
rispetta lo schema il servizio resta acceso ma risponde 503 con l'elenco degli errori,
invece di analizzare dati sbagliati.

Ogni richiesta porta un ID di correlazione (intestazione X-Request-Id, generata dal back end):
finisce in tutte le righe di log JSON di quella richiesta (registro.py).

Avvio (dalla cartella data_agent):  uvicorn app:app --port 8000
"""

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import registro
from agente_dati import CARTELLA_GRAFICI, DataAgent
from schema import DatasetNonValido

registro.configura()
log = logging.getLogger("supplybot.app")
ID_VALIDO = re.compile(r"^[A-Za-z0-9-]{8,64}$")

agente: DataAgent | None = None
errore_dataset: DatasetNonValido | None = None


@asynccontextmanager
async def avvio(_app: FastAPI):
    """Prepara il dataset prima di accettare richieste."""
    global agente, errore_dataset
    try:
        agente = DataAgent()
        log.info("servizio_pronto", extra={"dati": {"modello": agente.modello, **agente.dataset.stato()}})
    except DatasetNonValido as e:
        errore_dataset = e
        log.error("servizio_senza_dataset", extra={"dati": {"fase": e.fase, "errori": e.errori}})
    yield


app = FastAPI(title="SupplyBot Data Agent", version="1.1", lifespan=avvio)


@app.middleware("http")
async def id_di_correlazione(request: Request, call_next):
    ricevuto = request.headers.get("x-request-id", "")
    rid = ricevuto if ID_VALIDO.match(ricevuto) else uuid.uuid4().hex
    token = registro.request_id.set(rid)
    inizio = time.perf_counter()
    try:
        risposta = await call_next(request)
        # una riga per richiesta HTTP (sostituisce il log di accesso testuale di uvicorn)
        if request.url.path != "/health":
            log.info("richiesta_http", extra={"dati": {
                "metodo": request.method, "percorso": request.url.path, "stato": risposta.status_code,
                "durata_ms": round((time.perf_counter() - inizio) * 1000)}})
    finally:
        registro.request_id.reset(token)
    risposta.headers["X-Request-Id"] = rid
    return risposta


class Richiesta(BaseModel):
    domanda: str = Field(..., min_length=3, max_length=2000)


def _dataset_non_valido() -> JSONResponse:
    return JSONResponse(status_code=503, content={
        "detail": f"Il dataset non rispetta lo schema atteso ({errore_dataset.fase}): analisi non disponibili.",
        "errori": errore_dataset.errori})


@app.get("/health")
def health():
    if agente is None:
        return _dataset_non_valido()
    return {"stato": "ok", "modello": agente.modello, "dataset": agente.dataset.stato(), "cache": agente.cache.stato()}


@app.post("/analyze")
def analyze(richiesta: Richiesta):
    if agente is None:
        return _dataset_non_valido()
    log.info("analisi_richiesta", extra={"dati": {"domanda": richiesta.domanda[:300]}})
    try:
        esito = agente.analizza(richiesta.domanda)
    except Exception as e:  # errore dell'API OpenAI o imprevisto: messaggio chiaro al back end
        log.exception("analisi_fallita")
        raise HTTPException(status_code=502, detail=f"Errore del data agent: {type(e).__name__}: {e}") from e
    log.info("analisi_completata", extra={"dati": {"da_cache": esito["da_cache"], "grafici": len(esito["grafici"]),
                                                   "durata_ms": esito.get("durata_ms")}})
    return esito


@app.get("/charts/{nome}")
def chart(nome: str):
    # solo nomi generati da noi: niente percorsi arbitrari
    if not re.fullmatch(r"grafico_[0-9a-f]{10}\.png", nome):
        raise HTTPException(status_code=400, detail="Nome file non valido")
    percorso = CARTELLA_GRAFICI / nome
    if not percorso.exists():
        raise HTTPException(status_code=404, detail="Grafico non trovato")
    return FileResponse(percorso, media_type="image/png")
