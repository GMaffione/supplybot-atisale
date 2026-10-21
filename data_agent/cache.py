"""
Cache delle risposte gia' calcolate: la stessa domanda non rifa' l'analisi.

Un'analisi costa diverse chiamate all'LLM e alcuni secondi di CPU nella sandbox; molte
domande si ripetono (in una demo, quasi tutte). La cache tiene in memoria le ultime
risposte. La chiave e' la domanda normalizzata (minuscole, spazi uniformati) piu'
l'impronta del dataset.

QUANDO UNA RISPOSTA SMETTE DI VALERE (invalidazione)
1. Riavvio del servizio: la cache vive solo nella memoria del processo, quindi si SVUOTA
   DEL TUTTO a ogni riavvio. Online succede a ogni nuova pubblicazione e ogni volta che
   Render "addormenta" il servizio dopo 15 minuti senza visite.
2. Scadenza: una risposta piu' vecchia di `durata_secondi` (6 ore) si ricalcola.
3. Spazio: oltre `massimo` risposte si elimina quella usata meno di recente (LRU).
4. Dati cambiati: l'impronta SHA-256 del CSV fa parte della chiave, quindi con un CSV
   diverso le risposte vecchie non vengono piu' trovate.
5. Grafici spariti: se un file PNG citato dalla risposta non esiste piu', la risposta
   viene scartata e ricalcolata (controllo in agente_dati.py).
6. Esplicita: `svuota()` cancella tutto.
Non entrano mai in cache le analisi non concluse (nessun calcolo eseguito o passi esauriti).
"""

import re
import threading
import time
from collections import OrderedDict
from typing import Callable


def normalizza(testo: str) -> str:
    return re.sub(r"\s+", " ", testo.strip().lower()).rstrip(" ?!.")


class CacheRisposte:
    def __init__(self, massimo: int = 200, durata_secondi: int = 6 * 3600,
                 orologio: Callable[[], float] = time.monotonic):
        """`orologio` restituisce l'ora in secondi; nei test si sostituisce per simulare la scadenza."""
        self.massimo, self.durata, self.orologio = massimo, durata_secondi, orologio
        self._voci: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lucchetto = threading.Lock()
        self.trovate = self.mancate = self.scadute = 0

    def leggi(self, chiave: str) -> dict | None:
        with self._lucchetto:
            voce = self._voci.get(chiave)
            if voce is not None and self.orologio() - voce[0] > self.durata:
                del self._voci[chiave]
                self.scadute += 1
                voce = None
            if voce is None:
                self.mancate += 1
                return None
            self._voci.move_to_end(chiave)  # usata adesso: diventa la piu' recente
            self.trovate += 1
            return voce[1]

    def scrivi(self, chiave: str, valore: dict) -> None:
        with self._lucchetto:
            self._voci[chiave] = (self.orologio(), valore)
            self._voci.move_to_end(chiave)
            while len(self._voci) > self.massimo:
                self._voci.popitem(last=False)

    def scarta(self, chiave: str) -> None:
        with self._lucchetto:
            self._voci.pop(chiave, None)

    def svuota(self) -> None:
        """Cancella tutte le risposte (lo stesso effetto di un riavvio del servizio)."""
        with self._lucchetto:
            self._voci.clear()

    def stato(self) -> dict:
        return {"voci": len(self._voci), "trovate": self.trovate, "mancate": self.mancate,
                "scadute": self.scadute, "durata_ore": self.durata / 3600,
                "nota": "cache in memoria: si svuota a ogni riavvio del servizio"}
