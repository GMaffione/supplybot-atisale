"""
Pulizia deterministica del dataset spedizioni_clienti.csv.

L'agente prima DIAGNOSTICA il CSV grezzo (`profila_dataset`), poi DECIDE quali
operazioni di pulizia applicare (`pulisci_dataset`); il COME di ogni operazione
e' scritto qui, in codice testato, cosi' le regole di pulizia sono
sempre le stesse e verificabili. Ogni correzione viene contata nel report,
che l'agente usa per spiegare all'utente cosa ha sistemato.
"""

from pathlib import Path

import numpy as np
import pandas as pd

CSV_PREDEFINITO = Path(__file__).resolve().parent.parent / "data" / "spedizioni_clienti.csv"

REGIONI_CANONICHE = {
    "emilia romagna": "Emilia-Romagna", "er": "Emilia-Romagna", "emilia-romagna": "Emilia-Romagna",
    "friuli venezia giulia": "Friuli-Venezia Giulia", "fvg": "Friuli-Venezia Giulia",
    "friuli-venezia giulia": "Friuli-Venezia Giulia",
    "lombardia": "Lombardia",
    "trentino alto adige": "Trentino-Alto Adige", "trentino-alto adige": "Trentino-Alto Adige",
}

LINEE_CANONICHE = ["Alimentare", "Industriale", "Addolcimento acqua", "Disgelo stradale", "Zootecnia", "Piscine"]


def carica_grezzo(percorso=CSV_PREDEFINITO) -> pd.DataFrame:
    """Legge il CSV cosi' com'e', tutto come testo: nessuna conversione automatica."""
    return pd.read_csv(percorso, dtype=str, keep_default_na=False)


def _date_miste(serie: pd.Series) -> tuple[pd.Series, int]:
    """
    Converte date scritte in due formati: aaaa-mm-gg e gg/mm/aaaa.
    Ogni formato si legge separatamente: con un parser unico "dayfirst"
    la data 2025-01-07 verrebbe letta come 1 luglio (bug trovato in fase di test).
    """
    s = serie.str.strip().replace("", np.nan)
    iso = pd.to_datetime(s.where(s.str.contains("-", na=False)), format="%Y-%m-%d", errors="coerce")
    ita = pd.to_datetime(s.where(s.str.contains("/", na=False)), format="%d/%m/%Y", errors="coerce")
    return iso.fillna(ita), int(s.str.contains("/", na=False).sum())


def _numero(serie: pd.Series) -> tuple[pd.Series, int]:
    """Converte numeri scritti come '85,40' o '€ 85.40' in float."""
    s = serie.str.strip()
    sporchi = int(s.str.contains(r"[€,]", regex=True).sum())
    s = s.str.replace("€", "", regex=False).str.replace(",", ".", regex=False).str.strip()
    return pd.to_numeric(s.replace("", np.nan), errors="coerce"), sporchi


OPERAZIONI = {
    "rimuovi_duplicati": "elimina le righe identiche ripetute",
    "normalizza_testi": "uniforma linee di prodotto e regioni scritte in modi diversi",
    "neutralizza_valori_impossibili": "rende mancanti quantita' negative, prezzi fuori scala e consegne prima dell'ordine",
}


