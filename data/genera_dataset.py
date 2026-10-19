"""
Genera il dataset simulato spedizioni_clienti.csv per SupplyBot.

AVVISO: tutti i dati sono SIMULATI a scopo didattico. Il caso e' ispirato ad
Atisale S.p.A. solo per i fatti pubblici (due saline, linee di prodotto).
Clienti, vettori, volumi, prezzi e tempi sono inventati e non rappresentano
dati reali dell'azienda.

Riproducibilita': TUTTA la casualita' passa da un unico generatore numpy creato con un
seme esplicito (SEED, predefinito 42, modificabile con --seed). Nessun'altra fonte casuale
(niente random, uuid o data di sistema): stesso seme -> stesso file, byte per byte.
Lo script stampa l'impronta SHA-256 del CSV, per verificarlo a colpo d'occhio;
tests/test_data_agent.py lo controlla in automatico.

Struttura:
  1. si generano spedizioni "pulite" con stagionalita' e schemi realistici
  2. si inseriscono di proposito anomalie (dati sporchi) per testare il
     data cleaning dell'agente

Uso:  python genera_dataset.py                 (seme 42: ricrea spedizioni_clienti.csv)
      python genera_dataset.py --seed 7 --output altro.csv
"""

import argparse
import hashlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_argomenti = argparse.ArgumentParser(description="Genera il dataset simulato delle spedizioni.")
_argomenti.add_argument("--seed", type=int, default=42, help="seme del generatore casuale (predefinito 42)")
_argomenti.add_argument("--output", type=Path, default=Path(__file__).parent / "spedizioni_clienti.csv")
ARGOMENTI = _argomenti.parse_args()

SEED = ARGOMENTI.seed                  # seme esplicito: l'unica fonte di casualita' dello script
rng = np.random.default_rng(SEED)      # generatore usato da TUTTE le estrazioni casuali qui sotto

INIZIO = date(2025, 1, 1)
FINE = date(2026, 9, 30)
OGGI = date(2026, 10, 1)  # data di estrazione del dataset

OUTPUT = ARGOMENTI.output

# ---------------------------------------------------------------------------
# 1. Anagrafiche (inventate)
# ---------------------------------------------------------------------------

SITI = ["Margherita di Savoia", "Sant'Antioco"]

# linea di prodotto -> prezzo medio EUR/t, deviazione, tonnellate medie per spedizione
LINEE = {
    "Alimentare":         {"prezzo": 310, "sd": 40, "qty": 18},
    "Industriale":        {"prezzo": 85,  "sd": 10, "qty": 28},
    "Addolcimento acqua": {"prezzo": 215, "sd": 20, "qty": 16},
    "Disgelo stradale":   {"prezzo": 78,  "sd": 8,  "qty": 30},
    "Zootecnia":          {"prezzo": 135, "sd": 15, "qty": 10},
    "Piscine":            {"prezzo": 185, "sd": 18, "qty": 12},
}

