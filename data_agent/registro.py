"""
Log strutturati (una riga JSON per evento) con l'ID di correlazione della richiesta.

Il back end Node genera un request_id per ogni domanda e lo passa al data agent
nell'intestazione HTTP X-Request-Id. Qui l'ID viene messo in una variabile di contesto
e aggiunto automaticamente a ogni riga di log: cercando quell'ID nei log dei due
servizi si ricostruisce tutto il percorso di una domanda.

Esempio di riga:
{"ts": "2026-10-17T15:02:11.204Z", "livello": "info", "servizio": "data-agent",
 "request_id": "6f1c...", "evento": "codice_eseguito", "ok": true, "durata_ms": 1830}
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class FormatoJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        riga = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "livello": record.levelname.lower(),
            "servizio": "data-agent",
            "request_id": request_id.get(),
            "evento": record.getMessage(),
        }
        riga.update(getattr(record, "dati", {}) or {})
        if record.exc_info:
            riga["eccezione"] = self.formatException(record.exc_info)
        return json.dumps(riga, ensure_ascii=False, default=str)


def configura(livello: int = logging.INFO) -> None:
    """Tutti i logger 'supplybot.*' scrivono JSON su stdout (lo raccoglie Render o il terminale)."""
    radice = logging.getLogger("supplybot")
    if any(isinstance(h.formatter, FormatoJson) for h in radice.handlers):
        return
    gestore = logging.StreamHandler(sys.stdout)
    gestore.setFormatter(FormatoJson())
    radice.addHandler(gestore)
    radice.setLevel(livello)
    radice.propagate = False
