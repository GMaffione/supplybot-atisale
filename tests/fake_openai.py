"""
Finto server compatibile con l'API OpenAI, SOLO per i test automatici.

Permette di verificare l'intera catena (front end -> back end -> RAG / data agent)
senza chiave e senza costi. Le risposte sono scritte a regole: non e' un LLM,
imita soltanto le sue decisioni (quale strumento chiamare, con quali argomenti).

Avvio:  python tests/fake_openai.py   (porta 9999)
Poi impostare OPENAI_BASE_URL=http://localhost:9999/v1 e OPENAI_API_KEY=test
"""

import hashlib
import json
import math
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = 9999
DIM = 256

CODICE = {
    "disgelo": '''m = df[(df.product_line == "Disgelo stradale") & (df.order_date >= "2025-11-01") & (df.order_date < "2026-03-01")]
mese = m.groupby(m.order_date.dt.to_period("M"))["qty_t"].sum()
print(mese.round(1).to_string())
print("Totale inverno 2025-26:", round(mese.sum(), 1), "t")
fig, ax = plt.subplots()
ax.bar(mese.index.strftime("%b %Y"), mese.values, color="#2a78d6", width=0.6)
ax.set_ylabel("Tonnellate spedite (t)")
salva_grafico("Il disgelo tocca il picco a gennaio 2026")''',
    "otif": '''v = df[df.otif.notna() & (df.order_date.dt.year == 2026)].groupby("carrier")["otif"].agg(["mean", "count"])
v["mean"] = (v["mean"] * 100).round(1)
print(v.sort_values("mean").to_string())
fig, ax = plt.subplots()
s = v.sort_values("mean")
ax.barh(s.index, s["mean"], color="#2a78d6")
ax.axvline(95, color="#52514e", linestyle="--", linewidth=1)
ax.set_xlabel("OTIF 2026 (%)")
salva_grafico("OTIF 2026 per vettore rispetto all'obiettivo del 95%")''',
    "errore": '''print(df.groupby("vettore")["otif"].mean())''',
}