# peso mensile della domanda (gen..dic) per linea: crea la stagionalita'
STAGIONALITA = {
    "Alimentare":         [1.0, 0.9, 1.0, 1.0, 1.0, 1.0, 0.9, 0.7, 1.1, 1.1, 1.2, 1.3],
    "Industriale":        [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 1.0, 1.0, 1.0, 0.9],
    "Addolcimento acqua": [1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1, 0.8, 1.0, 1.0, 1.0, 0.9],
    "Disgelo stradale":   [4.0, 3.0, 0.8, 0.1, 0.0, 0.0, 0.0, 0.1, 0.8, 1.8, 3.2, 4.0],
    "Zootecnia":          [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    "Piscine":            [0.1, 0.3, 1.5, 2.8, 3.0, 2.0, 0.8, 0.3, 0.1, 0.1, 0.1, 0.1],
}

# numero medio di spedizioni al mese per linea (prima della stagionalita')
BASE_MESE = {
    "Alimentare": 21, "Industriale": 16, "Addolcimento acqua": 10,
    "Disgelo stradale": 10, "Zootecnia": 5, "Piscine": 6,
}

# canali ammessi per linea, con probabilita'
CANALI = {
    "Alimentare":         (["GDO", "Industria", "Horeca", "Export"], [0.45, 0.25, 0.15, 0.15]),
    "Industriale":        (["Industria", "Export"], [0.85, 0.15]),
    "Addolcimento acqua": (["GDO", "Industria"], [0.7, 0.3]),
    "Disgelo stradale":   (["Enti pubblici", "Industria"], [0.85, 0.15]),
    "Zootecnia":          (["Industria", "GDO"], [0.7, 0.3]),
    "Piscine":            (["GDO", "Horeca"], [0.75, 0.25]),
}

REGIONI_NORD = ["Lombardia", "Piemonte", "Veneto", "Emilia-Romagna",
                "Trentino-Alto Adige", "Friuli-Venezia Giulia", "Valle d'Aosta"]
REGIONI_CENTRO_SUD = ["Toscana", "Marche", "Umbria", "Lazio", "Abruzzo",
                      "Molise", "Campania", "Puglia", "Basilicata", "Calabria", "Sicilia"]
REGIONI_TIRRENO = ["Liguria", "Toscana", "Lazio", "Campania"]
EXPORT_PAESI = ["Francia", "Grecia", "Albania", "Malta", "Croazia"]

# lead time standard (giorni) per canale
LEAD_TIME = {"GDO": 7, "Industria": 10, "Horeca": 6, "Export": 18, "Enti pubblici": 6}
LEAD_TIME_URGENTE = 2  # emergenza neve: entro 48 ore (vedi manuale, cap. 4)

# vettori inventati: modalita', siti serviti, tariffa EUR/t, probabilita' di ritardo base
VETTORI = {
    "Autotrasporti Gargano":     {"modo": "Camion", "siti": ["Margherita di Savoia"], "tariffa": 34, "p_ritardo": 0.03},
    "Adriatica Logistica":       {"modo": "Camion", "siti": ["Margherita di Savoia"], "tariffa": 31, "p_ritardo": 0.04},
    "Logistica Padana Express":  {"modo": "Camion", "siti": ["Margherita di Savoia"], "tariffa": 29, "p_ritardo": 0.035},
    "Ferrocargo Sud":            {"modo": "Treno",  "siti": ["Margherita di Savoia"], "tariffa": 19, "p_ritardo": 0.05},
    "Sulcis Trasporti":          {"modo": "Camion", "siti": ["Sant'Antioco"], "tariffa": 22, "p_ritardo": 0.03},
    "Mare Sardo Shipping":       {"modo": "Nave + camion", "siti": ["Sant'Antioco"], "tariffa": 52, "p_ritardo": 0.05},
}

# ---------------------------------------------------------------------------
# 2. Funzioni di supporto
# ---------------------------------------------------------------------------

def mesi():
    """Restituisce (anno, mese) dal gennaio 2025 al settembre 2026."""
    a, m = INIZIO.year, INIZIO.month
    while (a, m) <= (FINE.year, FINE.month):
        yield a, m
        m += 1
        if m == 13:
            a, m = a + 1, 1


def giorno_casuale(anno, mese):
    primo = date(anno, mese, 1)
    ultimo = (date(anno + (mese == 12), mese % 12 + 1, 1) - timedelta(days=1))
    return primo + timedelta(days=int(rng.integers(0, (ultimo - primo).days + 1)))


def scegli_sito(linea, canale):
    # Sant'Antioco pesa circa il 30%; piu' presente su alimentare (Fior di sale) ed export
    p_sa = 0.30
    if linea == "Alimentare":
        p_sa = 0.40
    if linea == "Disgelo stradale":
        p_sa = 0.20
    return "Sant'Antioco" if rng.random() < p_sa else "Margherita di Savoia"


def scegli_regione(sito, linea, canale):
    if canale == "Export":
        return str(rng.choice(EXPORT_PAESI))
    if sito == "Sant'Antioco":
        # meta' in Sardegna, meta' sulla costa tirrenica
        return "Sardegna" if rng.random() < 0.5 else str(rng.choice(REGIONI_TIRRENO))
    if linea == "Disgelo stradale":
        return str(rng.choice(REGIONI_NORD + ["Marche", "Abruzzo", "Toscana", "Molise"]))
    return str(rng.choice(REGIONI_NORD + REGIONI_CENTRO_SUD))


def scegli_vettore(sito, regione, linea, qty):
    if sito == "Sant'Antioco":
        return "Sulcis Trasporti" if regione == "Sardegna" else "Mare Sardo Shipping"
    # da Margherita di Savoia: il treno solo per grandi lotti di disgelo/industriale al nord
    if linea in ("Disgelo stradale", "Industriale") and regione in REGIONI_NORD and qty >= 28 and rng.random() < 0.45:
        return "Ferrocargo Sud"
    return str(rng.choice(["Autotrasporti Gargano", "Adriatica Logistica", "Logistica Padana Express"],
                          p=[0.35, 0.33, 0.32]))


def ritardo_giorni(vettore, sito, data_ordine, urgente):
    """Giorni di ritardo rispetto alla data richiesta (0 = puntuale)."""
    p = VETTORI[vettore]["p_ritardo"]
    # schema 1: Logistica Padana Express peggiora dal 2026
    if vettore == "Logistica Padana Express" and data_ordine >= date(2026, 1, 1):
        p = 0.24
    # schema 2: maltempo in inverno blocca le navi da Sant'Antioco
    if vettore == "Mare Sardo Shipping" and data_ordine.month in (12, 1, 2):
        p = 0.35
    # le urgenze sono piu' difficili da rispettare
    if urgente:
        p += 0.06
    if rng.random() < p:
        massimo = 9 if vettore == "Mare Sardo Shipping" else 5
        return int(rng.integers(1, massimo + 1))
    return 0


# ---------------------------------------------------------------------------
# 3. Generazione delle spedizioni pulite
# ---------------------------------------------------------------------------

righe = []
for anno, mese in mesi():
    for linea, base in BASE_MESE.items():
        peso = STAGIONALITA[linea][mese - 1]
        # schema 3: ondata di freddo a gennaio 2026 -> +60% di disgelo
        if linea == "Disgelo stradale" and (anno, mese) == (2026, 1):
            peso *= 1.6
        n = rng.poisson(base * peso)
        for _ in range(n):
            canali, prob = CANALI[linea]
            canale = str(rng.choice(canali, p=prob))
            sito = scegli_sito(linea, canale)
            regione = scegli_regione(sito, linea, canale)
            qty = max(1.0, round(rng.normal(LINEE[linea]["qty"], LINEE[linea]["qty"] * 0.3), 1))
            vettore = scegli_vettore(sito, regione, linea, qty)
            if canale == "Export" and sito == "Margherita di Savoia":
                vettore = "Adriatica Logistica"

            data_ordine = giorno_casuale(anno, mese)
            urgente = (linea == "Disgelo stradale" and canale == "Enti pubblici"
                       and data_ordine.month in (12, 1, 2) and rng.random() < 0.35)
            lead = LEAD_TIME_URGENTE if urgente else LEAD_TIME[canale] + int(rng.integers(-1, 3))
            richiesta = data_ordine + timedelta(days=lead)

            prezzo = round(rng.normal(LINEE[linea]["prezzo"], LINEE[linea]["sd"]), 2)
            tariffa = VETTORI[vettore]["tariffa"] * rng.uniform(0.9, 1.15)
            if urgente:
                tariffa *= 1.25  # le urgenze costano di piu'
            costo_trasporto = round(qty * tariffa, 2)

            rit = ritardo_giorni(vettore, sito, data_ordine, urgente)
            consegna = richiesta + timedelta(days=rit) - timedelta(days=int(rng.integers(0, 2)) if rit == 0 else 0)

            # consegna parziale: raro, piu' frequente quando c'e' ritardo
            p_parziale = 0.10 if rit > 0 else 0.01
            qty_cons = round(qty * rng.uniform(0.7, 0.95), 1) if rng.random() < p_parziale else qty

            if consegna >= OGGI:
                stato, consegna_out, qty_cons_out = "In transito", None, None
            elif rng.random() < 0.02:
                stato, consegna_out, qty_cons_out = "Annullata", None, None
            else:
                stato, consegna_out, qty_cons_out = "Consegnata", consegna, qty_cons

            p_reclamo = 0.02 + (0.10 if rit > 2 else 0) + (0.05 if qty_cons != qty else 0)
            reclamo = "Si" if (stato == "Consegnata" and rng.random() < p_reclamo) else "No"

            righe.append({
                "order_date": data_ordine,
                "site": sito,
                "product_line": linea,
                "channel": canale,
                "customer_region": regione,
                "priority": "Urgente" if urgente else "Standard",
                "qty_t": qty,
                "qty_delivered_t": qty_cons_out,
                "price_eur_t": prezzo,
                "transport_mode": VETTORI[vettore]["modo"],
                "carrier": vettore,
                "freight_cost_eur": costo_trasporto,
                "requested_date": richiesta,
                "delivery_date": consegna_out,
                "status": stato,
                "complaint": reclamo,
            })

df = pd.DataFrame(righe).sort_values("order_date").reset_index(drop=True)
df.insert(0, "shipment_id", [f"SP-{d.year}-{i:05d}" for i, d in enumerate(df["order_date"], start=1)])

# ---------------------------------------------------------------------------
# 4. Inserimento di anomalie volute (dati "sporchi")
# ---------------------------------------------------------------------------

# da qui in poi si lavora con stringhe, come in un vero export da gestionale
df = df.astype(object)
for col in ["order_date", "requested_date", "delivery_date"]:
    df[col] = df[col].map(lambda d: d.isoformat() if isinstance(d, date) else "")
consegnate = df.index[df["status"] == "Consegnata"].to_numpy()


def a_caso(indici, n):
    return rng.choice(indici, size=n, replace=False)


# 4a. valori mancanti
for i in a_caso(consegnate, 14):
    df.at[i, "delivery_date"] = ""
for i in a_caso(df.index.to_numpy(), 12):
    df.at[i, "price_eur_t"] = ""

# 4b. formati di data incoerenti (gg/mm/aaaa)
for i in a_caso(df.index.to_numpy(), 110):
    a, m, g = df.at[i, "order_date"].split("-")
    df.at[i, "order_date"] = f"{g}/{m}/{a}"

# 4c. prezzi scritti male: virgola decimale o simbolo dell'euro
for i in a_caso(df.index.to_numpy(), 70):
    p = df.at[i, "price_eur_t"]
    if p == "":
        continue
    df.at[i, "price_eur_t"] = f"{p:.2f}".replace(".", ",") if rng.random() < 0.5 else f"€ {p:.2f}"

# 4d. regioni scritte in modi diversi
VARIANTI = {"Emilia-Romagna": ["Emilia Romagna", "ER"],
            "Friuli-Venezia Giulia": ["Friuli Venezia Giulia", "FVG"],
            "Lombardia": ["lombardia", "LOMBARDIA"],
            "Trentino-Alto Adige": ["Trentino Alto Adige"]}
for i in df.index:
    r = df.at[i, "customer_region"]
    if r in VARIANTI and rng.random() < 0.25:
        df.at[i, "customer_region"] = str(rng.choice(VARIANTI[r]))

# 4e. linee di prodotto con maiuscole/spazi incoerenti
for i in a_caso(df.index.to_numpy(), 25):
    df.at[i, "product_line"] = str(df.at[i, "product_line"]).lower() + (" " if rng.random() < 0.5 else "")

# 4f. valori fuori range
for i in a_caso(consegnate, 6):
    df.at[i, "qty_t"] = -abs(float(df.at[i, "qty_t"]))            # tonnellate negative
for i in a_caso(df.index.to_numpy(), 3):
    p = df.at[i, "price_eur_t"]
    if isinstance(p, float):
        df.at[i, "price_eur_t"] = round(p * 100, 2)                 # prezzo x100 (errore di battitura)
for i in a_caso(consegnate, 4):
    od = df.at[i, "order_date"]
    if "-" in od and df.at[i, "delivery_date"]:
        d = date.fromisoformat(od) - timedelta(days=int(rng.integers(3, 15)))
        df.at[i, "delivery_date"] = d.isoformat()                   # consegna prima dell'ordine

# 4g. duplicati esatti
dup = df.loc[a_caso(df.index.to_numpy(), 20)]
df = pd.concat([df, dup]).sort_index(kind="stable").reset_index(drop=True)

df.to_csv(OUTPUT, index=False, encoding="utf-8")
impronta = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
print(f"Scritto {OUTPUT.name}: {len(df)} righe, {df.shape[1]} colonne (seme {SEED}, SHA-256 {impronta[:16]}...)")
