// Il "cervello" di SupplyBot: l'LLM decide autonomamente, con il function calling,
// se consultare il manuale (RAG) o delegare l'analisi al data agent Python.
// Ciclo ReAct: ragiona -> chiama uno strumento -> osserva il risultato -> decide se basta.
import OpenAI from "openai";
import { config } from "./config.js";
import { cercaManuale } from "./rag.js";
import { analizzaDati } from "./dataAgent.js";
import { CacheLRU, normalizza } from "./cache.js";
import { log } from "./logger.js";

const openai = new OpenAI({ timeout: config.timeoutOpenAiMs, maxRetries: 1 });

const PROMPT_DI_SISTEMA = `Sei SupplyBot, l'assistente del reparto Supply Chain di Atisale (sale marino, saline di
Margherita di Savoia e Sant'Antioco). Aiuti l'ufficio ordini, la logistica e il Supply Chain Manager.
Dati e manuale sono simulati a scopo didattico.

HAI DUE STRUMENTI E DECIDI TU QUALE USARE
- cerca_manuale: per regole, procedure, priorita', soglie, obiettivi, responsabilita', FAQ.
  Esempi: "entro quanto va servita un'urgenza neve?", "come si gestisce un reclamo?".
- analizza_dati: per numeri, KPI, andamenti, confronti, classifiche e grafici sul dataset delle spedizioni.
  Esempi: "volumi di disgelo mese per mese", "OTIF per vettore nel 2026".
- Domande IBRIDE (confrontare i dati con una regola aziendale, es. "quali vettori sono sotto l'obiettivo?",
  "quanta scorta serve?"): PRIMA cerca_manuale per trovare la regola esatta, POI analizza_dati passando
  nella domanda la soglia o la regola trovata, con i numeri esatti.
- Saluti o domande fuori ambito: rispondi direttamente, senza strumenti, e spiega cosa sai fare.

REGOLE
- Non inventare regole o numeri: ogni regola viene dal manuale, ogni numero dal data agent.
- Se il manuale non contiene la risposta, dillo chiaramente. Se cerca_manuale risponde NESSUN RISULTATO PERTINENTE,
  non citare capitoli: spiega che il manuale non tratta l'argomento.
- Le domande per analizza_dati devono essere COMPLETE e AUTONOME: il data agent non vede la conversazione,
  quindi includi periodo, filtri e soglie anche se l'utente li ha detti nei messaggi precedenti
  (es. "E per Sant'Antioco?" dopo una domanda sui vettori -> "OTIF 2026 dei vettori che spediscono da Sant'Antioco...").
- Quando riformuli per analizza_dati NON aggiungere definizioni tue: usa le parole dell'utente e i termini aziendali
  qui sotto. Se la domanda riguarda andamenti, confronti tra mesi, vettori, siti o prodotti, chiedi esplicitamente
  anche un grafico.

TERMINI AZIENDALI (dal manuale)
- Stagione invernale = da novembre a febbraio compresi (es. inverno 2025-26 = novembre 2025 - febbraio 2026).
- OTIF = spedizione consegnata entro la data richiesta e con la quantita' completa; obiettivo 95%.
- Il dataset copre le spedizioni da gennaio 2025 a settembre 2026.
- Volumi = tonnellate consegnate (spedizioni con stato Consegnata), salvo che l'utente chieda le quantita' ordinate.

FORMATO DELLA RISPOSTA
- Italiano, chiaro, adatto a un manager: prima la risposta, poi i dettagli essenziali.
- Cita le regole con il riferimento: (Manuale, cap. 5.3).
- Se e' stato creato un grafico, non descriverlo punto per punto: l'interfaccia lo mostra sotto la risposta.
- Usa grassetto ed elenchi solo se aiutano la lettura.`;

