// Client HTTP verso il microservizio Python (FastAPI) del data agent.
import { config } from "./config.js";

const TIMEOUT_MS = 240_000; // un'analisi con autocorrezioni puo' richiedere fino a qualche minuto

export async function analizzaDati(domanda) {
  const risposta = await fetch(`${config.dataAgentUrl}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domanda }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!risposta.ok) {
    const dettaglio = await risposta.text();
    throw new Error(`Data agent: HTTP ${risposta.status} ${dettaglio.slice(0, 300)}`);
  }
  return risposta.json();
}

export async function dataAgentRaggiungibile() {
  try {
    const r = await fetch(`${config.dataAgentUrl}/health`, { signal: AbortSignal.timeout(3000) });
    return r.ok;
  } catch {
    return false;
  }
}

// Scarica un grafico dal servizio Python (il front end parla solo con il back end)
export async function scaricaGrafico(nome) {
  return fetch(`${config.dataAgentUrl}/charts/${encodeURIComponent(nome)}`, { signal: AbortSignal.timeout(10_000) });
}
