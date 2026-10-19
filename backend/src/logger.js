// Log strutturati: una riga JSON per evento, con l'ID di correlazione della richiesta.
//
// Ogni domanda che arriva su /api/* riceve un request_id (o usa quello in X-Request-Id, se valido).
// L'ID viaggia con la richiesta grazie ad AsyncLocalStorage, quindi ogni log scritto durante quella
// richiesta lo contiene senza passarlo a mano; lo stesso ID viene inviato al data agent Python
// nell'intestazione X-Request-Id. Filtrando i log dei due servizi per request_id si ricostruisce
// il percorso completo di una domanda: decisioni dell'LLM, ricerca nel manuale, analisi dei dati.
//
// Esempio:
// {"ts":"2026-10-17T15:02:09.871Z","livello":"info","servizio":"backend","request_id":"6f1c...",
//  "evento":"decisione","strumento":"analizza_dati","argomenti":{"domanda":"..."}}
import { AsyncLocalStorage } from "node:async_hooks";
import crypto from "node:crypto";

const contesto = new AsyncLocalStorage();
const ID_VALIDO = /^[A-Za-z0-9-]{8,64}$/;

/** @returns {string} l'ID della richiesta in corso, "-" fuori da una richiesta */
export function requestIdCorrente() {
  return contesto.getStore()?.requestId ?? "-";
}

/**
 * @param {"info"|"warn"|"error"} livello
 * @param {string} evento nome breve e stabile, utile per filtrare (es. "decisione", "cache_trovata")
 * @param {Record<string, unknown>} [dati] campi aggiuntivi
 */
function scrivi(livello, evento, dati = {}) {
  const riga = { ts: new Date().toISOString(), livello, servizio: "backend", request_id: requestIdCorrente(), evento, ...dati };
  (livello === "error" ? process.stderr : process.stdout).write(JSON.stringify(riga) + "\n");
}

export const log = {
  /** @param {string} evento @param {Record<string, unknown>} [dati] */
  info: (evento, dati) => scrivi("info", evento, dati),
  /** @param {string} evento @param {Record<string, unknown>} [dati] */
  warn: (evento, dati) => scrivi("warn", evento, dati),
  /** @param {string} evento @param {Record<string, unknown>} [dati] */
  error: (evento, dati) => scrivi("error", evento, dati),
};

/**
 * Middleware Express: assegna il request_id, lo restituisce nell'intestazione X-Request-Id
 * e scrive una riga di log per ogni richiesta /api/ conclusa.
 * @param {import("express").Request} req
 * @param {import("express").Response} res
 * @param {import("express").NextFunction} next
 */
export function middlewareRequestId(req, res, next) {
  const ricevuto = req.get("x-request-id") ?? "";
  const requestId = ID_VALIDO.test(ricevuto) ? ricevuto : crypto.randomUUID();
  res.set("X-Request-Id", requestId);
  const inizio = performance.now();
  contesto.run({ requestId }, () => {
    if (req.path.startsWith("/api/") && !["/api/health", "/api/ping"].includes(req.path)) {
      res.on("finish", () =>
        contesto.run({ requestId }, () =>
          log.info("richiesta_http", {
            metodo: req.method,
            percorso: req.path,
            stato: res.statusCode,
            durata_ms: Math.round(performance.now() - inizio),
          }),
        ),
      );
    }
    next();
  });
}
