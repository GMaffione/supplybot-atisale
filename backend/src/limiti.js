// Protezione dei costi: quando l'app e' pubblica chiunque puo' fare domande, e ogni domanda
// consuma chiamate a OpenAI pagate con la chiave del progetto. Due limiti, tenuti in memoria:
//   - per visitatore (indirizzo IP): al massimo N domande nell'ultima ora;
//   - totale: al massimo M domande al giorno per tutto il servizio.
// Al riavvio del servizio i contatori ripartono da zero: per un progetto dimostrativo basta.
import { config } from "./config.js";

const UN_ORA = 60 * 60 * 1000;
const richiestePerIp = new Map(); // ip -> istanti delle domande nell'ultima ora
let giorno = new Date().toISOString().slice(0, 10);
let domandeOggi = 0;

export function controllaLimiti(ip = "sconosciuto") {
  const adesso = Date.now();

  const oggi = new Date(adesso).toISOString().slice(0, 10);
  if (oggi !== giorno) {
    giorno = oggi;
    domandeOggi = 0;
  }
  if (domandeOggi >= config.domandeAlGiorno) {
    return "SupplyBot ha raggiunto il numero massimo di domande per oggi (protezione dei costi). Riprova domani.";
  }

  const recenti = (richiestePerIp.get(ip) ?? []).filter((t) => adesso - t < UN_ORA);
  if (recenti.length >= config.domandePerOraPerUtente) {
    const minuti = Math.ceil((UN_ORA - (adesso - recenti[0])) / 60000);
    return `Hai raggiunto il limite di ${config.domandePerOraPerUtente} domande all'ora. Riprova tra ${minuti} minuti.`;
  }

  recenti.push(adesso);
  richiestePerIp.set(ip, recenti);
  domandeOggi += 1;

  // pulizia periodica: niente memoria che cresce all'infinito
  if (richiestePerIp.size > 5000) {
    for (const [chiave, tempi] of richiestePerIp) {
      if (!tempi.some((t) => adesso - t < UN_ORA)) richiestePerIp.delete(chiave);
    }
  }
  return null;
}
