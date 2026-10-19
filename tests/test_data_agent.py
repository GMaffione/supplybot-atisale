"""
Test unitari del data agent: validazione dello schema, dataset preparato una volta,
cache delle risposte, riproducibilita' del generatore.

Niente chiave OpenAI e niente rete: l'LLM e' sostituito da un finto client che chiama
lo strumento esegui_python e poi risponde. Solo libreria standard (unittest).

Uso (dalla radice del progetto):  python -m unittest tests/test_data_agent.py -v
"""

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "data_agent"))

import pandas as pd  # noqa: E402
from openai.types.chat import ChatCompletion  # noqa: E402

import dataset as modulo_dataset  # noqa: E402
from agente_dati import DataAgent  # noqa: E402
from cache import CacheRisposte  # noqa: E402
from pulizia import carica_grezzo, profila_dataset, pulisci_dati  # noqa: E402
from schema import DatasetNonValido, valida_grezzo, valida_pulito  # noqa: E402

CSV = RADICE / "data" / "spedizioni_clienti.csv"
CODICE = "print(round(df.qty_delivered_t.sum(), 1))"


class FintoLLM:
    """Imita l'API chat.completions: prima chiama esegui_python, poi risponde con l'output."""

    def __init__(self):
        self.chiamate = 0
        self.chat = self
        self.completions = self

    def create(self, **parametri):
        self.chiamate += 1
        messaggi = parametri["messages"]
        if messaggi[-1]["role"] == "tool":
            msg = {"role": "assistant", "content": f"Totale consegnato: {messaggi[-1]['content'].strip()} t"}
        else:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{self.chiamate}", "type": "function",
                "function": {"name": "esegui_python", "arguments": f'{{"scopo": "somma", "codice": "{CODICE}"}}'}}]}
        return ChatCompletion.model_validate({
            "id": "x", "object": "chat.completion", "created": 0, "model": "finto",
            "choices": [{"index": 0, "finish_reason": "stop", "message": msg}]})


def csv_modificato(modifica) -> Path:
    """Scrive in una cartella temporanea una copia del CSV alterata da `modifica(df)`."""
    df = carica_grezzo(CSV)
    df = modifica(df)
    percorso = Path(tempfile.mkdtemp()) / "spedizioni.csv"
    df.to_csv(percorso, index=False)
    return percorso


class TestSchema(unittest.TestCase):
    def test_csv_reale_valido_con_soli_avvisi(self):
        esito = valida_grezzo(carica_grezzo(CSV))
        self.assertTrue(esito["valido"], esito["errori"])
        self.assertTrue(any("qty_t" in a for a in esito["avvisi"]))  # le quantita' negative sono correggibili

    def test_colonna_mancante_e_un_errore(self):
        esito = valida_grezzo(carica_grezzo(CSV).drop(columns=["carrier"]))
        self.assertFalse(esito["valido"])
        self.assertIn("carrier", esito["errori"][0])

    def test_valori_sconosciuti_oltre_soglia(self):
        def rompi(df):
            df.loc[df.index[:200], "status"] = "Smarrita"
            return df
        esito = valida_grezzo(rompi(carica_grezzo(CSV)))
        self.assertFalse(esito["valido"])
        self.assertTrue(any(e.startswith("status") for e in esito["errori"]))

    def test_date_illeggibili_oltre_soglia(self):
        def rompi(df):
            df.loc[df.index[:300], "order_date"] = "ieri"
            return df
        esito = valida_grezzo(rompi(carica_grezzo(CSV)))
        self.assertFalse(esito["valido"])
        self.assertTrue(any(e.startswith("order_date") for e in esito["errori"]))

    def test_codice_fuori_formato(self):
        def rompi(df):
            df.loc[df.index[0], "shipment_id"] = "123"
            return df
        self.assertFalse(valida_grezzo(rompi(carica_grezzo(CSV)))["valido"])

    def test_validazione_rigida_dopo_la_pulizia(self):
        df, _ = pulisci_dati(carica_grezzo(CSV))
        self.assertTrue(valida_pulito(df)["valido"])
        df.loc[0, "qty_t"] = -5
        self.assertFalse(valida_pulito(df)["valido"])

    def test_senza_rimozione_duplicati_il_pulito_non_passa(self):
        df, _ = pulisci_dati(carica_grezzo(CSV), ["normalizza_testi", "neutralizza_valori_impossibili"])
        esito = valida_pulito(df)
        self.assertFalse(esito["valido"])
        self.assertTrue(any("shipment_id" in e for e in esito["errori"]))


