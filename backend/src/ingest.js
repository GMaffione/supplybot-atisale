// Reindicizza il manuale in ChromaDB da zero.  Uso: npm run ingest
// (Il server lo fa gia' da solo al primo avvio se la collezione e' vuota.)
import { indicizzaManuale, dividiInFrammenti, estraiTesto } from "./rag.js";
import { config } from "./config.js";

const frammenti = dividiInFrammenti(await estraiTesto(config.manualePdf));
console.log(`Manuale diviso in ${frammenti.length} frammenti:`);
for (const f of frammenti) console.log(`  - ${f.metadati.sezione.padEnd(4)} ${f.metadati.titolo}`);
const esito = await indicizzaManuale({ forza: true });
console.log(`Indicizzati ${esito.nuovi} frammenti nella collezione "${config.collezione}".`);