def _converti(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Conversione dei tipi: date e numeri da testo. Sempre necessaria, altrimenti nessun calcolo e' possibile."""
    df = df.copy()
    rep = {}
    df["order_date"], rep["date_ordine_formato_gg_mm_aaaa"] = _date_miste(df["order_date"])
    df["requested_date"], _ = _date_miste(df["requested_date"])
    df["delivery_date"], _ = _date_miste(df["delivery_date"])
    df["price_eur_t"], rep["prezzi_con_virgola_o_simbolo_euro"] = _numero(df["price_eur_t"])
    for col in ["qty_t", "qty_delivered_t", "freight_cost_eur"]:
        df[col], _ = _numero(df[col])
    return df, rep


def _maschere_impossibili(df: pd.DataFrame) -> dict:
    linea = df["product_line"].str.strip().str.lower()
    mediana = df.groupby(linea)["price_eur_t"].transform("median")
    return {
        "quantita_negative": df["qty_t"] < 0,
        "prezzi_fuori_scala": df["price_eur_t"] > 10 * mediana,  # errore di battitura x100
        "consegne_prima_dell_ordine": df["delivery_date"] < df["order_date"],
    }


def profila_dataset(df_grezzo: pd.DataFrame) -> dict:
    """
    Diagnosi del CSV grezzo, SENZA modificarlo: quali anomalie ci sono e quante.
    E' quello che l'agente legge per decidere quali operazioni di pulizia applicare.
    """
    linea = df_grezzo["product_line"].str.strip().str.lower()
    canoniche = {l.lower(): l for l in LINEE_CANONICHE}
    reg = df_grezzo["customer_region"].str.strip()
    convertito, conv = _converti(df_grezzo)
    impossibili = {k: int(v.sum()) for k, v in _maschere_impossibili(convertito).items()}
    vuoti = {c: int((df_grezzo[c].str.strip() == "").sum()) for c in df_grezzo.columns}
    profilo = {
        "righe": len(df_grezzo),
        "duplicati_esatti": int(df_grezzo.duplicated().sum()),
        "linee_prodotto_scritte_in_modo_non_standard": int((df_grezzo["product_line"] != linea.map(canoniche)).sum()),
        "regioni_scritte_in_modo_non_standard": int((reg.str.lower().map(REGIONI_CANONICHE).fillna(reg) != df_grezzo["customer_region"]).sum()),
        **conv,
        **{f"{k}": v for k, v in impossibili.items()},
        "valori_mancanti_per_colonna": {c: n for c, n in vuoti.items() if n},
        "operazioni_disponibili": OPERAZIONI,
        "nota": "La conversione di date e numeri viene sempre applicata. I valori mancanti non vengono mai inventati.",
    }
    return profilo


def pulisci_dati(df_grezzo: pd.DataFrame, operazioni=None) -> tuple[pd.DataFrame, dict]:
    """
    Restituisce (dataframe pulito, report). `operazioni` = elenco scelto dall'agente tra OPERAZIONI;
    None = tutte. La conversione dei tipi e' sempre applicata.
    """
    scelte = list(OPERAZIONI) if operazioni is None else [o for o in operazioni if o in OPERAZIONI]
    df = df_grezzo.copy()
    report = {"righe_iniziali": len(df), "operazioni_applicate": scelte,
              "operazioni_non_applicate": [o for o in OPERAZIONI if o not in scelte]}

    # 1. duplicati esatti
    if "rimuovi_duplicati" in scelte:
        report["duplicati_rimossi"] = int(df.duplicated().sum())
        df = df.drop_duplicates().reset_index(drop=True)

    # 2. testi: linee di prodotto e regioni scritte in modi diversi
    if "normalizza_testi" in scelte:
        linea = df["product_line"].str.strip().str.lower()
        mappa_linee = {l.lower(): l for l in LINEE_CANONICHE}
        report["linee_prodotto_normalizzate"] = int((df["product_line"] != linea.map(mappa_linee)).sum())
        df["product_line"] = linea.map(mappa_linee).fillna(df["product_line"].str.strip())
        reg = df["customer_region"].str.strip()
        nuova = reg.str.lower().map(REGIONI_CANONICHE).fillna(reg)
        report["regioni_normalizzate"] = int((nuova != df["customer_region"]).sum())
        df["customer_region"] = nuova

    # 3-4. date in formati misti e numeri scritti come testo (sempre)
    df, conv = _converti(df)
    report.update(conv)

    # 5. valori fuori range -> marcati come mancanti (non si inventano valori)
    if "neutralizza_valori_impossibili" in scelte:
        m = _maschere_impossibili(df)
        report["quantita_negative_annullate"] = int(m["quantita_negative"].sum())
        df.loc[m["quantita_negative"], ["qty_t", "qty_delivered_t"]] = np.nan
        report["prezzi_fuori_scala_annullati"] = int(m["prezzi_fuori_scala"].sum())
        df.loc[m["prezzi_fuori_scala"], "price_eur_t"] = np.nan
        report["consegne_prima_dell_ordine_annullate"] = int(m["consegne_prima_dell_ordine"].sum())
        df.loc[m["consegne_prima_dell_ordine"], "delivery_date"] = pd.NaT

    # 6. valori mancanti rimasti (contati, non riempiti)
    consegnata = df["status"] == "Consegnata"
    report["consegnate_senza_data_consegna"] = int((consegnata & df["delivery_date"].isna()).sum())
    report["prezzi_mancanti"] = int(df["price_eur_t"].isna().sum())

    # 7. colonne derivate utili alle analisi
    df["revenue_eur"] = df["qty_t"] * df["price_eur_t"]
    valutabile = consegnata & df["delivery_date"].notna() & df["qty_t"].notna()
    df["on_time"] = np.where(valutabile, df["delivery_date"] <= df["requested_date"], np.nan)
    df["in_full"] = np.where(valutabile, df["qty_delivered_t"] >= df["qty_t"], np.nan)
    df["otif"] = np.where(valutabile, (df["on_time"] == 1) & (df["in_full"] == 1), np.nan)
    df["delay_days"] = (df["delivery_date"] - df["requested_date"]).dt.days

    report["righe_finali"] = len(df)
    report["note"] = ("Le righe con valori impossibili non sono state eliminate: il valore errato "
                      "e' stato reso mancante. OTIF calcolato solo su spedizioni consegnate con dati completi.")
    return df, report


if __name__ == "__main__":
    import json
    print(json.dumps(profila_dataset(carica_grezzo()), indent=1, ensure_ascii=False))
    pulito, rep = pulisci_dati(carica_grezzo())
    for k, v in rep.items():
        print(f"{k}: {v}")
    print(pulito.dtypes)
