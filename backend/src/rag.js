// RAG custom su ChromaDB: indicizzazione del manuale e ricerca semantica.
// In locale si usa il server ChromaDB avviato sul PC; online (CHROMA_API_KEY impostata) Chroma Cloud.
// Il codice di indicizzazione e ricerca e' lo stesso: cambia solo il client.
import crypto from "node:crypto";
import fs from "node:fs";
import { ChromaClient, CloudClient } from "chromadb";
import OpenAI from "openai";
import { PDFParse } from "pdf-parse";
import { config } from "./config.js";
import { CacheLRU, normalizza } from "./cache.js";
import { log } from "./logger.js";

// legge OPENAI_API_KEY (ed eventuale OPENAI_BASE_URL) dall'ambiente
const openai = new OpenAI({ timeout: config.timeoutOpenAiMs, maxRetries: 1 });
const chroma = config.chromaCloud
  ? new CloudClient(config.chromaCloud)
  : new ChromaClient({ host: config.chromaHost, port: config.chromaPorta });
// Chroma Cloud usa l'indice SPANN, il server locale HNSW: in entrambi i casi distanza coseno
/** @type {import("chromadb").CreateCollectionConfiguration} */
const CONFIG_INDICE = config.chromaCloud ? { spann: { space: "cosine" } } : { hnsw: { space: "cosine" } };

// null = nessuna funzione di embedding lato Chroma (i vettori li calcoliamo noi con OpenAI);
// i tipi della libreria non prevedono null, ma il client lo accetta ed e' il modo documentato
const SENZA_FUNZIONE_EMBEDDING = /** @type {any} */ (null);

export const descrizioneChroma = config.chromaCloud ? "Chroma Cloud" : `${config.chromaHost}:${config.chromaPorta}`;

async function embedding(testi) {
  const r = await openai.embeddings.create({ model: config.modelloEmbedding, input: testi });
  return r.data.map((d) => d.embedding);
}

// --- 1. Dal PDF al testo pulito ---------------------------------------------------------
export async function estraiTesto(percorsoPdf) {
  const parser = new PDFParse({ data: fs.readFileSync(percorsoPdf) });
  const { text } = await parser.getText();
  await parser.destroy();
  return text
    .split("\n")
    .map((r) => r.trim())
    // via pie' di pagina e separatori di pagina
    .filter((r) => r && !/documento simulato a scopo didattico Pagina \d+/.test(r) && !/^--\s*\d+ of \d+\s*--$/.test(r))
    .join("\n");
}

// --- 2. Dal testo ai frammenti (chunk), uno per paragrafo del manuale --------------------
// Strategia principale, per documenti con titoli numerati (come il manuale):
// - si spezza sui titoli (es. "5.3 Emergenza neve"); per non confondere un titolo con un elenco
//   numerato ("1. L'ufficio ordini..."), un capitolo e' accettato solo se segue la numerazione attesa;
// - un capitolo di domande frequenti (titolo con "Domande frequenti" o "FAQ", in qualunque posizione)
//   si spezza una domanda alla volta;
// - un paragrafo troppo lungo si divide in blocchi, ripetendo il titolo in ognuno.
// Strategia di riserva, per PDF senza titoli numerati riconoscibili: blocchi di lunghezza fissa
// con sovrapposizione, tagliati a fine riga. Cosi' il chunking funziona anche con un altro documento.
const LUNGHEZZA_BLOCCO = 1500;
const SOVRAPPOSIZIONE = 200;
const MIN_FRAMMENTI_STRUTTURATI = 3;
const CAPITOLO_FAQ = /domande frequenti|\bfaq\b/i;

/** Divide un testo in blocchi di circa `lunghezza` caratteri, tagliando a fine riga, con sovrapposizione. */
export function dividiInBlocchi(testo, lunghezza = LUNGHEZZA_BLOCCO, sovrapposizione = SOVRAPPOSIZIONE) {
  const righe = testo.split("\n").filter((r) => r.trim());
  const blocchi = [];
  let corrente = [];
  let lunghezzaCorrente = 0;
  for (const riga of righe) {
    if (lunghezzaCorrente + riga.length > lunghezza && corrente.length) {
      blocchi.push(corrente.join("\n"));
      // sovrapposizione: le ultime righe del blocco precedente aprono il successivo
      const coda = [];
      let lunghezzaCoda = 0;
      for (let i = corrente.length - 1; i >= 0 && lunghezzaCoda + corrente[i].length <= sovrapposizione; i--) {
        coda.unshift(corrente[i]);
        lunghezzaCoda += corrente[i].length;
      }
      corrente = coda;
      lunghezzaCorrente = lunghezzaCoda;
    }
    corrente.push(riga);
    lunghezzaCorrente += riga.length;
  }
  if (corrente.length) blocchi.push(corrente.join("\n"));
  return blocchi;
}

