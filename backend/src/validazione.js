// Validazione a runtime dei dati in ingresso al confine dell'API, con zod.
// Lo schema dichiara in un solo punto cosa e' ammesso; se la richiesta non lo rispetta si risponde
// 400 con l'elenco dei campi sbagliati, prima di toccare l'LLM o i servizi. Dallo stesso schema si
// ricava anche il tipo dei dati validati (vedi i commenti @typedef), usato dal controllo dei tipi.
import { z } from "zod";
import { config } from "./config.js";

const idSessione = z
  .string({ error: "sessionId mancante" })
  .regex(/^[A-Za-z0-9-]{8,64}$/, "sessionId: 8-64 caratteri tra lettere, cifre e trattini");

export const schemaChat = z.strictObject({
  sessionId: idSessione,
  message: z
    .string({ error: "message mancante" })
    .trim()
    .min(1, "message vuoto")
    .max(config.maxCaratteriDomanda, `message: massimo ${config.maxCaratteriDomanda} caratteri`),
});

export const schemaReset = z.strictObject({ sessionId: idSessione });

/** @typedef {z.infer<typeof schemaChat>} RichiestaChat */

/**
 * Valida `dati` con `schema`: { ok: true, dati } oppure { ok: false, errore } con un messaggio leggibile.
 * @template {z.ZodType} S
 * @param {S} schema
 * @param {unknown} dati
 * @returns {{ ok: true, dati: z.infer<S> } | { ok: false, errore: string }}
 */
export function valida(schema, dati) {
  const esito = schema.safeParse(dati ?? {});
  if (esito.success) return { ok: true, dati: esito.data };
  const dettagli = esito.error.issues.map((i) => (i.code === "unrecognized_keys" ? `campi non ammessi: ${i.keys.join(", ")}` : i.message));
  return { ok: false, errore: `Richiesta non valida: ${dettagli.join("; ")}.` };
}
