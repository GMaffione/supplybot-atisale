// Client HTTP verso il microservizio Python (FastAPI) del data agent.
import { config } from "./config.js";
import { requestIdCorrente } from "./logger.js";

const TIMEOUT_MS = 240_000; // un'analisi con autocorrezioni puo' richiedere fino a qualche minuto

export async function analizzaDati(domanda) {
  const risposta = await fetch(`${config.dataAgentUrl}/analyze`, {
    method: "POST",
    // l'ID di correlazione accompagna la chiamata: nei log del data agent compare lo stesso request_id
    headers: { "Content-Type": "application/json", "X-Request-Id": requestIdCorrente() },
    body: JSON.stringify({ domanda }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!risposta.ok) {
    const dettaglio = await risposta.text();
    let messaggio = dettaglio.slice(0, 300);
    try {
      // 503 = il CSV non ha superato la validazione dello schema: si riportano gli errori trovati
      const corpo = JSON.parse(dettaglio);
      const descrizione = typeof corpo.detail === "string" ? corpo.detail : JSON.stringify(corpo.detail);
      messaggio = [descrizione, ...(corpo.errori ?? [])].filter(Boolean).join(" ").slice(0, 600);
    } catch {
      /* risposta non JSON: si usa il testo cosi' com'e' */
    }
    throw new Error(`Data agent: HTTP ${risposta.status} ${messaggio}`);
  }
  return risposta.json();
}

export async function dataAgentRaggiungibile() {
  try {
    const r = await fetch(`${config.dataAgentUrl}/health`, {
      headers: { "X-Request-Id": requestIdCorrente() },
      signal: AbortSignal.timeout(3000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

// Scarica un grafico dal servizio Python (il front end parla solo con il back end)
export async function scaricaGrafico(nome) {
  return fetch(`${config.dataAgentUrl}/charts/${encodeURIComponent(nome)}`, {
    headers: { "X-Request-Id": requestIdCorrente() },
    signal: AbortSignal.timeout(10_000),
  });
}