class TestDatasetPronto(unittest.TestCase):
    def setUp(self):
        modulo_dataset.azzera()

    def tearDown(self):
        modulo_dataset.azzera()

    def test_operazioni_scelte_dalla_diagnosi(self):
        profilo = profila_dataset(carica_grezzo(CSV))
        self.assertEqual(modulo_dataset.scegli_operazioni(profilo),
                         ["rimuovi_duplicati", "normalizza_testi", "neutralizza_valori_impossibili"])
        pulito = {k: (0 if isinstance(v, int) else v) for k, v in profilo.items()}
        self.assertEqual(modulo_dataset.scegli_operazioni(pulito), [])

    def test_csv_non_valido_blocca_il_servizio(self):
        percorso = csv_modificato(lambda df: df.drop(columns=["status"]))
        with self.assertRaises(DatasetNonValido) as e:
            modulo_dataset.prepara(percorso)
        self.assertIn("status", e.exception.errori[0])

    def test_csv_letto_una_sola_volta(self):
        dati = modulo_dataset.prepara(CSV)
        llm = FintoLLM()
        agente = DataAgent(client=llm, dataset=dati, cache=CacheRisposte())
        with mock.patch("pandas.read_csv", side_effect=AssertionError("CSV riletto durante una domanda")):
            r1 = agente.analizza("Quante tonnellate abbiamo consegnato?")
            r2 = agente.analizza("Quante tonnellate abbiamo consegnato in totale?")
        self.assertIn("Totale consegnato", r1["sintesi"])
        self.assertTrue(r2["sintesi"])
        self.assertIs(modulo_dataset.prepara(CSV), dati)  # stessa istanza: nessuna nuova preparazione
        tipi = [p["tipo"] for p in r1["passi"]]
        self.assertEqual(tipi[:3], ["profilo", "pulizia", "codice"])  # diagnosi e pulizia restano visibili
        self.assertEqual(llm.chiamate, 4)  # 2 per domanda: niente piu' passi LLM per profilare e pulire


class TestCache(unittest.TestCase):
    def setUp(self):
        modulo_dataset.azzera()

    def test_domanda_ripetuta_servita_dalla_cache(self):
        llm = FintoLLM()
        agente = DataAgent(client=llm, dataset=modulo_dataset.prepara(CSV), cache=CacheRisposte())
        prima = agente.analizza("Quante tonnellate abbiamo consegnato?")
        seconda = agente.analizza("  quante tonnellate abbiamo consegnato  ")
        self.assertFalse(prima["da_cache"])
        self.assertTrue(seconda["da_cache"])
        self.assertEqual(prima["sintesi"], seconda["sintesi"])
        self.assertEqual(llm.chiamate, 2)

    def test_scadenza_e_dimensione_massima(self):
        cache = CacheRisposte(massimo=2, durata_secondi=60)
        for i in range(3):
            cache.scrivi(str(i), {"n": i})
        self.assertIsNone(cache.leggi("0"))           # la meno recente e' stata scartata
        self.assertEqual(cache.leggi("2"), {"n": 2})
        scaduta = CacheRisposte(durata_secondi=0)
        scaduta.scrivi("a", {})
        self.assertIsNone(scaduta.leggi("a"))


class TestGeneratore(unittest.TestCase):
    def _genera(self, seme, percorso):
        subprocess.run([sys.executable, str(RADICE / "data" / "genera_dataset.py"), "--seed", str(seme),
                        "--output", str(percorso)], check=True, capture_output=True)
        # confronto indipendente dai fine riga (su Windows git puo' convertirli)
        return hashlib.sha256(percorso.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    def test_stesso_seme_stesso_file(self):
        cartella = Path(tempfile.mkdtemp())
        a = self._genera(42, cartella / "a.csv")
        b = self._genera(42, cartella / "b.csv")
        c = self._genera(7, cartella / "c.csv")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        originale = hashlib.sha256(CSV.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(a, originale, "il CSV del repository non corrisponde al seme 42")


if __name__ == "__main__":
    unittest.main()
