// Test dell'invalidazione delle cache del back end (node:test, nessuna dipendenza in piu').
//
// L'orchestratore viene provato davvero, ma senza OpenAI e senza data agent: due piccoli server HTTP
// locali li imitano. Il finto LLM conta le chiamate, cosi' si vede se una risposta e' stata ricalcolata
// o servita dalla cache. Uso: cd backend && npm test
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

let chiamateLLM = 0;
let chiamateDataAgent = 0;

/** Avvia un server HTTP su una porta libera e restituisce { server, url } */
async function avvia(gestore) {
  const server = http.createServer(async (req, res) => {
    let corpo = "";
    for await (const pezzo of req) corpo += pezzo;
    const { stato = 200, json } = await gestore(req, corpo ? JSON.parse(corpo) : {});
    res.writeHead(stato, { "Content-Type": "application/json" });
    res.end(JSON.stringify(json));
  });
  await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
  return { server, url: `http://127.0.0.1:${/** @type {any} */ (server.address()).port}` };
}

/** Finto LLM: "dati ..." -> chiama analizza_dati; poi (o per tutto il resto) risponde a parole. */
function rispostaLLM(corpo) {
  chiamateLLM += 1;
  const messaggi = corpo.messages;
  const ultimo = messaggi.at(-1);
  const domanda = messaggi.filter((m) => m.role === "user").at(-1).content;
  const messaggio =
    ultimo.role === "user" && domanda.startsWith("dati")
      ? {
          role: "assistant",
          content: null,
          tool_calls: [{ id: `c${chiamateLLM}`, type: "function", function: { name: "analizza_dati", arguments: JSON.stringify({ domanda }) } }],
        }
      : { role: "assistant", content: `risposta numero ${chiamateLLM} a: ${domanda}` };
  return {
    json: { id: "x", object: "chat.completion", created: 0, model: "finto", choices: [{ index: 0, message: messaggio, finish_reason: "stop" }] },
  };
}

/** Finto data agent: "errore" -> HTTP 500; "incompleta" -> analisi non conclusa; altrimenti conclusa. */
function rispostaDataAgent(req, corpo) {
  if (req.url === "/health") return { json: { stato: "ok" } };
  chiamateDataAgent += 1;
  if (corpo.domanda.includes("errore")) return { stato: 500, json: { detail: "guasto simulato" } };
  const completata = !corpo.domanda.includes("incompleta");
  return { json: { sintesi: "ok", grafici: [], passi: [], modello: "finto", da_cache: false, completata } };
}

let servizi = [];
/** @type {typeof import("../backend/src/orchestrator.js")} */
let orchestratore;

before(async () => {
  const llm = await avvia(async (_req, corpo) => rispostaLLM(corpo));
  const dataAgent = await avvia(async (req, corpo) => rispostaDataAgent(req, corpo));
  servizi = [llm.server, dataAgent.server];
  // la configurazione si legge all'import: le variabili vanno impostate prima
  process.env.OPENAI_API_KEY = "chiave-finta-per-i-test";
  process.env.OPENAI_BASE_URL = `${llm.url}/v1`;
  process.env.DATA_AGENT_URL = dataAgent.url;
  orchestratore = await import("../backend/src/orchestrator.js");
});

after(() => servizi.forEach((s) => s.close()));

let n = 0;
const nuovaSessione = () => `sessione-test-${++n}`;

test("la prima domanda di una conversazione entra in cache e si riusa in un'altra conversazione", async () => {
  const prima = await orchestratore.chat(nuovaSessione(), "Entro quanto va servita un'urgenza neve?");
  const chiamate = chiamateLLM;
  const seconda = await orchestratore.chat(nuovaSessione(), "Entro quanto va servita un'urgenza neve?");
  assert.equal(prima.daCache, false);
  assert.equal(seconda.daCache, true);
  assert.equal(seconda.risposta, prima.risposta);
  assert.equal(chiamateLLM, chiamate, "nessuna nuova chiamata all'LLM");
});

