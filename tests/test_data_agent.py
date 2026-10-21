"""
Test unitari del data agent: validazione dello schema (anche nei casi limite), dataset
preparato una volta, cache delle risposte e sua invalidazione, API (validazione delle
richieste e request_id), riproducibilita' del generatore.

Niente chiave OpenAI e niente rete: l'LLM e' sostituito da un finto client che chiama
lo strumento esegui_python e poi risponde. Solo libreria standard (unittest).

Uso (dalla radice del progetto):  python -m unittest tests/test_data_agent.py -v
"""

import hashlib
import json
import os
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
                "function": {"name": "esegui_python",
                             "arguments": json.dumps({"scopo": "calcolo", "codice": CODICE})}}]}
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


class FintoLLMSenzaCalcoli(FintoLLM):
    """Risponde sempre a parole, senza mai eseguire codice: l'analisi resta non conclusa."""

    def create(self, **parametri):
        self.chiamate += 1
        return ChatCompletion.model_validate({
            "id": "x", "object": "chat.completion", "created": 0, "model": "finto",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "Circa 5.000 t, a occhio."}}]})


class OrologioFinto:
    """Orologio controllabile: permette di far 'passare' ore in un istante."""

    def __init__(self):
        self.ora = 1000.0

    def __call__(self):
        return self.ora


class TestValidazioneCasiLimite(unittest.TestCase):
    """Il confine tra anomalia correggibile (avviso) e file da rifiutare (errore)."""

    def setUp(self):
        self.grezzo = carica_grezzo(CSV)
        self.n = len(self.grezzo)

    def _con(self, colonna, valore, quante):
        df = self.grezzo.copy()
        df.loc[df.index[:quante], colonna] = valore
        return valida_grezzo(df)

    def test_soglia_del_5_per_cento_esatta(self):
        massimo_tollerato = int(self.n * 0.05)               # 75 righe su 1.501: ancora avviso
        al_limite = self._con("status", "Smarrita", massimo_tollerato)
        oltre = self._con("status", "Smarrita", massimo_tollerato + 1)
        self.assertTrue(al_limite["valido"], al_limite["errori"])
        self.assertTrue(any(a.startswith("status") for a in al_limite["avvisi"]))
        self.assertFalse(oltre["valido"])

    def test_solo_intestazione_senza_righe(self):
        esito = valida_grezzo(self.grezzo.iloc[0:0])
        self.assertFalse(esito["valido"])
        self.assertIn("non contiene righe", esito["errori"][0])

    def test_colonna_in_piu_e_solo_un_avviso(self):
        df = self.grezzo.copy()
        df["note_interne"] = "x"
        esito = valida_grezzo(df)
        self.assertTrue(esito["valido"])
        self.assertTrue(any("note_interne" in a for a in esito["avvisi"]))

    def test_obbligatorio_mancante_sotto_e_sopra_soglia(self):
        self.assertTrue(self._con("carrier", "", 10)["valido"])                    # pochi vuoti: avviso
        self.assertFalse(self._con("carrier", "", int(self.n * 0.05) + 1)["valido"])  # troppi: errore

    def test_colonna_facoltativa_vuota_non_e_un_errore(self):
        self.assertTrue(self._con("delivery_date", "", 400)["valido"])  # vuota se non ancora consegnata

    def test_formati_di_data_ammessi_e_data_impossibile(self):
        self.assertTrue(self._con("order_date", "07/01/2025", 300)["valido"])  # gg/mm/aaaa e' ammesso
        esito = self._con("order_date", "31/02/2025", 3)                        # il 31 febbraio non esiste
        self.assertTrue(esito["valido"])
        self.assertTrue(any("order_date: 3 date illeggibili" in a for a in esito["avvisi"]))

    def test_numeri_scritti_come_testo(self):
        self.assertTrue(self._con("price_eur_t", "€ 85,40", 300)["valido"])   # convertibile: nessun problema
        esito = self._con("price_eur_t", "ottanta", 5)
        self.assertTrue(any("price_eur_t: 5 valori non numerici" in a for a in esito["avvisi"]))

    def test_spazi_e_maiuscole_nelle_categorie_correggibili(self):
        esito = self._con("product_line", "  DISGELO STRADALE ", 300)
        self.assertTrue(esito["valido"])
        self.assertFalse(any(a.startswith("product_line") for a in esito["avvisi"]))

    def test_codice_spedizione_al_limite_del_formato(self):
        self.assertTrue(self._con("shipment_id", "SP-2025-99999", 1)["valido"])
        self.assertFalse(self._con("shipment_id", "SP-2025-9999", 1)["valido"])    # 4 cifre invece di 5
        self.assertFalse(self._con("shipment_id", "sp-2025-00001", 1)["valido"])   # minuscolo

    def test_file_mancante_o_non_testuale(self):
        for percorso in (Path(tempfile.mkdtemp()) / "non_esiste.csv", Path(sys.executable)):
            modulo_dataset.azzera()
            with self.assertRaises(DatasetNonValido) as e:
                modulo_dataset.prepara(percorso)
            self.assertEqual(e.exception.fase, "lettura del CSV")
        modulo_dataset.azzera()


