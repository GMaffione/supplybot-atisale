// Server Express: unico punto di contatto del front end.
//   POST /api/chat          { sessionId, message } -> { reply, charts, trace }
//   POST /api/reset         { sessionId }           -> azzera la memoria della conversazione
//   GET  /api/charts/:nome  -> immagine del grafico (inoltrata dal servizio Python)
//   GET  /api/health        -> stato di Chroma e del data agent
import express from "express";
import { Readable } from "node:stream";
import { config } from "./config.js";
import { chat, azzeraSessione } from "./orchestrator.js";
import { indicizzaManuale, chromaRaggiungibile } from "./rag.js";
import { dataAgentRaggiungibile, scaricaGrafico } from "./dataAgent.js";

const app = express();
app.use(express.json({ limit: "100kb" }));

const ID_VALIDO = /^[A-Za-z0-9-]{8,64}$/;

app.post("/api/chat", async (req, res) => {
  const { sessionId, message } = req.body ?? {};
  if (!ID_VALIDO.test(sessionId ?? "") || typeof message !== "string" || !message.trim() || message.length > 2000) {
    return res.status(400).json({ error: "Richiesta non valida: servono sessionId e un messaggio (max 2000 caratteri)." });
  }
  const inizio = Date.now();
  try {
    const esito = await chat(sessionId, message.trim());
    res.json({
      reply: esito.risposta,
      charts: esito.grafici.map((nome) => ({ nome, url: `/api/charts/${nome}` })),
      trace: esito.traccia,
      durationMs: Date.now() - inizio,
    });
  } catch (errore) {
    console.error("[ERRORE CHAT]", errore);
    res.status(500).json({ error: `Errore del back end: ${errore.message}` });
  }
});

app.post("/api/reset", (req, res) => {
  if (ID_VALIDO.test(req.body?.sessionId ?? "")) azzeraSessione(req.body.sessionId);
  res.json({ ok: true });
});

app.get("/api/charts/:nome", async (req, res) => {
  if (!/^grafico_[0-9a-f]{10}\.png$/.test(req.params.nome)) return res.status(400).end();
  try {
    const r = await scaricaGrafico(req.params.nome);
    if (!r.ok) return res.status(r.status).end();
    res.type("image/png");
    Readable.fromWeb(r.body).pipe(res);
  } catch {
    res.status(502).end();
  }
});

app.get("/api/health", async (_req, res) => {
  const [chroma, dataAgent] = await Promise.all([chromaRaggiungibile(), dataAgentRaggiungibile()]);
  res.status(chroma && dataAgent ? 200 : 503).json({ backend: true, chroma, dataAgent, modello: config.modello });
});

// Avvio: verifica Chroma e indicizza il manuale se la collezione e' vuota
// ChromaDB puo' impiegare qualche secondo ad avviarsi: si riprova per 30 secondi
for (let i = 0; i < 15 && !(await chromaRaggiungibile()); i++) {
  if (i === 0) console.log("In attesa di ChromaDB...");
  await new Promise((r) => setTimeout(r, 2000));
}
if (!(await chromaRaggiungibile())) {
  console.error(`ERRORE: ChromaDB non risponde su ${config.chromaHost}:${config.chromaPorta}. Avvialo prima del back end (vedi README).`);
  process.exit(1);
}
const indice = await indicizzaManuale();
console.log(indice.nuovi ? `Manuale indicizzato: ${indice.nuovi} frammenti.` : `Manuale gia' indicizzato (${indice.frammenti} frammenti).`);
if (!(await dataAgentRaggiungibile())) {
  console.warn(`ATTENZIONE: il data agent non risponde su ${config.dataAgentUrl}. Le domande sui dati daranno errore finche' non e' avviato.`);
}
app.listen(config.porta, () => console.log(`SupplyBot back end pronto su http://localhost:${config.porta} (modello: ${config.modello})`));
