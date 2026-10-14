// Configurazione centrale: tutte le impostazioni arrivano dal file .env nella radice del progetto
// (in locale) o dalle variabili d'ambiente del servizio (online, su Render). Nessuna chiave e' scritta nel codice.
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
  // Render assegna la porta con PORT; in locale si usa BACKEND_PORT
  porta: Number(process.env.PORT || process.env.BACKEND_PORT || 3001),
  modello: process.env.OPENAI_MODEL || "gpt-6-luna",
  modelloEmbedding: process.env.OPENAI_EMBEDDING_MODEL || "text-embedding-3-small",
  chromaHost: process.env.CHROMA_HOST || "localhost",
  chromaPorta: Number(process.env.CHROMA_PORT || 8001),
  // Online: se c'e' CHROMA_API_KEY si usa Chroma Cloud invece del server ChromaDB locale
  chromaCloud: process.env.CHROMA_API_KEY
    ? Object.fromEntries(
        Object.entries({
          apiKey: process.env.CHROMA_API_KEY,
          tenant: process.env.CHROMA_TENANT,
          database: process.env.CHROMA_DATABASE,
        }).filter(([, v]) => v), // le voci non impostate restano ai valori predefiniti del client
      )
    : null,
  collezione: "manuale_supply_chain",
  dataAgentUrl: process.env.DATA_AGENT_URL || "http://localhost:8000",
  manualePdf: path.join(RADICE, "data", "manuale_supply_chain.pdf"),
  maxPassi: 6,
  maxTurniMemoria: 12,
  // Protezione dei costi quando l'app e' pubblica: limiti per visitatore e totali al giorno
  maxCaratteriDomanda: Number(process.env.MAX_CARATTERI_DOMANDA || 1000),
  domandePerOraPerUtente: Number(process.env.LIMITE_DOMANDE_ORA || 20),
  domandeAlGiorno: Number(process.env.LIMITE_DOMANDE_GIORNO || 300),
  timeoutOpenAiMs: Number(process.env.OPENAI_TIMEOUT_MS || 60_000),
};
