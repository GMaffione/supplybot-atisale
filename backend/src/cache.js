// Cache in memoria con due limiti: dimensione massima (si scarta la voce usata meno di recente, LRU)
// e scadenza. Usata per non rifare lavoro gia' fatto:
//   - ricerche nel manuale: la stessa query non ricalcola l'embedding ne' interroga di nuovo Chroma;
//   - risposte complete alla PRIMA domanda di una conversazione: la stessa domanda non rifa' l'intero
//     ciclo (LLM, manuale, data agent). Le domande successive dipendono dalla conversazione e non si
//     mettono in cache qui; il data agent ha comunque la sua cache delle analisi (data_agent/cache.py).

/** Minuscole e spazi uniformati, senza punteggiatura finale: "Quali vettori?" = "quali vettori". */
export function normalizza(testo) {
  return testo.trim().toLowerCase().replace(/\s+/g, " ").replace(/[\s?!.]+$/, "");
}

/** @template T */
export class CacheLRU {
  /** @param {{ massimo?: number, durataMs?: number }} [opzioni] */
  constructor({ massimo = 200, durataMs = 6 * 60 * 60 * 1000 } = {}) {
    this.massimo = massimo;
    this.durataMs = durataMs;
    /** @type {Map<string, { scrittaIl: number, valore: T }>} */
    this.voci = new Map();
    this.trovate = 0;
    this.mancate = 0;
  }

  /** @param {string} chiave @returns {T | undefined} */
  leggi(chiave) {
    const voce = this.voci.get(chiave);
    if (!voce || Date.now() - voce.scrittaIl > this.durataMs) {
      this.voci.delete(chiave);
      this.mancate += 1;
      return undefined;
    }
    // reinserita in fondo: la Map resta ordinata dalla voce usata meno di recente
    this.voci.delete(chiave);
    this.voci.set(chiave, voce);
    this.trovate += 1;
    return voce.valore;
  }

  /** @param {string} chiave @param {T} valore */
  scrivi(chiave, valore) {
    this.voci.delete(chiave);
    this.voci.set(chiave, { scrittaIl: Date.now(), valore });
    while (this.voci.size > this.massimo) this.voci.delete(this.voci.keys().next().value);
  }

  stato() {
    return { voci: this.voci.size, trovate: this.trovate, mancate: this.mancate };
  }
}
