// Configurazione centrale: tutte le impostazioni arrivano dal file .env nella radice del progetto.
// Nessuna chiave e' scritta nel codice.
import path from "node:path";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

const qui = path.dirname(fileURLToPath(import.meta.url));
export const RADICE = path.resolve(qui, "..", "..");
dotenv.config({ path: path.join(RADICE, ".env"), quiet: true });

if (!process.env.OPENAI_API_KEY) {
  console.error("ERRORE: OPENAI_API_KEY mancante. Copia .env.example in .env e inserisci la tua chiave.");
  process.exit(1);
}

export const config = {
  porta: Number(process.env.BACKEND_PORT || 3001),
  modello: process.env.OPENAI_MODEL || "gpt-6-luna",
  modelloEmbedding: process.env.OPENAI_EMBEDDING_MODEL || "text-embedding-3-small",
  chromaHost: process.env.CHROMA_HOST || "localhost",
  chromaPorta: Number(process.env.CHROMA_PORT || 8001),
  collezione: "manuale_supply_chain",
  dataAgentUrl: process.env.DATA_AGENT_URL || "http://localhost:8000",
  manualePdf: path.join(RADICE, "data", "manuale_supply_chain.pdf"),
  maxPassi: 6,
  maxTurniMemoria: 12,
};
