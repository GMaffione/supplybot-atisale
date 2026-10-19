// Server Express: unico punto di contatto del front end.
//   POST /api/chat          { sessionId, message } -> { reply, charts, trace }
//   POST /api/reset         { sessionId }           -> azzera la memoria della conversazione
//   GET  /api/charts/:nome  -> immagine del grafico (inoltrata dal servizio Python)
//   GET  /api/health        -> stato di Chroma e del data agent
//   GET  /api/ping          -> il back end e' vivo (controllo di salute della piattaforma online)
// Online serve anche la pagina React gia' compilata (frontend/dist): un solo indirizzo per tutto.
import fs from "node:fs";
import path from "node:path";
import express from "express";
import { Readable } from "node:stream";
import { config, RADICE } from "./config.js";
import { chat, azzeraSessione, statoCacheRisposte } from "./orchestrator.js";
import { indicizzaManuale, chromaRaggiungibile, descrizioneChroma, statoCacheRicerche } from "./rag.js";
import { dataAgentRaggiungibile, scaricaGrafico } from "./dataAgent.js";
import { controllaLimiti } from "./limiti.js";
import { log, middlewareRequestId, requestIdCorrente } from "./logger.js";
import { schemaChat, schemaReset, valida } from "./validazione.js";

const app = express();
app.set("trust proxy", 1); // online le richieste passano dal proxy della piattaforma: serve l'IP reale del visitatore
app.use(middlewareRequestId); // request_id per ogni richiesta, nei log e verso il data agent
app.use(express.json({ limit: "100kb" }));

app.post("/api/chat", async (req, res) => {
  // validazione dello schema in ingresso (zod): campi, tipi, lunghezze; nient'altro e' ammesso
  const v = valida(schemaChat, req.body);
  if (!v.ok) {
    log.warn("richiesta_non_valida", { errore: v.errore });
    return res.status(400).json({ error: v.errore, requestId: requestIdCorrente() });
  }
  const { sessionId, message } = v.dati;
  // ogni domanda costa chiamate a OpenAI: limiti per visitatore e totali (vedi limiti.js)
  // online il visitatore arriva dietro al proxy della piattaforma: si usa l'IP originale se il proxy lo indica
  const visitatore = req.get("cf-connecting-ip") || req.get("true-client-ip") || req.ip;
  const blocco = controllaLimiti(visitatore);
  if (blocco) return res.status(429).json({ error: blocco });
  const inizio = Date.now();
  try {
    const esito = await chat(sessionId, message);
    res.json({
      reply: esito.risposta,
      charts: esito.grafici.map((nome) => ({ nome, url: `/api/charts/${nome}` })),
      trace: esito.traccia,
      cached: esito.daCache,
      durationMs: Date.now() - inizio,
      requestId: requestIdCorrente(),
    });
  } catch (errore) {
    log.error("errore_chat", { messaggio: errore.message, stack: errore.stack });
    res.status(500).json({ error: `Errore del back end: ${errore.message}`, requestId: requestIdCorrente() });
  }
});

app.post("/api/reset", (req, res) => {
  const v = valida(schemaReset, req.body);
  if (!v.ok) return res.status(400).json({ error: v.errore });
  azzeraSessione(v.dati.sessionId);
  res.json({ ok: true });
});

app.get("/api/charts/:nome", async (req, res) => {
  if (!/^grafico_[0-9a-f]{10}\.png$/.test(req.params.nome)) return res.status(400).end();
  try {
    const r = await scaricaGrafico(req.params.nome);
    if (!r.ok || !r.body) return res.status(r.ok ? 502 : r.status).end();
    res.type("image/png");
    Readable.fromWeb(/** @type {import("node:stream/web").ReadableStream} */ (r.body)).pipe(res);
  } catch {
    res.status(502).end();
  }
});

app.get("/api/ping", (_req, res) => res.json({ ok: true }));

app.get("/api/health", async (_req, res) => {
  const [chroma, dataAgent] = await Promise.all([chromaRaggiungibile(), dataAgentRaggiungibile()]);
  res.status(chroma && dataAgent ? 200 : 503).json({
    backend: true,
    chroma,
    dataAgent,
    modello: config.modello,
    cache: { risposte: statoCacheRisposte(), ricercheManuale: statoCacheRicerche() },
  });
});

// Pagina React compilata (presente online dopo "npm run build" nel front end)
const cartellaFrontEnd = path.join(RADICE, "frontend", "dist");
if (fs.existsSync(cartellaFrontEnd)) {
  app.use(express.static(cartellaFrontEnd));
  app.get(/^\/(?!api\/).*/, (_req, res) => res.sendFile(path.join(cartellaFrontEnd, "index.html")));
}

// Avvio: verifica Chroma e indicizza il manuale se la collezione e' vuota
// ChromaDB puo' impiegare qualche secondo ad avviarsi: si riprova per 30 secondi
for (let i = 0; i < 15 && !(await chromaRaggiungibile()); i++) {
  if (i === 0) log.info("attesa_chroma", { indice: descrizioneChroma });
  await new Promise((r) => setTimeout(r, 2000));
}
if (!(await chromaRaggiungibile())) {
  log.error("chroma_non_raggiungibile", {
    indice: descrizioneChroma,
    suggerimento: "In locale avvia ChromaDB prima del back end; online controlla CHROMA_API_KEY, CHROMA_TENANT e CHROMA_DATABASE (vedi README).",
  });
  process.exit(1);
}
const indice = await indicizzaManuale();
log.info("manuale_indicizzato", { frammenti: indice.frammenti, nuovi: indice.nuovi, collezione: indice.collezione });
if (!(await dataAgentRaggiungibile())) {
  log.warn("data_agent_non_raggiungibile", { url: config.dataAgentUrl, nota: "le domande sui dati daranno errore finche' non e' avviato" });
}
app.listen(config.porta, () =>
  log.info("backend_pronto", { url: `http://localhost:${config.porta}`, modello: config.modello, indice: descrizioneChroma }),
);
