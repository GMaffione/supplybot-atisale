// Test unitari del back end Node (node:test, nessuna dipendenza in piu').
// Uso: cd backend && npm test
import { test } from "node:test";
import assert from "node:assert/strict";

process.env.OPENAI_API_KEY ??= "chiave-finta-per-i-test"; // config.js la richiede; qui non si chiama OpenAI

const { dividiInFrammenti, dividiInBlocchi, filtraPerSoglia, estraiTesto } = await import("../backend/src/rag.js");
const { schemaChat, schemaReset, valida } = await import("../backend/src/validazione.js");
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

test("zod, casi limite: lunghezza del messaggio esattamente al massimo", () => {
  const max = config.maxCaratteriDomanda;
  const id = "sessione-123";
  assert.equal(valida(schemaChat, { sessionId: id, message: "x".repeat(max) }).ok, true);
  assert.equal(valida(schemaChat, { sessionId: id, message: "x".repeat(max + 1) }).ok, false);
  // gli spazi ai bordi si tolgono PRIMA di contare: max caratteri utili + spazi = valido
  const conSpazi = valida(schemaChat, { sessionId: id, message: `   ${"x".repeat(max)}   ` });
  assert.equal(conSpazi.ok, true);
  assert.equal(/** @type {any} */ (conSpazi).dati.message.length, max);
  assert.equal(valida(schemaChat, { sessionId: id, message: "a" }).ok, true); // un solo carattere basta
  assert.equal(valida(schemaChat, { sessionId: id, message: "\n\t  " }).ok, false); // solo spazi e a capo
});

test("zod, casi limite: formato dell'id di sessione", () => {
  const conId = (sessionId) => valida(schemaChat, { sessionId, message: "ciao" }).ok;
  assert.equal(conId("a".repeat(7)), false); // troppo corto
  assert.equal(conId("a".repeat(8)), true); // minimo
  assert.equal(conId("a".repeat(64)), true); // massimo
  assert.equal(conId("a".repeat(65)), false); // troppo lungo
  assert.equal(conId("sessione_123"), false); // trattino basso non ammesso
  assert.equal(conId("sessione 123"), false); // spazio
  assert.equal(conId("sessione-àèì"), false); // lettere accentate
  assert.equal(conId("../../etc/x"), false); // percorsi
  assert.equal(conId(12345678), false); // numero invece di testo
});

test("zod, casi limite: corpo della richiesta di forma sbagliata", () => {
  for (const corpo of [null, undefined, "testo", 42, [], [{ sessionId: "sessione-123", message: "ciao" }]]) {
    assert.equal(valida(schemaChat, corpo).ok, false, `deve rifiutare ${JSON.stringify(corpo)}`);
  }
  assert.equal(valida(schemaChat, { sessionId: "sessione-123", message: null }).ok, false);
  assert.equal(valida(schemaChat, { sessionId: "sessione-123", message: ["ciao"] }).ok, false);
  assert.equal(valida(schemaChat, { sessionId: "sessione-123", message: "Il disgelo ❄️ a gennaio?" }).ok, true); // emoji ok
});

test("zod: /api/reset accetta solo un sessionId valido", () => {
  assert.equal(valida(schemaReset, { sessionId: "sessione-123" }).ok, true);
  assert.equal(valida(schemaReset, { sessionId: "x" }).ok, false);
  assert.equal(valida(schemaReset, { sessionId: "sessione-123", message: "ciao" }).ok, false); // campo in piu'
  assert.equal(valida(schemaReset, {}).ok, false);
});

test("cache: LRU, scadenza e chiavi normalizzate", () => {
  let ora = 0;
  const cache = new CacheLRU({ massimo: 2, durataMs: 1000, orologio: () => ora });
  cache.scrivi(normalizza("Quali vettori?"), 1);
  cache.scrivi("b", 2);
  assert.equal(cache.leggi("quali vettori"), 1); // stessa domanda scritta in modo diverso; "b" diventa la meno usata
  cache.scrivi("c", 3); // supera il massimo: si scarta "b"
  assert.equal(cache.leggi("b"), undefined);
  assert.equal(cache.leggi("c"), 3);
  ora = 1000;
  assert.equal(cache.leggi("c"), 3); // esattamente alla scadenza: ancora valida
  ora = 1001;
  assert.equal(cache.leggi("c"), undefined); // un millisecondo dopo: scaduta
  assert.equal(cache.stato().scadute, 1);
  cache.scrivi("d", 4);
  cache.svuota();
  assert.equal(cache.stato().voci, 0); // svuotata, come dopo un riavvio
});

test("cache: riscrivere una voce ne aggiorna valore e scadenza", () => {
  let ora = 0;
  const cache = new CacheLRU({ durataMs: 1000, orologio: () => ora });
  cache.scrivi("a", "vecchio");
  ora = 900;
  cache.scrivi("a", "nuovo");
  ora = 1500; // 1500 ms dalla prima scrittura, 600 dalla seconda
  assert.equal(cache.leggi("a"), "nuovo");
});
