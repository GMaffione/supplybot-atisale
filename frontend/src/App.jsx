import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const SUGGERIMENTI = [
  {
    gruppo: "Regole e procedure",
    strada: "manuale",
    domande: [
      "Un comune chiede sale da disgelo per un'allerta neve: entro quanto va servito?",
      "Come si gestisce un reclamo per sacchi rotti?",
    ],
  },
  {
    gruppo: "Analisi dei dati",
    strada: "dati",
    domande: [
      "Come sono andati i volumi di sale da disgelo nell'inverno 2025-26, mese per mese?",
      "Le spedizioni via nave da Sant'Antioco sono meno puntuali d'inverno?",
    ],
  },
  {
    gruppo: "Regola più dati",
    strada: "entrambi",
    domande: [
      "Quali vettori sono sotto l'obiettivo di puntualità previsto dal manuale?",
      "Quanto sale da disgelo dobbiamo avere a scorta entro il 31 ottobre?",
    ],
  },
];

const nuovoId = () => crypto.randomUUID();

function strada(traccia = []) {
  const usati = new Set(traccia.map((t) => t.strumento));
  if (usati.has("cerca_manuale") && usati.has("analizza_dati")) return "entrambi";
  if (usati.has("cerca_manuale")) return "manuale";
  if (usati.has("analizza_dati")) return "dati";
  return null;
}

const ETICHETTE = {
  manuale: "Risposta dal manuale",
  dati: "Risposta dall'analisi dei dati",
  entrambi: "Manuale e analisi dei dati",
};

function StatoServizi() {
  const [stato, setStato] = useState(null);
  useEffect(() => {
    const controlla = () =>
      fetch("/api/health")
        .then((r) => r.json())
        .then(setStato)
        .catch(() => setStato({ backend: false, chroma: false, dataAgent: false }));
    controlla();
    // se un servizio non risponde (per esempio si sta ancora avviando) si ricontrolla piu' spesso
    const t = setInterval(controlla, stato && (!stato.chroma || !stato.dataAgent) ? 10000 : 30000);
    return () => clearInterval(t);
  }, [stato?.chroma, stato?.dataAgent]);
  if (!stato) return null;
  const voci = [
    ["Manuale", stato.chroma],
    ["Dati", stato.dataAgent],
  ];
  return (
    <ul className="servizi" aria-label="Stato dei servizi">
      {voci.map(([nome, ok]) => (
        <li key={nome} className={ok ? "ok" : "ko"} title={ok ? `${nome}: disponibile` : `${nome}: non raggiungibile`}>
          <span className="spia" aria-hidden="true" />
          {nome}
          <span className="sr-only">{ok ? " disponibile" : " non raggiungibile"}</span>
        </li>
      ))}
    </ul>
  );
}

