"""
Pandas agent di SupplyBot.

Riceve una domanda in linguaggio naturale sui dati delle spedizioni e segue
un ciclo ReAct (ragiona -> agisce -> osserva) con lo strumento esegui_python,
che esegue codice pandas/matplotlib scritto dall'LLM nella sandbox (sandbox.py)
e restituisce l'output o l'errore.

L'agente parte dal dataframe GIA' PRONTO: lettura, validazione dello schema,
diagnosi e pulizia del CSV avvengono una sola volta all'avvio (dataset.py).
Il riepilogo della pulizia entra nel prompt, cosi' l'agente puo' citarlo nella
"Nota sui dati", e i passi di diagnosi e pulizia compaiono in ogni risposta.
Le domande gia' viste si servono dalla cache (cache.py).

Se il codice va in errore, l'errore torna all'LLM come osservazione e l'LLM
lo corregge (autocorrezione). I numeri della risposta finale vengono SOLO
dall'output del codice eseguito, mai dalla "memoria" del modello.
"""

import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

import dataset as modulo_dataset
from cache import CacheRisposte, normalizza
from sandbox import esegui

RADICE = Path(__file__).resolve().parent.parent
load_dotenv(RADICE / ".env")

CARTELLA_GRAFICI = Path(__file__).resolve().parent / "charts"
MAX_PASSI = 8
log = logging.getLogger("supplybot.agente")

PROMPT_DI_SISTEMA = """Sei il Data Agent di SupplyBot, l'analista dati del reparto Supply Chain di Atisale
(produttore di sale marino con due saline: Margherita di Savoia in Puglia e Sant'Antioco in Sardegna).
Rispondi a domande su un dataset di spedizioni ai clienti (gennaio 2025 - settembre 2026; dati simulati).

COME LAVORI
1. Il dataset e' gia' stato validato, diagnosticato e pulito all'avvio del servizio (vedi QUALITA' DEI DATI
   in fondo): non devi pulirlo di nuovo.
2. Usa `esegui_python` per calcolare cio' che serve. Nel codice hai gia' disponibili:
   `df` (il dataframe PULITO), `pd`, `np`, `plt` e la funzione `salva_grafico(titolo)`.
   Stampa con print() i risultati che ti servono: vedi solo cio' che stampi.
3. Se il codice va in errore, leggi l'errore, correggi e riprova. Non ripetere lo stesso codice.
4. Crea SEMPRE un grafico quando la risposta confronta piu' valori (mesi, vettori, siti, prodotti) o mostra
   un andamento, anche se la domanda non lo chiede esplicitamente. Un solo grafico chiaro:
   titolo che dice il risultato, assi con etichette in italiano e unita' di misura (t, EUR, %),
   date leggibili. Usa i colori predefiniti; per distinguere sopra/sotto una soglia usa blu e arancione,
   mai rosso e verde (illeggibili per chi non distingue i colori). Chiama salva_grafico(titolo) alla fine del codice del grafico.
5. Quando hai i numeri, rispondi in italiano senza chiamare altri strumenti.

COLONNE DI df
shipment_id, order_date (datetime), site (Margherita di Savoia | Sant'Antioco),
product_line (Alimentare, Industriale, Addolcimento acqua, Disgelo stradale, Zootecnia, Piscine),
channel (GDO, Industria, Enti pubblici, Horeca, Export), customer_region, priority (Standard | Urgente),
qty_t (tonnellate ordinate), qty_delivered_t (tonnellate consegnate), price_eur_t (EUR/t),
transport_mode (Camion | Treno | Nave + camion), carrier (vettore), freight_cost_eur,
requested_date, delivery_date (datetime), status (Consegnata | In transito | Annullata), complaint (Si | No),
e colonne derivate: revenue_eur, on_time, in_full, otif (1 = OTIF, 0 = no, NaN = non valutabile), delay_days.
OTIF = consegnata entro la data richiesta E con quantita' completa; si calcola solo sulle righe con otif non nullo.
Per "stagione invernale" si intende novembre-febbraio (definizione del manuale aziendale), anche se la domanda
indica un periodo piu' breve senza motivarlo: in quel caso usa novembre-febbraio e segnalalo nella risposta. Per i periodi usa order_date se non indicato diversamente.
VOLUMI: per tonnellate spedite o consegnate usa qty_delivered_t delle sole spedizioni con status "Consegnata";
usa qty_t solo se la domanda parla esplicitamente di quantita' ordinate. Per fatturato e costi escludi le "Annullata".
PUNTUALITA': "puntuale" = on_time; OTIF = on_time e in_full insieme. Dichiara sempre quale delle due hai usato.

REGOLE
- Mai inventare o stimare numeri: ogni numero della risposta deve comparire nell'output del codice.
- Se la domanda contiene una soglia o una regola (per esempio presa dal manuale aziendale), confronta i dati con essa.
- Se un dato richiesto non esiste nel dataset, dillo chiaramente.

FORMATO DELLA RISPOSTA FINALE
Una sintesi per chi deve decidere, 3-6 frasi: prima il risultato principale con i numeri chiave e le unita',
poi cosa significa per il business. Se la pulizia dei dati ha inciso sul risultato, aggiungi una riga
"Nota sui dati:" con le correzioni rilevanti. Non citare nomi di file o codice.

QUALITA' DEI DATI (diagnosi e pulizia eseguite all'avvio)
"""