class TestInvalidazioneCache(unittest.TestCase):
    """Quando una risposta in cache smette di valere."""

    DOMANDA = "Quante tonnellate abbiamo consegnato?"

    def setUp(self):
        modulo_dataset.azzera()
        self.dati = modulo_dataset.prepara(CSV)

    def tearDown(self):
        modulo_dataset.azzera()

    def test_scadenza_dopo_sei_ore(self):
        orologio, llm = OrologioFinto(), FintoLLM()
        agente = DataAgent(client=llm, dataset=self.dati, cache=CacheRisposte(orologio=orologio))
        agente.analizza(self.DOMANDA)
        orologio.ora += 6 * 3600 - 1
        self.assertTrue(agente.analizza(self.DOMANDA)["da_cache"])   # un secondo prima: ancora valida
        orologio.ora += 2
        self.assertFalse(agente.analizza(self.DOMANDA)["da_cache"])  # scaduta: ricalcolata
        self.assertEqual(agente.cache.stato()["scadute"], 1)

    def test_riavvio_svuota_la_cache(self):
        llm = FintoLLM()
        prima = DataAgent(client=llm, dataset=self.dati, cache=CacheRisposte())
        prima.analizza(self.DOMANDA)
        self.assertTrue(prima.analizza(self.DOMANDA)["da_cache"])
        # un riavvio crea un nuovo agente con una cache nuova (in memoria): la risposta va ricalcolata
        dopo_il_riavvio = DataAgent(client=llm, dataset=self.dati)
        self.assertFalse(dopo_il_riavvio.analizza(self.DOMANDA)["da_cache"])

    def test_svuota_esplicito(self):
        agente = DataAgent(client=FintoLLM(), dataset=self.dati, cache=CacheRisposte())
        agente.analizza(self.DOMANDA)
        agente.cache.svuota()
        self.assertFalse(agente.analizza(self.DOMANDA)["da_cache"])

    def test_lru_scarta_la_meno_usata_di_recente(self):
        cache = CacheRisposte(massimo=2)
        cache.scrivi("a", {"n": 1})
        cache.scrivi("b", {"n": 2})
        cache.leggi("a")              # "a" usata adesso: la meno recente diventa "b"
        cache.scrivi("c", {"n": 3})
        self.assertIsNone(cache.leggi("b"))
        self.assertEqual(cache.leggi("a"), {"n": 1})

    def test_grafico_cancellato_forza_il_ricalcolo(self):
        llm = FintoLLM()
        llm_codice = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.bar([1], [1])\nsalva_grafico('t')"
        agente = DataAgent(client=llm, dataset=self.dati, cache=CacheRisposte())
        with mock.patch(f"{__name__}.CODICE", llm_codice):
            prima = agente.analizza(self.DOMANDA)
            self.assertEqual(len(prima["grafici"]), 1)
            from agente_dati import CARTELLA_GRAFICI
            (CARTELLA_GRAFICI / prima["grafici"][0]).unlink()
            seconda = agente.analizza(self.DOMANDA)
        self.assertFalse(seconda["da_cache"])
        self.assertTrue((CARTELLA_GRAFICI / seconda["grafici"][0]).exists())

    def test_csv_diverso_non_riusa_le_risposte(self):
        cache, llm = CacheRisposte(), FintoLLM()
        DataAgent(client=llm, dataset=self.dati, cache=cache).analizza(self.DOMANDA)
        modulo_dataset.azzera()
        altro_csv = csv_modificato(lambda df: df.iloc[:-1])   # una riga in meno: impronta diversa
        altri_dati = modulo_dataset.prepara(altro_csv)
        self.assertNotEqual(altri_dati.impronta, self.dati.impronta)
        risposta = DataAgent(client=llm, dataset=altri_dati, cache=cache).analizza(self.DOMANDA)
        self.assertFalse(risposta["da_cache"])

    def test_analisi_non_conclusa_non_entra_in_cache(self):
        llm = FintoLLMSenzaCalcoli()
        agente = DataAgent(client=llm, dataset=self.dati, cache=CacheRisposte())
        prima = agente.analizza(self.DOMANDA)
        self.assertFalse(prima["completata"])
        self.assertFalse(agente.analizza(self.DOMANDA)["da_cache"])
        self.assertEqual(agente.cache.stato()["voci"], 0)

    def test_domande_diverse_non_si_confondono(self):
        agente = DataAgent(client=FintoLLM(), dataset=self.dati, cache=CacheRisposte())
        agente.analizza(self.DOMANDA)
        self.assertTrue(agente.analizza("  QUANTE tonnellate abbiamo consegnato  ")["da_cache"])  # stessa domanda
        self.assertFalse(agente.analizza("Quante tonnellate abbiamo ordinato?")["da_cache"])      # domanda diversa