test("la chiave ignora maiuscole, spazi e punteggiatura finale", async () => {
  await orchestratore.chat(nuovaSessione(), "Chi approva i resi?");
  const variante = await orchestratore.chat(nuovaSessione(), "  CHI approva   i resi  ");
  assert.equal(variante.daCache, true);
});

test("le domande successive di una conversazione non entrano in cache", async () => {
  const sessione = nuovaSessione();
  await orchestratore.chat(sessione, "Parliamo dei vettori");
  const seguito = await orchestratore.chat(sessione, "E per Sant'Antioco?");
  assert.equal(seguito.daCache, false);
  // la stessa frase come seconda domanda in un'altra conversazione viene ricalcolata
  const altra = nuovaSessione();
  await orchestratore.chat(altra, "Parliamo dei siti");
  const chiamate = chiamateLLM;
  const ripetuta = await orchestratore.chat(altra, "E per Sant'Antioco?");
  assert.equal(ripetuta.daCache, false);
  assert.ok(chiamateLLM > chiamate);
  assert.equal(orchestratore.statoCacheRisposte().voci >= 1, true);
});

test("una risposta con un errore di uno strumento non entra in cache", async () => {
  const prima = await orchestratore.chat(nuovaSessione(), "dati con errore del servizio");
  assert.ok(prima.traccia.some((t) => t.errore), "lo strumento ha restituito un errore");
  const seconda = await orchestratore.chat(nuovaSessione(), "dati con errore del servizio");
  assert.equal(seconda.daCache, false);
});

test("un'analisi non conclusa del data agent non entra in cache", async () => {
  const prima = await orchestratore.chat(nuovaSessione(), "dati analisi incompleta");
  assert.equal(prima.traccia[0].completata, false);
  const chiamate = chiamateDataAgent;
  const seconda = await orchestratore.chat(nuovaSessione(), "dati analisi incompleta");
  assert.equal(seconda.daCache, false);
  assert.equal(chiamateDataAgent, chiamate + 1, "il data agent viene interrogato di nuovo");
});

test("un'analisi conclusa entra in cache e non richiama il data agent", async () => {
  await orchestratore.chat(nuovaSessione(), "dati volumi invernali");
  const chiamate = chiamateDataAgent;
  const seconda = await orchestratore.chat(nuovaSessione(), "dati volumi invernali");
  assert.equal(seconda.daCache, true);
  assert.equal(chiamateDataAgent, chiamate);
});

test("svuotare la cache equivale a un riavvio: la risposta si ricalcola", async () => {
  await orchestratore.chat(nuovaSessione(), "Qual e' l'obiettivo OTIF?");
  orchestratore.svuotaCacheRisposte();
  const dopo = await orchestratore.chat(nuovaSessione(), "Qual e' l'obiettivo OTIF?");
  assert.equal(dopo.daCache, false);
});

test("un riavvio vero (modulo ricaricato da zero) parte con la cache vuota", async () => {
  await orchestratore.chat(nuovaSessione(), "Come si gestisce un reclamo?");
  // importare di nuovo il modulo con un indirizzo diverso crea un'istanza nuova, come un processo riavviato
  const riavviato = await import("../backend/src/orchestrator.js?riavvio=1");
  assert.equal(riavviato.statoCacheRisposte().voci, 0);
  const risposta = await riavviato.chat(nuovaSessione(), "Come si gestisce un reclamo?");
  assert.equal(risposta.daCache, false);
});

test("una nuova conversazione dopo 'Nuova conversazione' torna a usare la cache", async () => {
  const sessione = nuovaSessione();
  await orchestratore.chat(sessione, "Quanta scorta di disgelo serve?");
  orchestratore.azzeraSessione(sessione); // il pulsante "Nuova conversazione"
  const dopoReset = await orchestratore.chat(sessione, "Quanta scorta di disgelo serve?");
  assert.equal(dopoReset.daCache, true, "senza storia e' di nuovo una prima domanda");
});
