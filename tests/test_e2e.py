"""
Test end-to-end dell'architettura: front end escluso, tutto il resto incluso
(back end Node -> ChromaDB / data agent Python -> grafici).

Verifica che per ogni tipo di domanda l'orchestratore scelga gli strumenti attesi,
che il data agent diagnostichi e pulisca i dati prima di calcolare, che i grafici
siano raggiungibili e che la memoria della sessione funzioni.

Uso (con i servizi avviati):
    python tests/test_e2e.py                      # contro il back end su localhost:3001
Per un test senza chiave e senza costi, avviare i servizi con il finto server:
    python tests/fake_openai.py   e   OPENAI_BASE_URL=http://localhost:9999/v1
Solo libreria standard: nessuna dipendenza da installare.
"""

import json
import sys
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3001"
SESSIONE = "test-" + uuid.uuid4().hex[:12]

CASI = [
    ("Un comune chiede sale da disgelo per un'allerta neve: entro quanto va servito?", ["cerca_manuale"], False),
    ("Come sono andati i volumi di sale da disgelo nell'inverno 2025-26, mese per mese?", ["analizza_dati"], True),
    ("Quali vettori sono sotto l'obiettivo di puntualità previsto dal manuale?", ["cerca_manuale", "analizza_dati"], True),
    ("E per Sant'Antioco?", ["analizza_dati"], True),
]


def chiama(percorso, dati=None):
    corpo = json.dumps(dati).encode() if dati is not None else None
    req = urllib.request.Request(BASE + percorso, data=corpo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.status, r.read()


def main():
    esiti = []
    stato, corpo = chiama("/api/health")
    salute = json.loads(corpo)
    esiti.append(("Servizi raggiungibili (Chroma e data agent)", stato == 200 and salute["chroma"] and salute["dataAgent"]))

    for domanda, attesi, vuole_grafico in CASI:
        _, corpo = chiama("/api/chat", {"sessionId": SESSIONE, "message": domanda})
        r = json.loads(corpo)
        usati = [t["strumento"] for t in r["trace"]]
        esiti.append((f"Strumenti per «{domanda[:45]}…»: {usati}", usati == attesi))
        for t in r["trace"]:
            if t["strumento"] == "analizza_dati":
                tipi = [p["tipo"] for p in t["passi"]]
                esiti.append(("  il data agent diagnostica e pulisce prima di calcolare",
                              tipi[:2] == ["profilo", "pulizia"] and "codice" in tipi))
        if vuole_grafico:
            ok = bool(r["charts"])
            if ok:
                stato, png = chiama(r["charts"][0]["url"])
                ok = stato == 200 and png[:4] == b"\x89PNG"
            esiti.append(("  grafico generato e scaricabile dal back end", ok))

    print(f"\nTest end-to-end su {BASE}\n")
    for descrizione, ok in esiti:
        print(f"[{'OK' if ok else 'KO'}] {descrizione}")
    falliti = sum(not ok for _, ok in esiti)
    print(f"\n{len(esiti) - falliti}/{len(esiti)} verifiche superate")
    sys.exit(1 if falliti else 0)


if __name__ == "__main__":
    main()
