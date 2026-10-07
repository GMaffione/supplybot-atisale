// RAG custom su ChromaDB locale: indicizzazione del manuale e ricerca semantica.
import fs from "node:fs";
import { ChromaClient } from "chromadb";
import OpenAI from "openai";
import { PDFParse } from "pdf-parse";
import { config } from "./config.js";

const openai = new OpenAI(); // legge OPENAI_API_KEY (ed eventuale OPENAI_BASE_URL) dall'ambiente
const chroma = new ChromaClient({ host: config.chromaHost, port: config.chromaPorta });

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
// Si spezza sui titoli numerati (es. "5.3 Emergenza neve"). Per non confondere un titolo con
// un elenco numerato ("1. L'ufficio ordini..."), un capitolo e' accettato solo se segue la
// numerazione attesa. Le FAQ si spezzano una domanda alla volta.
export function dividiInFrammenti(testo) {
  const righe = testo.split("\n");
  const frammenti = [];
  let capitolo = 0;
  let titoloCapitolo = "";
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
    } else if (titoloCapitolo.startsWith("10.") && riga.endsWith("?")) {
      // una domanda lunga va a capo: se la riga prima non chiude una frase, fa parte della domanda
      const prima = corrente?.righe.at(-1);
      const inizio = prima && !/[.?:)]$/.test(prima) ? corrente.righe.pop() + " " : "";
      apri("10", `FAQ: ${inizio}${riga}`);
      corrente.righe.push(inizio + riga);
    } else if (corrente) {
      corrente.righe.push(riga);
    } else {
      corrente = { sezione: "0", titolo: "Intestazione e avviso", capitolo: "Intestazione", righe: [riga] };
    }
  }
  chiudi();

  return frammenti.map((f, i) => ({
    id: `manuale-${String(i).padStart(3, "0")}`,
    testo: `${f.capitolo} > ${f.sezione} ${f.titolo}\n${f.righe.join("\n")}`,
    metadati: { sezione: f.sezione, titolo: f.titolo, capitolo: f.capitolo },
  }));
}

// --- 3. Indicizzazione in Chroma --------------------------------------------------------
export async function indicizzaManuale({ forza = false } = {}) {
  if (forza) {
    await chroma.deleteCollection({ name: config.collezione }).catch(() => {});
  }
  const collezione = await chroma.getOrCreateCollection({
    name: config.collezione,
    embeddingFunction: null, // gli embedding li calcoliamo noi con OpenAI
    configuration: { hnsw: { space: "cosine" } },
  });
  const presenti = await collezione.count();
  if (presenti > 0 && !forza) return { frammenti: presenti, nuovi: 0 };

  const frammenti = dividiInFrammenti(await estraiTesto(config.manualePdf));
  const vettori = await embedding(frammenti.map((f) => f.testo));
  await collezione.add({
    ids: frammenti.map((f) => f.id),
    documents: frammenti.map((f) => f.testo),
    metadatas: frammenti.map((f) => f.metadati),
    embeddings: vettori,
  });
  return { frammenti: frammenti.length, nuovi: frammenti.length };
}

// --- 4. Ricerca semantica ---------------------------------------------------------------
export async function cercaManuale(domanda, k = 4) {
  const collezione = await chroma.getCollection({ name: config.collezione, embeddingFunction: null });
  const [vettore] = await embedding([domanda]);
  const r = await collezione.query({ queryEmbeddings: [vettore], nResults: k });
  return r.documents[0].map((testo, i) => ({
    sezione: r.metadatas[0][i].sezione,
    titolo: r.metadatas[0][i].titolo,
    testo,
    somiglianza: Number((1 - r.distances[0][i]).toFixed(3)),
  }));
}

export async function chromaRaggiungibile() {
  try {
    await chroma.heartbeat();
    return true;
  } catch {
    return false;
  }
}