export function dividiInFrammenti(testo) {
  const strutturati = dividiPerTitoli(testo);
  if (strutturati.length >= MIN_FRAMMENTI_STRUTTURATI) return strutturati;
  // nessuna struttura riconosciuta: blocchi a lunghezza fissa
  return dividiInBlocchi(testo).map((blocco, i) => ({
    id: `manuale-${String(i).padStart(3, "0")}`,
    testo: blocco,
    metadati: { sezione: `blocco ${i + 1}`, titolo: blocco.split("\n")[0].slice(0, 80), capitolo: "" },
  }));
}

function dividiPerTitoli(testo) {
  const righe = testo.split("\n");
  const frammenti = [];
  let capitolo = 0;
  let titoloCapitolo = "";
  /** @type {any} frammento in costruzione */
  let corrente = null;

  const chiudi = () => {
    if (corrente && corrente.righe.join(" ").trim().length > 40) frammenti.push(corrente);
    corrente = null;
  };
  const apri = (sezione, titolo) => {
    chiudi();
    corrente = { sezione, titolo, capitolo: titoloCapitolo, righe: [] };
  };

  for (const riga of righe) {
    const principale = riga.match(/^(\d+)\.\s+([A-ZÀ-Ü].{2,80})$/);
    const sotto = riga.match(/^(\d+)\.(\d+)\s+([A-ZÀ-Ü].{2,80})$/);
    if (principale && Number(principale[1]) === capitolo + 1) {
      capitolo += 1;
      titoloCapitolo = `${capitolo}. ${principale[2]}`;
      apri(String(capitolo), principale[2]);
    } else if (sotto && Number(sotto[1]) === capitolo) {
      apri(`${sotto[1]}.${sotto[2]}`, sotto[3]);
    } else if (CAPITOLO_FAQ.test(titoloCapitolo) && riga.endsWith("?")) {
      // una domanda lunga va a capo: se la riga prima non chiude una frase, fa parte della domanda
      const prima = corrente?.righe.at(-1);
      const inizio = prima && !/[.?:)]$/.test(prima) ? corrente.righe.pop() + " " : "";
      apri(String(capitolo), `FAQ: ${inizio}${riga}`);
      corrente.righe.push(inizio + riga);
    } else if (corrente) {
      corrente.righe.push(riga);
    } else {
      corrente = { sezione: "0", titolo: "Intestazione e avviso", capitolo: "Intestazione", righe: [riga] };
    }
  }
  chiudi();

  // un paragrafo molto lungo diventerebbe un unico vettore "annacquato": si divide in blocchi
  const pezzi = frammenti.flatMap((f) => {
    const intestazione = `${f.capitolo} > ${f.sezione} ${f.titolo}`;
    const corpo = f.righe.join("\n");
    const blocchi = corpo.length > 2 * LUNGHEZZA_BLOCCO ? dividiInBlocchi(corpo) : [corpo];
    return blocchi.map((b) => ({ ...f, testo: `${intestazione}\n${b}` }));
  });
  return pezzi.map((f, i) => ({
    id: `manuale-${String(i).padStart(3, "0")}`,
    testo: f.testo,
    metadati: { sezione: f.sezione, titolo: f.titolo, capitolo: f.capitolo },
  }));
}

// --- 3. Indicizzazione in Chroma --------------------------------------------------------
// Il nome della collezione contiene l'impronta dei frammenti (es. manuale_supply_chain_3fa1c9e2).
// Se il manuale o le regole di chunking cambiano, cambia l'impronta e si crea una collezione NUOVA:
// quella in uso non viene mai cancellata, quindi due versioni del codice che condividono lo stesso
// database (es. locale e online su Chroma Cloud) non si sovrascrivono a vicenda.
let collezioneAttiva = config.collezione;

/** @param {{ testo: string }[]} frammenti */
function nomeCollezione(frammenti) {
  const impronta = crypto.createHash("sha256");
  for (const f of frammenti) impronta.update(f.testo).update("\u0000");
  return `${config.collezione}_${impronta.digest("hex").slice(0, 8)}`;
}