function Traccia({ traccia, daCache, requestId }) {
  if (!traccia?.length) return null;
  return (
    <details className="traccia">
      <summary>Come ho risposto</summary>
      {daCache && <p className="tenue">Domanda già vista: risposta servita dalla cache, senza ripetere l'analisi.</p>}
      <ol>
        {traccia.map((t, i) => (
          <li key={i}>
            {t.strumento === "cerca_manuale" && (
              <>
                <p>
                  <strong>Ricerca nel manuale:</strong> “{t.input}”
                </p>
                {t.fonti?.length > 0 && (
                  <ul className="fonti">
                    {t.fonti.map((f, j) => (
                      <li key={j}>
                        Cap. {f.sezione} – {f.titolo} <span className="tenue">(pertinenza {f.somiglianza})</span>
                      </li>
                    ))}
                  </ul>
                )}
                {t.fonti?.length === 0 && <p className="tenue">Nessun paragrafo abbastanza pertinente: il manuale non è stato citato.</p>}
                {t.scartate?.length > 0 && (
                  <p className="tenue">
                    Scartati perché sotto la soglia di pertinenza {t.soglia}: {t.scartate.map((f) => `cap. ${f.sezione} (${f.somiglianza})`).join(", ")}.
                  </p>
                )}
              </>
            )}
            {t.strumento === "analizza_dati" && (
              <>
                <p>
                  <strong>Analisi delegata al data agent:</strong> “{t.input}”
                </p>
                {t.daCache && <p className="tenue">Analisi già eseguita in precedenza: risultato dalla cache del data agent.</p>}
                {t.autocorrezioni > 0 && (
                  <p className="tenue">
                    Il codice è andato in errore {t.autocorrezioni} {t.autocorrezioni === 1 ? "volta" : "volte"} ed è stato corretto in automatico.
                  </p>
                )}
                {t.passi?.map((p, j) => {
                  if (p.tipo === "profilo") {
                    const e = p.esito;
                    const impossibili = (e.quantita_negative ?? 0) + (e.prezzi_fuori_scala ?? 0) + (e.consegne_prima_dell_ordine ?? 0);
                    return (
                      <p key={j} className="tenue">
                        Diagnosi del CSV all'avvio del servizio (schema {e.validazione_schema?.valido === false ? "non valido" : "valido"}): {e.duplicati_esatti} duplicati, {e.date_ordine_formato_gg_mm_aaaa} date in formato diverso,{" "}
                        {e.linee_prodotto_scritte_in_modo_non_standard + e.regioni_scritte_in_modo_non_standard} testi incoerenti, {impossibili} valori impossibili.
                      </p>
                    );
                  }
                  if (p.tipo === "pulizia") {
                    const nomi = { rimuovi_duplicati: "duplicati rimossi", normalizza_testi: "testi uniformati", neutralizza_valori_impossibili: "valori impossibili neutralizzati" };
                    const fatte = (p.esito.operazioni_applicate ?? []).map((o) => nomi[o] ?? o);
                    return (
                      <p key={j} className="tenue">
                        Pulizia scelta in base alla diagnosi, eseguita una volta all'avvio: {["date e numeri convertiti", ...fatte].join(", ")}.
                      </p>
                    );
                  }
                  return (
                    <details key={j} className={`codice ${p.ok ? "" : "errore"}`}>
                      <summary>
                        {p.ok ? "Calcolo" : "Tentativo con errore"}: {p.scopo}
                      </summary>
                      <pre>{p.codice}</pre>
                      {!p.ok && <pre className="msg-errore">{p.errore}</pre>}
                    </details>
                  );
                })}
              </>
            )}
            {t.errore && <p className="msg-errore">Errore: {t.errore}</p>}
          </li>
        ))}
      </ol>
      {requestId && <p className="tenue id-richiesta">ID richiesta: {requestId}</p>}
    </details>
  );
}

function Messaggio({ m }) {
  if (m.ruolo === "utente") {
    return (
      <div className="msg utente">
        <p>{m.testo}</p>
      </div>
    );
  }
  const s = strada(m.traccia);
  return (
    <article className={`msg assistente ${s ?? ""} ${m.errore ? "fallito" : ""}`}>
      {s && <p className="strada">{ETICHETTE[s]}</p>}
      <div className="testo">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.testo}</ReactMarkdown>
      </div>
      {m.grafici?.map((g) => (
        <figure key={g.nome} className="grafico">
          <a href={g.url} target="_blank" rel="noreferrer" title="Apri il grafico a piena grandezza">
            <img src={g.url} alt="Grafico generato dal data agent" loading="lazy" />
          </a>
        </figure>
      ))}
      <Traccia traccia={m.traccia} daCache={m.daCache} requestId={m.requestId} />
    </article>
  );
}

function InAttesa({ inizio }) {
  const [secondi, setSecondi] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSecondi(Math.round((Date.now() - inizio) / 1000)), 1000);
    return () => clearInterval(t);
  }, [inizio]);
  return (
    <div className="msg assistente attesa" role="status" aria-live="polite">
      <span className="puntini" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      SupplyBot sta ragionando{secondi > 3 ? ` · ${secondi} s` : ""}
      {secondi > 15 && <span className="tenue"> – le analisi sui dati richiedono più tempo</span>}
    </div>
  );
}