const STRUMENTI = [
  {
    type: "function",
    function: {
      name: "cerca_manuale",
      description: "Cerca nel Manuale Operativo Supply Chain (procedure, regole, soglie, FAQ). Restituisce i paragrafi piu' pertinenti con il numero di capitolo.",
      parameters: {
        type: "object",
        properties: { query: { type: "string", description: "Cosa cercare, formulato come domanda o parole chiave." } },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "analizza_dati",
      description: "Delega al data agent Python l'analisi del dataset delle spedizioni (gen 2025 - set 2026): pulizia dei dati, calcoli, trend, grafici. Restituisce una sintesi numerica.",
      parameters: {
        type: "object",
        properties: { domanda: { type: "string", description: "Domanda completa e autonoma, con periodo, filtri ed eventuali soglie." } },
        required: ["domanda"],
      },
    },
  },
];

// Alcuni modelli recenti (es. famiglia gpt-5.6 / gpt-6) rifiutano gli strumenti su /chat/completions se il
// ragionamento interno e' attivo: chiedono reasoning_effort "none". Altri modelli (es. gpt-5-mini) non accettano
// quel valore. Soluzione: si prova senza; se OpenAI risponde con quell'errore, si riprova con "none"
// e lo si ricorda per le chiamate successive. Cosi' il codice funziona con entrambi i tipi di modello.
let serveReasoningNone = process.env.OPENAI_REASONING_EFFORT === "none";

async function chiamaModello(parametri) {
  const extra = serveReasoningNone ? { reasoning_effort: "none" } : {};
  try {
    return await openai.chat.completions.create({ ...parametri, ...extra });
  } catch (errore) {
    if (!serveReasoningNone && errore.status === 400 && /reasoning_effort/i.test(errore.message)) {
      log.warn("modello_reasoning_none", { motivo: "strumenti non ammessi con il ragionamento attivo" });
      serveReasoningNone = true;
      return openai.chat.completions.create({ ...parametri, reasoning_effort: "none" });
    }
    throw errore;
  }
}

// Memoria delle conversazioni: per ogni sessione si tengono gli ultimi scambi utente/assistente.
const sessioni = new Map();

// Cache delle risposte complete, SOLO per la PRIMA domanda di una conversazione: senza storia la risposta
// dipende solo dalla domanda. Regole (verificate da tests/cache.test.js):
//   - entra in cache: la risposta alla prima domanda, se nessuno strumento ha dato errore e se il data
//     agent, quando usato, ha concluso l'analisi;
//   - non entra mai: le domande successive ("E per Sant'Antioco?" significa cose diverse in conversazioni
//     diverse), le risposte con errori, le analisi non concluse;
//   - si invalida: al riavvio del servizio (e' in memoria), dopo 6 ore, quando supera 200 voci (LRU)
//     o con svuotaCacheRisposte(). Vedi cache.js e la sezione "Cache" del README.
const cacheRisposte = new CacheLRU({ massimo: 200 });

export function statoCacheRisposte() {
  return cacheRisposte.stato();
}

/** Svuota la cache delle risposte complete (stesso effetto di un riavvio). */
export function svuotaCacheRisposte() {
  cacheRisposte.svuota();
}

function ricordaScambio(idSessione, storia, messaggioUtente, risposta) {
  const nuovaStoria = [...storia, { role: "user", content: messaggioUtente }, { role: "assistant", content: risposta }];
  sessioni.delete(idSessione); // reinserita in fondo: la Map resta ordinata dalla meno recente
  sessioni.set(idSessione, nuovaStoria.slice(-2 * config.maxTurniMemoria));
  // app pubblica: si tengono al massimo 500 conversazioni, le piu' vecchie si scartano
  while (sessioni.size > 500) sessioni.delete(sessioni.keys().next().value);
}

export function azzeraSessione(id) {
  sessioni.delete(id);
}

async function eseguiStrumento(nome, argomenti) {
  if (nome === "cerca_manuale") {
    const { pertinenti, scartati, soglia, daCache } = await cercaManuale(argomenti.query);
    const perLLM = pertinenti.length
      ? pertinenti.map((r) => `[Manuale, cap. ${r.sezione}] ${r.testo}`).join("\n\n---\n\n")
      : `NESSUN RISULTATO PERTINENTE: nessun paragrafo del manuale raggiunge la somiglianza minima (${soglia}) ` +
        "con questa ricerca. Non citare il manuale per questa domanda.";
    return {
      perLLM,
      traccia: {
        strumento: "cerca_manuale",
        input: argomenti.query,
        fonti: pertinenti.map((r) => ({ sezione: r.sezione, titolo: r.titolo, somiglianza: r.somiglianza })),
        scartate: scartati.map((r) => ({ sezione: r.sezione, titolo: r.titolo, somiglianza: r.somiglianza })),
        soglia,
        daCache,
      },
      grafici: [],
    };
  }
  if (nome === "analizza_dati") {
    const r = await analizzaDati(argomenti.domanda);
    const codici = r.passi.filter((p) => p.tipo === "codice");
    return {
      perLLM: r.sintesi,
      traccia: {
        strumento: "analizza_dati",
        input: argomenti.domanda,
        passi: r.passi,
        autocorrezioni: codici.filter((p) => !p.ok).length,
        modello: r.modello,
        daCache: Boolean(r.da_cache),
        completata: r.completata !== false, // false: il data agent non e' arrivato a un risultato
      },
      grafici: r.grafici,
    };
  }
  throw new Error(`Strumento sconosciuto: ${nome}`);
}

export async function chat(idSessione, messaggioUtente) {
  const storia = sessioni.get(idSessione) ?? [];
  const chiaveCache = storia.length === 0 ? normalizza(messaggioUtente) : null;
  const salvata = chiaveCache ? cacheRisposte.leggi(chiaveCache) : undefined;
  if (salvata) {
    log.info("risposta_da_cache", { domanda: messaggioUtente });
    ricordaScambio(idSessione, storia, messaggioUtente, salvata.risposta);
    return { ...salvata, daCache: true };
  }
  const messaggi = [
    { role: "system", content: PROMPT_DI_SISTEMA },
    ...storia,
    { role: "user", content: messaggioUtente },
  ];
  /** @type {Array<Record<string, any>>} passi mostrati nel pannello "Come ho risposto" */
  const traccia = [];
  /** @type {string[]} */
  const grafici = [];

  for (let passo = 0; passo < config.maxPassi; passo++) {
    const risposta = await chiamaModello({ model: config.modello, messages: messaggi, tools: STRUMENTI });
    const msg = risposta.choices[0].message;
    messaggi.push(msg);

    if (!msg.tool_calls?.length) {
      const testo = msg.content ?? "";
      // in memoria solo domanda e risposta finale: la conversazione resta leggera
      ricordaScambio(idSessione, storia, messaggioUtente, testo);
      const esito = { risposta: testo, grafici, traccia };
      // in cache solo risposte riuscite: un errore o un'analisi non conclusa non devono ripetersi per ore
      if (chiaveCache && !traccia.some((t) => t.errore || t.completata === false)) cacheRisposte.scrivi(chiaveCache, esito);
      log.info("risposta", { strumenti: traccia.map((t) => t.strumento), grafici: grafici.length, passi: passo + 1 });
      return { ...esito, daCache: false };
    }

    for (const chiamata of msg.tool_calls) {
      if (chiamata.type !== "function") {
        // usiamo solo strumenti di tipo funzione; ogni chiamata deve comunque ricevere una risposta
        messaggi.push({ role: "tool", tool_call_id: chiamata.id, content: "Strumento non supportato." });
        continue;
      }
      const nome = chiamata.function.name;
      let argomenti = {};
      try {
        argomenti = JSON.parse(chiamata.function.arguments || "{}");
      } catch {
        /* argomenti non validi: lo strumento restituira' un errore all'LLM */
      }
      log.info("decisione", { strumento: nome, argomenti });
      let contenuto;
      try {
        const esito = await eseguiStrumento(nome, argomenti);
        contenuto = esito.perLLM;
        traccia.push(esito.traccia);
        grafici.push(...esito.grafici);
      } catch (errore) {
        // l'errore torna all'LLM come osservazione: puo' riprovare o spiegare il problema all'utente
        log.error("errore_strumento", { strumento: nome, messaggio: errore.message });
        contenuto = `ERRORE durante ${nome}: ${errore.message}`;
        traccia.push({ strumento: nome, input: argomenti.query ?? argomenti.domanda, errore: errore.message });
      }
      messaggi.push({ role: "tool", tool_call_id: chiamata.id, content: contenuto });
    }
  }
  log.warn("passi_esauriti", { strumenti: traccia.map((t) => t.strumento) });
  return {
    risposta: "Non sono riuscito a completare la richiesta in un numero ragionevole di passaggi. Prova a riformularla.",
    grafici,
    traccia,
    daCache: false,
  };
}