class TestApi(unittest.TestCase):
    """Il confine HTTP del data agent: validazione delle richieste e ID di correlazione."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("OPENAI_API_KEY", "chiave-finta-per-i-test")
        modulo_dataset.azzera()
        from fastapi.testclient import TestClient
        import app as modulo_app
        cls.modulo_app = modulo_app
        cls.client = TestClient(modulo_app.app).__enter__()  # avvia il servizio (prepara il dataset)
        modulo_app.agente.analizza = lambda domanda: {"sintesi": domanda, "grafici": [], "passi": [], "da_cache": False}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        modulo_dataset.azzera()

    def test_lunghezza_della_domanda_ai_limiti(self):
        self.assertEqual(self.client.post("/analyze", json={"domanda": "ab"}).status_code, 422)
        self.assertEqual(self.client.post("/analyze", json={"domanda": "abc"}).status_code, 200)
        self.assertEqual(self.client.post("/analyze", json={"domanda": "x" * 2000}).status_code, 200)
        self.assertEqual(self.client.post("/analyze", json={"domanda": "x" * 2001}).status_code, 422)

    def test_richieste_malformate(self):
        self.assertEqual(self.client.post("/analyze", json={}).status_code, 422)
        self.assertEqual(self.client.post("/analyze", json={"domanda": 42}).status_code, 422)
        self.assertEqual(self.client.post("/analyze", content=b"non json").status_code, 422)

    def test_request_id_propagato_o_rigenerato(self):
        valido = self.client.get("/health", headers={"X-Request-Id": "richiesta-di-prova-1"})
        self.assertEqual(valido.headers["X-Request-Id"], "richiesta-di-prova-1")
        sospetto = self.client.get("/health", headers={"X-Request-Id": "a b<script>"})
        self.assertNotEqual(sospetto.headers["X-Request-Id"], "a b<script>")  # formato non valido: ID nuovo

    def test_nomi_dei_grafici(self):
        self.assertEqual(self.client.get("/charts/grafico_123.png").status_code, 400)     # nome fuori formato
        self.assertEqual(self.client.get("/charts/app.py").status_code, 400)
        self.assertNotEqual(self.client.get("/charts/..%2F..%2Fapp.py").status_code, 200)  # niente percorsi
        self.assertEqual(self.client.get("/charts/grafico_0123456789.png").status_code, 404)  # formato ok, assente


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
