"""
Cache delle risposte gia' calcolate: la stessa domanda non rifa' l'analisi.

Un'analisi costa diverse chiamate all'LLM e alcuni secondi di CPU nella sandbox; molte
domande si ripetono (in una demo, quasi tutte). La cache tiene in memoria le ultime
risposte, con due limiti:
- dimensione massima (LRU: quando e' piena si scarta la risposta usata meno di recente);
- scadenza: dopo un certo tempo la risposta si ricalcola comunque.

La chiave e' la domanda normalizzata (minuscole, spazi uniformati) piu' l'impronta del
dataset: se il CSV cambia, le risposte vecchie non vengono piu' trovate.
"""

import re
import threading
import time
from collections import OrderedDict


def normalizza(testo: str) -> str:
    return re.sub(r"\s+", " ", testo.strip().lower()).rstrip(" ?!.")


class CacheRisposte:
    def __init__(self, massimo: int = 200, durata_secondi: int = 6 * 3600):
        self.massimo, self.durata = massimo, durata_secondi
        self._voci: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lucchetto = threading.Lock()
        self.trovate = self.mancate = 0

    def leggi(self, chiave: str) -> dict | None:
        with self._lucchetto:
            voce = self._voci.get(chiave)
            if voce is None or time.monotonic() - voce[0] > self.durata:
                self._voci.pop(chiave, None)
                self.mancate += 1
                return None
            self._voci.move_to_end(chiave)
            self.trovate += 1
            return voce[1]

    def scrivi(self, chiave: str, valore: dict) -> None:
        with self._lucchetto:
            self._voci[chiave] = (time.monotonic(), valore)
            self._voci.move_to_end(chiave)
            while len(self._voci) > self.massimo:
                self._voci.popitem(last=False)

    def scarta(self, chiave: str) -> None:
        with self._lucchetto:
            self._voci.pop(chiave, None)

    def stato(self) -> dict:
        return {"voci": len(self._voci), "trovate": self.trovate, "mancate": self.mancate}
