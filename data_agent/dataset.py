"""
Il dataset si prepara UNA SOLA VOLTA, all'avvio del data agent, e poi resta in memoria.

Prima ogni domanda rileggeva il CSV dal disco, lo diagnosticava, lo puliva e lo riscriveva
in una cartella temporanea: due passi LLM, CPU e secondi spesi a ricalcolare sempre la
stessa cosa (critico sul piano gratuito di Render). I dati non cambiano tra una domanda e
l'altra, quindi la catena si esegue all'avvio:

    leggi il CSV -> valida lo schema -> diagnostica -> scegli le pulizie -> pulisci
                 -> valida il risultato -> salva il dataframe pronto (df.pkl)

La scelta delle pulizie segue la diagnosi, regola per regola: ogni anomalia trovata attiva
l'operazione che la corregge (`scegli_operazioni`). Profilo e report restano disponibili e
vengono mostrati in ogni risposta, nel pannello "Come ho risposto".

Se il CSV non supera la validazione il servizio NON analizza nulla e restituisce gli errori.
"""

import atexit
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pandas as pd

from pulizia import CSV_PREDEFINITO, OPERAZIONI, carica_grezzo, profila_dataset, pulisci_dati
from schema import DatasetNonValido, valida_grezzo, valida_pulito

log = logging.getLogger("supplybot.dataset")


def scegli_operazioni(profilo: dict) -> list[str]:
    """Dalla diagnosi alle pulizie: un'operazione si applica solo se c'e' l'anomalia che corregge."""
    scelte = []
    if profilo["duplicati_esatti"] > 0:
        scelte.append("rimuovi_duplicati")
    if profilo["linee_prodotto_scritte_in_modo_non_standard"] > 0 or profilo["regioni_scritte_in_modo_non_standard"] > 0:
        scelte.append("normalizza_testi")
    if profilo["quantita_negative"] > 0 or profilo["prezzi_fuori_scala"] > 0 or profilo["consegne_prima_dell_ordine"] > 0:
        scelte.append("neutralizza_valori_impossibili")
    return [o for o in OPERAZIONI if o in scelte]  # ordine stabile


class DatasetPronto:
    """Dataset validato e pulito, con profilo, report e percorso del dataframe salvato."""

    def __init__(self, percorso_csv: Path = CSV_PREDEFINITO):
        self.percorso_csv = Path(percorso_csv)
        inizio = time.perf_counter()

        try:
            grezzo = carica_grezzo(self.percorso_csv)  # l'unica lettura dal disco
        except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as e:
            # file mancante, illeggibile o non CSV: stesso trattamento di un CSV fuori schema
            raise DatasetNonValido("lettura del CSV", [f"{type(e).__name__}: {e}"]) from e
        self.validazione_grezzo = valida_grezzo(grezzo)
        if not self.validazione_grezzo["valido"]:
            raise DatasetNonValido("CSV grezzo", self.validazione_grezzo["errori"])

        self.profilo = profila_dataset(grezzo)
        self.operazioni = scegli_operazioni(self.profilo)
        self.df, self.report = pulisci_dati(grezzo, self.operazioni)

        self.validazione_pulito = valida_pulito(self.df)
        if not self.validazione_pulito["valido"]:
            raise DatasetNonValido("dati puliti", self.validazione_pulito["errori"])

        # il dataframe pronto si salva una volta: la sandbox lo carica da qui a ogni analisi
        self._cartella = Path(tempfile.mkdtemp(prefix="supplybot_dati_"))
        atexit.register(shutil.rmtree, self._cartella, True)  # cartella rimossa alla chiusura del servizio
        self.percorso_df = str(self._cartella / "df.pkl")
        self.df.to_pickle(self.percorso_df)

        # impronta del file: entra nella chiave della cache, cosi' un CSV diverso non riusa risposte vecchie
        self.impronta = hashlib.sha256(self.percorso_csv.read_bytes()).hexdigest()[:16]
        self.durata_ms = round((time.perf_counter() - inizio) * 1000)
        log.info("dataset_pronto", extra={"dati": {
            "righe": len(self.df), "operazioni": self.operazioni, "avvisi": self.validazione_grezzo["avvisi"],
            "impronta": self.impronta, "durata_ms": self.durata_ms}})

    def passi_preparazione(self) -> list[dict]:
        """I due passi mostrati nel pannello "Come ho risposto": eseguiti all'avvio, non a ogni domanda."""
        return [
            {"tipo": "profilo", "scopo": "Diagnosi del CSV grezzo (eseguita all'avvio del servizio)",
             "esito": {**self.profilo, "validazione_schema": self.validazione_grezzo}},
            {"tipo": "pulizia", "scopo": "Pulizia del dataset (eseguita all'avvio del servizio)", "esito": self.report},
        ]

    def riepilogo_per_agente(self) -> str:
        """Cosa l'LLM deve sapere della qualita' dei dati per scrivere la "Nota sui dati"."""
        r = self.report
        voci = [f"righe iniziali {r['righe_iniziali']}, finali {r['righe_finali']}",
                f"operazioni applicate: {', '.join(r['operazioni_applicate']) or 'nessuna'}"]
        for chiave in ("duplicati_rimossi", "linee_prodotto_normalizzate", "regioni_normalizzate",
                       "date_ordine_formato_gg_mm_aaaa", "prezzi_con_virgola_o_simbolo_euro",
                       "quantita_negative_annullate", "prezzi_fuori_scala_annullati",
                       "consegne_prima_dell_ordine_annullate", "consegnate_senza_data_consegna", "prezzi_mancanti"):
            if r.get(chiave):
                voci.append(f"{chiave.replace('_', ' ')}: {r[chiave]}")
        return "; ".join(voci) + ". " + r["note"]

    def stato(self) -> dict:
        return {"righe": len(self.df), "operazioni": self.operazioni, "impronta": self.impronta,
                "avvisi_schema": self.validazione_grezzo["avvisi"], "preparato_in_ms": self.durata_ms}


_dataset: DatasetPronto | None = None
_errore: DatasetNonValido | None = None
_lucchetto = threading.Lock()


def prepara(percorso_csv: Path | None = None) -> DatasetPronto:
    """
    Prepara (una volta) e restituisce il dataset; solleva DatasetNonValido se il CSV non va bene.
    Il file e' data/spedizioni_clienti.csv, oppure quello indicato nella variabile DATASET_CSV.
    """
    global _dataset, _errore
    with _lucchetto:
        if _dataset is None and _errore is None:
            percorso = Path(percorso_csv or os.getenv("DATASET_CSV") or CSV_PREDEFINITO)
            try:
                _dataset = DatasetPronto(percorso)
            except DatasetNonValido as e:
                _errore = e
                log.error("dataset_non_valido", extra={"dati": {"fase": e.fase, "errori": e.errori}})
        if _errore is not None:
            raise _errore
        return _dataset


def azzera() -> None:
    """Solo per i test: dimentica il dataset preparato."""
    global _dataset, _errore
    with _lucchetto:
        _dataset, _errore = None, None