def embedding(testo):
    """Vettore deterministico 'bag of words' con hashing: abbastanza per testare il retrieval."""
    v = [0.0] * DIM
    for parola in re.findall(r"[a-zà-ù0-9]{3,}", testo.lower()):
        h = int(hashlib.md5(parola[:6].encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def chiamata(nome, argomenti):
    return {"id": f"call_{int(time.time()*1000) % 10**8}_{nome}", "type": "function",
            "function": {"name": nome, "arguments": json.dumps(argomenti, ensure_ascii=False)}}


def risposta_data_agent(messaggi):
    domanda = next(m["content"] for m in messaggi if m["role"] == "user").lower()
    tool = [m for m in messaggi if m["role"] == "tool"]
    if not tool:
        return None, [chiamata("profila_dataset", {})]
    ultimo_nome = [m for m in messaggi if m["role"] == "assistant" and m.get("tool_calls")][-1]["tool_calls"][0]["function"]["name"]
    if ultimo_nome == "profila_dataset":
        return None, [chiamata("pulisci_dataset", {"operazioni": ["rimuovi_duplicati", "normalizza_testi",
                                                                   "neutralizza_valori_impossibili"]})]
    ultimo = tool[-1]["content"]
    codici_eseguiti = sum(1 for m in messaggi if m["role"] == "assistant" and m.get("tool_calls")
                          and m["tool_calls"][0]["function"]["name"] == "esegui_python")
    chiave = "disgelo" if "disgelo" in domanda else "otif"
    if codici_eseguiti == 0:
        primo = "errore" if "test errore" in domanda else chiave
        return None, [chiamata("esegui_python", {"scopo": f"Calcolo per la domanda ({primo})", "codice": CODICE[primo]})]
    if ultimo.startswith("ERRORE") and codici_eseguiti < 3:
        return None, [chiamata("esegui_python", {"scopo": "Correggo il nome della colonna (carrier)", "codice": CODICE["otif"]})]
    righe = ultimo.strip().splitlines()
    return ("Risultato dell'analisi (risposta simulata dal server di test):\n" + "\n".join(righe[:12])), None


def risposta_orchestratore(messaggi):
    utente = [m for m in messaggi if m["role"] == "user"][-1]["content"].lower()
    ultimi_tool = []
    for m in reversed(messaggi):
        if m["role"] == "user":
            break
        if m["role"] == "tool":
            ultimi_tool.append(m)
    nomi_chiamati = [tc["function"]["name"] for m in messaggi[messaggi.index([x for x in messaggi if x["role"] == "user"][-1]):]
                     if m["role"] == "assistant" and m.get("tool_calls") for tc in m["tool_calls"]]

    vuole_manuale = any(k in utente for k in ["manuale", "procedura", "entro quanto", "regola", "policy", "obiettivo", "soglia", "scorta"])
    vuole_dati = any(k in utente for k in ["andat", "volumi", "quali vettori", "quanto", "mese", "grafico", "sotto"]) and "entro quanto" not in utente
    if "e per" in utente or "e rispetto" in utente:
        vuole_dati, vuole_manuale = True, False

    if vuole_manuale and "cerca_manuale" not in nomi_chiamati:
        return None, [chiamata("cerca_manuale", {"query": utente})]
    if vuole_dati and "analizza_dati" not in nomi_chiamati:
        contesto = ultimi_tool[0]["content"][:300] if ultimi_tool else ""
        domanda = utente if not contesto else f"{utente} (regola dal manuale: {contesto})"
        return None, [chiamata("analizza_dati", {"domanda": domanda})]
    testo = "\n\n".join(t["content"][:600] for t in reversed(ultimi_tool))
    return f"Risposta simulata dal server di test. Fonti usate:\n{testo}", None


class Gestore(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, codice, dati):
        corpo = json.dumps(dati).encode()
        self.send_response(codice)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self):
        dati = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/embeddings"):
            testi = dati["input"] if isinstance(dati["input"], list) else [dati["input"]]
            return self._json(200, {"object": "list", "model": dati["model"],
                                    "data": [{"object": "embedding", "index": i, "embedding": embedding(t)}
                                             for i, t in enumerate(testi)],
                                    "usage": {"prompt_tokens": 0, "total_tokens": 0}})
        if self.path.endswith("/chat/completions"):
            # imita il rifiuto reale di alcuni modelli: strumenti ammessi solo con reasoning_effort="none"
            if os.getenv("FAKE_RICHIEDI_REASONING_NONE") and dati.get("tools") and dati.get("reasoning_effort") != "none":
                return self._json(400, {"error": {"message": "Function tools with reasoning_effort are not supported for "
                                                  f"{dati['model']} in /v1/chat/completions. To use function tools, use "
                                                  "/v1/responses or set reasoning_effort to 'none'.",
                                                  "type": "invalid_request_error", "param": "reasoning_effort"}})
            messaggi = dati["messages"]
            sistema = messaggi[0]["content"]
            testo, chiamate = (risposta_data_agent(messaggi) if "Data Agent" in sistema
                               else risposta_orchestratore(messaggi))
            msg = {"role": "assistant", "content": testo}
            if chiamate:
                msg["tool_calls"] = chiamate
            return self._json(200, {"id": "chatcmpl-test", "object": "chat.completion", "created": int(time.time()),
                                    "model": dati["model"],
                                    "choices": [{"index": 0, "message": msg,
                                                 "finish_reason": "tool_calls" if chiamate else "stop"}],
                                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
        self._json(404, {"error": {"message": "endpoint non simulato"}})


if __name__ == "__main__":
    print(f"Finto server OpenAI su http://localhost:{PORTA}/v1")
    ThreadingHTTPServer(("127.0.0.1", PORTA), Gestore).serve_forever()
