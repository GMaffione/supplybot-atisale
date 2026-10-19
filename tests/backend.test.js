// Test unitari del back end Node (node:test, nessuna dipendenza in piu').
// Uso: cd backend && npm test
import { test } from "node:test";
import assert from "node:assert/strict";

process.env.OPENAI_API_KEY ??= "chiave-finta-per-i-test"; // config.js la richiede; qui non si chiama OpenAI

const { dividiInFrammenti, dividiInBlocchi, filtraPerSoglia, estraiTesto } = await import("../backend/src/rag.js");
const { schemaChat, valida } = await import("../backend/src/validazione.js");
const { CacheLRU, normalizza } = await import("../backend/src/cache.js");
const { config } = await import("../backend/src/config.js");

test("RAG: il manuale reale si divide in 29 frammenti, con una FAQ per domanda", async () => {
  const frammenti = dividiInFrammenti(await estraiTesto(config.manualePdf));
  assert.equal(frammenti.length, 29);
  assert.equal(frammenti.filter((f) => f.metadati.titolo.startsWith("FAQ:")).length, 7);
});

test("RAG: il capitolo FAQ si riconosce dal titolo, non dal numero 10", () => {
  const testo = [
    "1. Introduzione",
    "Questo documento descrive le regole di base del magazzino centrale.",
    "2. Spedizioni",
    "Le spedizioni partono ogni giorno lavorativo entro le ore 16.",
    "3. Domande frequenti",
    "Posso spedire di sabato?",
    "No, il sabato il magazzino e' chiuso e non si caricano camion.",
    "Chi approva un reso?",
    "Il responsabile qualita', entro cinque giorni lavorativi dalla richiesta.",
  ].join("\n");
  const titoli = dividiInFrammenti(testo).map((f) => f.metadati.titolo);
  assert.deepEqual(titoli, ["Introduzione", "Spedizioni", "FAQ: Posso spedire di sabato?", "FAQ: Chi approva un reso?"]);
});

test("RAG: senza titoli numerati si passa ai blocchi a lunghezza fissa con sovrapposizione", () => {
  const righe = Array.from({ length: 80 }, (_, i) => `Riga ${i} di un documento senza struttura, con testo qualsiasi.`);
  const frammenti = dividiInFrammenti(righe.join("\n"));
  assert.ok(frammenti.length >= 3);
  assert.ok(frammenti.every((f) => f.testo.length <= 1600));
  const blocchi = dividiInBlocchi(righe.join("\n"));
  const ultimaRigaPrimo = blocchi[0].split("\n").at(-1);
  assert.ok(blocchi[1].includes(ultimaRigaPrimo), "il blocco successivo riparte dalle ultime righe del precedente");
});

test("RAG: i frammenti sotto la soglia di somiglianza vengono scartati", () => {
  const risultati = [
    { sezione: "5.3", somiglianza: 0.52 },
    { sezione: "6.3", somiglianza: 0.31 },
    { sezione: "9", somiglianza: 0.12 },
  ];
  const esito = filtraPerSoglia(risultati, 0.3);
  assert.deepEqual(esito.pertinenti.map((r) => r.sezione), ["5.3", "6.3"]);
  assert.deepEqual(esito.scartati.map((r) => r.sezione), ["9"]);
  assert.equal(filtraPerSoglia([{ sezione: "1", somiglianza: 0.1 }], 0.3).pertinenti.length, 0);
});

test("zod: /api/chat accetta solo sessionId e message validi", () => {
  assert.deepEqual(valida(schemaChat, { sessionId: "sessione-123", message: "  ciao  " }), {
    ok: true,
    dati: { sessionId: "sessione-123", message: "ciao" },
  });
  assert.equal(valida(schemaChat, { sessionId: "x", message: "ciao" }).ok, false);
  assert.equal(valida(schemaChat, { sessionId: "sessione-123", message: "   " }).ok, false);
  assert.equal(valida(schemaChat, { sessionId: "sessione-123", message: 42 }).ok, false);
  assert.equal(valida(schemaChat, { sessionId: "sessione-123", message: "x".repeat(config.maxCaratteriDomanda + 1) }).ok, false);
  const extra = valida(schemaChat, { sessionId: "sessione-123", message: "ciao", admin: true });
  assert.equal(extra.ok, false);
  assert.match(/** @type {any} */ (extra).errore, /admin/);
  assert.equal(valida(schemaChat, null).ok, false);
});

test("cache: LRU con scadenza e chiavi normalizzate", async () => {
  const cache = new CacheLRU({ massimo: 2, durataMs: 50 });
  cache.scrivi(normalizza("Quali vettori?"), 1);
  cache.scrivi("b", 2);
  assert.equal(cache.leggi("quali vettori"), 1); // stessa domanda scritta in modo diverso
  cache.scrivi("c", 3); // supera il massimo: si scarta "b", la meno usata di recente
  assert.equal(cache.leggi("b"), undefined);
  assert.equal(cache.leggi("c"), 3);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(cache.leggi("c"), undefined); // scaduta
});