export default function App() {
  const [sessione, setSessione] = useState(nuovoId);
  const [messaggi, setMessaggi] = useState([]);
  const [bozza, setBozza] = useState("");
  const [inizioAttesa, setInizioAttesa] = useState(null);
  const fondo = useRef(null);
  const campo = useRef(null);

  useEffect(() => {
    fondo.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messaggi, inizioAttesa]);

  async function invia(testo) {
    const domanda = testo.trim();
    if (!domanda || inizioAttesa) return;
    setMessaggi((m) => [...m, { ruolo: "utente", testo: domanda }]);
    setBozza("");
    setInizioAttesa(Date.now());
    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId: sessione, message: domanda }),
      });
      const dati = await r.json().catch(() => ({}));
      if (r.status === 429) {
        // limite di domande raggiunto (protezione dei costi): messaggio chiaro, nessun consiglio tecnico
        setMessaggi((m) => [...m, { ruolo: "assistente", errore: true, testo: `**Limite raggiunto.** ${dati.error}` }]);
        return;
      }
      if (!r.ok) throw new Error(dati.error || `Errore ${r.status}`);
      setMessaggi((m) => [
        ...m,
        { ruolo: "assistente", testo: dati.reply, grafici: dati.charts, traccia: dati.trace, daCache: dati.cached, requestId: dati.requestId },
      ]);
    } catch (e) {
      setMessaggi((m) => [
        ...m,
        {
          ruolo: "assistente",
          errore: true,
          testo: `**La richiesta non è andata a buon fine.** ${e.message}\n\nControlla che i servizi siano avviati (indicatori in alto) e riprova.`,
        },
      ]);
    } finally {
      setInizioAttesa(null);
      campo.current?.focus();
    }
  }

  function nuovaConversazione() {
    fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sessionId: sessione }) });
    setSessione(nuovoId());
    setMessaggi([]);
    campo.current?.focus();
  }

  return (
    <div className="app">
      <header className="testata">
        <div className="marchio">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <path d="M16 3 29 16 16 29 3 16z" />
          </svg>
          <div>
            <h1>SupplyBot</h1>
            <p>Assistente del reparto Supply Chain di Atisale</p>
          </div>
        </div>
        <div className="azioni">
          <StatoServizi />
          {messaggi.length > 0 && (
            <button type="button" className="secondario" onClick={nuovaConversazione} disabled={!!inizioAttesa}>
              Nuova conversazione
            </button>
          )}
        </div>
      </header>

      <main className="conversazione">
        {messaggi.length === 0 ? (
          <section className="benvenuto">
            <h2>Chiedi una regola del manuale o un numero delle spedizioni.</h2>
            <p>
              SupplyBot decide da solo dove cercare: nel manuale operativo per procedure e soglie, nei dati delle spedizioni
              (gennaio 2025 – settembre 2026) per volumi, puntualità e grafici. Se serve, usa entrambi.
            </p>
            <div className="gruppi">
              {SUGGERIMENTI.map((g) => (
                <div key={g.gruppo} className={`gruppo ${g.strada}`}>
                  <h3>{g.gruppo}</h3>
                  {g.domande.map((d) => (
                    <button key={d} type="button" onClick={() => invia(d)}>
                      {d}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </section>
        ) : (
          messaggi.map((m, i) => <Messaggio key={i} m={m} />)
        )}
        {inizioAttesa && <InAttesa inizio={inizioAttesa} />}
        <div ref={fondo} />
      </main>

      <form
        className="compositore"
        onSubmit={(e) => {
          e.preventDefault();
          invia(bozza);
        }}
      >
        <label htmlFor="domanda" className="sr-only">
          Scrivi una domanda
        </label>
        <textarea
          id="domanda"
          ref={campo}
          rows={1}
          maxLength={1000}
          value={bozza}
          placeholder="Scrivi una domanda…"
          title="Invio per inviare, Maiusc+Invio per andare a capo"
          onChange={(e) => setBozza(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              invia(bozza);
            }
          }}
          autoFocus
        />
        <button type="submit" disabled={!bozza.trim() || !!inizioAttesa}>
          Invia
        </button>
      </form>
      <p className="avviso">Caso didattico: manuale e dati sono simulati e non descrivono l'organizzazione reale di Atisale.</p>
    </div>
  );
}