export async function indicizzaManuale({ forza = false } = {}) {
  const frammenti = dividiInFrammenti(await estraiTesto(config.manualePdf));
  const nome = nomeCollezione(frammenti);
  if (forza) await chroma.deleteCollection({ name: nome }).catch(() => {});
  let collezione = await chroma.getOrCreateCollection({
    name: nome,
    embeddingFunction: SENZA_FUNZIONE_EMBEDDING, // gli embedding li calcoliamo noi con OpenAI
    configuration: CONFIG_INDICE,
  });
  const presenti = await collezione.count();
  if (presenti === frammenti.length) {
    collezioneAttiva = nome;
    return { frammenti: presenti, nuovi: 0, collezione: nome };
  }
  if (presenti > 0) {
    // indicizzazione interrotta a meta' in un avvio precedente: si rifa' solo questa collezione
    log.warn("indice_incompleto", { collezione: nome, presenti, attesi: frammenti.length });
    await chroma.deleteCollection({ name: nome });
    collezione = await chroma.getOrCreateCollection({ name: nome, embeddingFunction: SENZA_FUNZIONE_EMBEDDING, configuration: CONFIG_INDICE });
  }
  cacheRicerche.svuota(); // indice nuovo: i risultati delle ricerche precedenti non valgono piu'
  const vettori = await embedding(frammenti.map((f) => f.testo));
  await collezione.add({
    ids: frammenti.map((f) => f.id),
    documents: frammenti.map((f) => f.testo),
    metadatas: frammenti.map((f) => f.metadati),
    embeddings: vettori,
  });
  collezioneAttiva = nome;
  return { frammenti: frammenti.length, nuovi: frammenti.length, collezione: nome };
}

// --- 4. Ricerca semantica ---------------------------------------------------------------
// Si recuperano i k frammenti piu' vicini, poi si SCARTANO quelli sotto la soglia di somiglianza:
// una domanda che non c'entra col manuale restituisce "nessun risultato pertinente" invece di
// capitoli lontani che l'LLM potrebbe citare a sproposito. Nel RAG la qualita' del recupero conta
// piu' del modello.
// Cache delle ricerche: chiave = query normalizzata. Si svuota al riavvio (e' in memoria), dopo 6 ore,
// oltre 300 voci (LRU) e ogni volta che l'indice del manuale viene ricostruito (vedi indicizzaManuale).
const cacheRicerche = new CacheLRU({ massimo: 300 });

/** Tiene solo i risultati con somiglianza >= soglia (funzione pura, testata in tests/backend.test.js). */
export function filtraPerSoglia(risultati, soglia = config.sogliaSimilarita) {
  return {
    pertinenti: risultati.filter((r) => r.somiglianza >= soglia),
    scartati: risultati.filter((r) => r.somiglianza < soglia),
    soglia,
  };
}

export async function cercaManuale(domanda, k = 4) {
  const chiave = normalizza(domanda);
  const salvati = cacheRicerche.leggi(chiave);
  let risultati = salvati;
  if (salvati) {
    log.info("ricerca_manuale_da_cache", { query: domanda });
  } else {
    const collezione = await chroma.getCollection({ name: collezioneAttiva, embeddingFunction: SENZA_FUNZIONE_EMBEDDING });
    const [vettore] = await embedding([domanda]);
    const r = await collezione.query({ queryEmbeddings: [vettore], nResults: k });
    const metadati = r.metadatas[0] ?? [];
    const distanze = r.distances?.[0] ?? [];
    risultati = (r.documents[0] ?? []).map((testo, i) => ({
      sezione: String(metadati[i]?.sezione ?? ""),
      titolo: String(metadati[i]?.titolo ?? ""),
      testo: testo ?? "",
      somiglianza: Number((1 - (distanze[i] ?? 1)).toFixed(3)),
    }));
    cacheRicerche.scrivi(chiave, risultati);
  }
  const esito = filtraPerSoglia(risultati);
  log.info("ricerca_manuale", {
    query: domanda,
    pertinenti: esito.pertinenti.map((r) => [r.sezione, r.somiglianza]),
    scartati: esito.scartati.map((r) => [r.sezione, r.somiglianza]),
    soglia: esito.soglia,
    da_cache: Boolean(salvati),
  });
  return { ...esito, daCache: Boolean(salvati) };
}

export function statoCacheRicerche() {
  return cacheRicerche.stato();
}

export async function chromaRaggiungibile() {
  try {
    await chroma.heartbeat();
    return true;
  } catch {
    return false;
  }
}