STRUMENTI = [
    {"type": "function", "function": {
        "name": "esegui_python",
        "description": "Esegue codice Python (pandas, numpy, matplotlib) sul dataframe pulito `df`. "
                       "Restituisce cio' che il codice stampa, oppure l'errore.",
        "parameters": {"type": "object", "properties": {
            "scopo": {"type": "string", "description": "In una frase: cosa calcola questo codice e perche'."},
            "codice": {"type": "string", "description": "Codice Python da eseguire."}},
            "required": ["scopo", "codice"]}}},
]


class DataAgent:
    def __init__(self, modello=None, verbose=False, client=None, dataset=None, cache=None):
        self.client = client or OpenAI(timeout=90, max_retries=1)  # legge OPENAI_API_KEY (e OPENAI_BASE_URL) dall'ambiente
        self.modello = modello or os.getenv("OPENAI_MODEL_DATA") or os.getenv("OPENAI_MODEL", "gpt-6-luna")
        self.verbose = verbose
        self._reasoning_none = os.getenv("OPENAI_REASONING_EFFORT") == "none"
        # dataset preparato una volta (lettura, validazione, diagnosi, pulizia): DatasetNonValido se il CSV non va bene
        self.dataset = dataset or modulo_dataset.prepara()
        self.prompt = PROMPT_DI_SISTEMA + self.dataset.riepilogo_per_agente()
        self.cache = cache if cache is not None else CacheRisposte(
            massimo=int(os.getenv("CACHE_MAX_RISPOSTE", "200")),
            durata_secondi=int(os.getenv("CACHE_DURATA_SECONDI", str(6 * 3600))))
        CARTELLA_GRAFICI.mkdir(exist_ok=True)

    def _chiama_modello(self, **parametri):
        """
        Alcuni modelli recenti rifiutano gli strumenti su /chat/completions se il ragionamento interno e'
        attivo e chiedono reasoning_effort="none"; altri non accettano quel valore. Si prova senza e,
        se arriva quell'errore, si riprova con "none" ricordandolo per le chiamate successive.
        """
        if self._reasoning_none:
            return self.client.chat.completions.create(**parametri, reasoning_effort="none")
        try:
            return self.client.chat.completions.create(**parametri)
        except BadRequestError as e:
            if "reasoning_effort" not in str(e):
                raise
            self._log("modello_reasoning_none", "strumenti non ammessi con il ragionamento attivo: riprovo con reasoning_effort=none")
            self._reasoning_none = True
            return self.client.chat.completions.create(**parametri, reasoning_effort="none")

    def _log(self, evento, testo="", **dati):
        """Una riga JSON per evento (registro.py); con verbose anche il testo leggibile, per il notebook."""
        log.info(evento, extra={"dati": {**dati, **({"testo": testo[:2000]} if testo else {})}})
        if self.verbose:
            print(f"\n[{evento.upper()}] {testo}")

    def analizza(self, domanda: str) -> dict:
        chiave = f"{self.dataset.impronta}|{normalizza(domanda)}"
        salvata = self.cache.leggi(chiave)
        # una risposta in cache vale solo se i suoi grafici esistono ancora sul disco
        if salvata and all((CARTELLA_GRAFICI / g).exists() for g in salvata["grafici"]):
            self._log("risposta_da_cache", domanda)
            return {**salvata, "da_cache": True}
        if salvata:
            self.cache.scarta(chiave)

        inizio = time.perf_counter()
        risultato = self._analizza(domanda)
        risultato["durata_ms"] = round((time.perf_counter() - inizio) * 1000)
        if risultato.get("completata"):
            self.cache.scrivi(chiave, risultato)
        return {**risultato, "da_cache": False}

    def _analizza(self, domanda: str) -> dict:
        percorso_df = self.dataset.percorso_df
        report = self.dataset.report
        messaggi = [{"role": "system", "content": self.prompt}, {"role": "user", "content": domanda}]
        passi, grafici = self.dataset.passi_preparazione(), []
        analisi_eseguite, sollecito_fatto = 0, False

        self._log("domanda", domanda)
        for _ in range(MAX_PASSI):
            risposta = self._chiama_modello(model=self.modello, messages=messaggi, tools=STRUMENTI)
            msg = risposta.choices[0].message
            messaggi.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                # Guardia anti-allucinazione: nessuna risposta senza aver eseguito almeno un'analisi
                if analisi_eseguite == 0 and not sollecito_fatto:
                    sollecito_fatto = True
                    self._log("controllo_anti_allucinazione", "risposta senza analisi eseguite: chiedo di calcolare i numeri")
                    messaggi.append({"role": "user", "content": "Non hai ancora eseguito nessuna analisi: "
                                     "i numeri devono venire dal codice. Esegui il calcolo con esegui_python."})
                    continue
                self._log("risposta", msg.content or "", analisi=analisi_eseguite, grafici=len(grafici))
                return {"sintesi": msg.content, "grafici": grafici, "passi": passi, "report_pulizia": report,
                        "modello": self.modello, "completata": analisi_eseguite > 0}

            for chiamata in msg.tool_calls:
                nome = chiamata.function.name
                try:
                    argomenti = json.loads(chiamata.function.arguments or "{}")
                except json.JSONDecodeError:
                    argomenti = {}

                if nome == "esegui_python":
                    codice, scopo = argomenti.get("codice", ""), argomenti.get("scopo", "")
                    self._log("pensiero", scopo)
                    self._log("azione", "esegui_python\n" + codice)
                    t0 = time.perf_counter()
                    esito = esegui(codice, percorso_df, str(CARTELLA_GRAFICI))
                    self._log("codice_eseguito", ok=esito["ok"], durata_ms=round((time.perf_counter() - t0) * 1000))
                    if esito["ok"]:
                        analisi_eseguite += 1
                        grafici.extend(esito["grafici"])
                        osservazione = esito["output"] or "(il codice non ha stampato nulla: usa print())"
                    else:
                        osservazione = f"ERRORE:\n{esito['errore']}\nCorreggi il codice e riprova."
                    passi.append({"tipo": "codice", "scopo": scopo, "codice": codice, "ok": esito["ok"],
                                  "output": esito["output"][:1500], "errore": esito["errore"]})
                    self._log("osservazione", osservazione[:1500])
                else:
                    osservazione = f"Strumento sconosciuto: {nome}"

                messaggi.append({"role": "tool", "tool_call_id": chiamata.id, "content": osservazione})

        self._log("passi_esauriti", domanda)
        return {"sintesi": "Non sono riuscito a completare l'analisi entro il numero massimo di passaggi. "
                           "Prova a riformulare la domanda in modo piu' specifico.",
                "grafici": grafici, "passi": passi, "report_pulizia": report, "modello": self.modello,
                "completata": False}
