// Cache in memoria con due limiti: dimensione massima (si scarta la voce usata meno di recente, LRU)
// e scadenza. Usata per non rifare lavoro gia' fatto:
//   - ricerche nel manuale (rag.js): la stessa query non ricalcola l'embedding ne' interroga di nuovo Chroma;
//   - risposte complete alla PRIMA domanda di una conversazione (orchestrator.js): la stessa domanda non
//     rifa' l'intero ciclo (LLM, manuale, data agent). Le domande successive dipendono dalla conversazione
//     ("E per Sant'Antioco?" significa cose diverse in conversazioni diverse) e non entrano in questa cache;
//     il data agent ha comunque la sua cache delle analisi (data_agent/cache.py).
//
// QUANDO UNA VOCE SMETTE DI VALERE (invalidazione)
//   1. Riavvio del servizio: la cache vive solo nella memoria del processo, quindi si SVUOTA DEL TUTTO a ogni
//      riavvio. Online succede a ogni nuova pubblicazione (git push) e ogni volta che Render "addormenta" il
//      servizio dopo 15 minuti senza visite: la prima domanda dopo il risveglio viene ricalcolata.
//   2. Scadenza: una voce piu' vecchia di `durataMs` (6 ore) viene scartata alla lettura e ricalcolata.
//   3. Spazio: oltre `massimo` voci si elimina quella usata meno di recente.
//   4. Esplicita: `svuota()` cancella tutto (utile nei test o dopo aver cambiato manuale o dati).
// Le regole su COSA si mette in cache (niente errori, niente analisi non concluse) stanno dove la cache si usa.

/** Minuscole e spazi uniformati, senza punteggiatura finale: "Quali vettori?" = "quali vettori". */
export function normalizza(testo) {
  return testo.trim().toLowerCase().replace(/\s+/g, " ").replace(/[\s?!.]+$/, "");
}

/** @template T */
export class CacheLRU {
  /**
   * @param {{ massimo?: number, durataMs?: number, orologio?: () => number }} [opzioni]
   *   orologio: funzione che restituisce l'ora in millisecondi; si sostituisce nei test per simulare la scadenza
   */
  constructor({ massimo = 200, durataMs = 6 * 60 * 60 * 1000, orologio = Date.now } = {}) {
    this.massimo = massimo;
    this.durataMs = durataMs;
    this.orologio = orologio;
    /** @type {Map<string, { scrittaIl: number, valore: T }>} */
    this.voci = new Map();
    this.trovate = 0;
    this.mancate = 0;
    this.scadute = 0;
  }

  /** @param {string} chiave @returns {T | undefined} */
  leggi(chiave) {
    const voce = this.voci.get(chiave);
    if (voce && this.orologio() - voce.scrittaIl > this.durataMs) {
      this.voci.delete(chiave);
      this.scadute += 1;
      this.mancate += 1;
      return undefined;
    }
    if (!voce) {
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
    this.voci.set(chiave, { scrittaIl: this.orologio(), valore });
    while (this.voci.size > this.massimo) this.voci.delete(this.voci.keys().next().value);
  }

  /** Cancella tutte le voci (lo stesso effetto di un riavvio del servizio). */
  svuota() {
    this.voci.clear();
  }

  stato() {
    return {
      voci: this.voci.size,
      trovate: this.trovate,
      mancate: this.mancate,
      scadute: this.scadute,
      durata_ore: this.durataMs / 3_600_000,
      nota: "cache in memoria: si svuota a ogni riavvio del servizio",
    };
  }
}
