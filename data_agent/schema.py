"""
Contratto dei dati: quali colonne deve avere il CSV delle spedizioni, di che tipo,
con quali valori ammessi e in quali formati.

La validazione avviene in due momenti, all'avvio del data agent:

1. sul CSV GREZZO, prima di analizzarlo (`valida_grezzo`). Il dataset e' volutamente
   "sporco", quindi qui si distingue tra:
   - anomalie CORREGGIBILI (date in due formati, prezzi con la virgola, testi scritti
     in modi diversi, pochi valori impossibili): diventano AVVISI e ci pensa la pulizia;
   - problemi STRUTTURALI (colonne mancanti, file vuoto, colonne in gran parte
     illeggibili o con valori sconosciuti): sono ERRORI e il CSV viene rifiutato.
2. sul dataframe PULITO (`valida_pulito`), con regole rigide: tipi corretti, solo valori
   ammessi, niente quantita' negative o consegne prima dell'ordine. Se qualcosa non
   torna la pulizia ha un difetto, e anche questo e' un errore.

Se una delle due validazioni fallisce il servizio non analizza nulla e risponde con
l'elenco degli errori (vedi dataset.py e app.py), invece di dare numeri sbagliati.
"""

from dataclasses import dataclass, field

import pandas as pd

from pulizia import LINEE_CANONICHE, REGIONI_CANONICHE, _date_miste, _numero

# Oltre questa quota di valori illeggibili o sconosciuti una colonna e' considerata rotta:
# non si tratta piu' di qualche riga sporca, ma di un file diverso da quello atteso.
QUOTA_MASSIMA_ANOMALIE = 0.05

REGIONI_AMMESSE = {
    "Abruzzo", "Basilicata", "Calabria", "Campania", "Emilia-Romagna", "Friuli-Venezia Giulia", "Lazio",
    "Liguria", "Lombardia", "Marche", "Molise", "Piemonte", "Puglia", "Sardegna", "Sicilia", "Toscana",
    "Trentino-Alto Adige", "Umbria", "Valle d'Aosta", "Veneto",
    # destinazioni export
    "Albania", "Croazia", "Francia", "Grecia", "Malta",
}


@dataclass(frozen=True)
class Colonna:
    tipo: str                       # "id", "data", "numero", "categoria"
    obbligatoria: bool = True       # True: il valore non puo' mancare
    ammessi: frozenset = field(default_factory=frozenset)  # solo per "categoria"
    minimo: float | None = None     # solo per "numero"


SCHEMA: dict[str, Colonna] = {
    "shipment_id": Colonna("id"),
    "order_date": Colonna("data"),
    "site": Colonna("categoria", ammessi=frozenset({"Margherita di Savoia", "Sant'Antioco"})),
    "product_line": Colonna("categoria", ammessi=frozenset(LINEE_CANONICHE)),
    "channel": Colonna("categoria", ammessi=frozenset({"GDO", "Industria", "Enti pubblici", "Horeca", "Export"})),
    "customer_region": Colonna("categoria", ammessi=frozenset(REGIONI_AMMESSE)),
    "priority": Colonna("categoria", ammessi=frozenset({"Standard", "Urgente"})),
    "qty_t": Colonna("numero", minimo=0),
    "qty_delivered_t": Colonna("numero", obbligatoria=False, minimo=0),  # vuota se non ancora consegnata
    "price_eur_t": Colonna("numero", obbligatoria=False, minimo=0),
    "transport_mode": Colonna("categoria", ammessi=frozenset({"Camion", "Treno", "Nave + camion"})),
    "carrier": Colonna("categoria", ammessi=frozenset({
        "Adriatica Logistica", "Autotrasporti Gargano", "Ferrocargo Sud", "Logistica Padana Express",
        "Mare Sardo Shipping", "Sulcis Trasporti"})),
    "freight_cost_eur": Colonna("numero", minimo=0),
    "requested_date": Colonna("data"),
    "delivery_date": Colonna("data", obbligatoria=False),           # vuota se non ancora consegnata
    "status": Colonna("categoria", ammessi=frozenset({"Consegnata", "In transito", "Annullata"})),
    "complaint": Colonna("categoria", ammessi=frozenset({"Si", "No"})),
}

FORMATO_ID = r"^SP-\d{4}-\d{5}$"


class DatasetNonValido(Exception):
    """Il CSV non rispetta il contratto: contiene l'elenco degli errori trovati."""

    def __init__(self, fase: str, errori: list[str]):
        self.fase, self.errori = fase, errori
        super().__init__(f"Dataset non valido ({fase}): " + "; ".join(errori))


def _normalizza_categoria(nome: str, serie: pd.Series) -> pd.Series:
    """Le stesse regole della pulizia: un valore e' 'riconosciuto' se la pulizia sa ricondurlo a uno ammesso."""
    s = serie.str.strip()
    if nome == "product_line":
        return s.str.lower().map({l.lower(): l for l in LINEE_CANONICHE}).fillna(s)
    if nome == "customer_region":
        return s.str.lower().map(REGIONI_CANONICHE).fillna(s)
    return s


def valida_grezzo(df: pd.DataFrame) -> dict:
    """
    Controlla il CSV grezzo (tutto testo). Restituisce {"valido", "errori", "avvisi"}.
    Errori = il file non e' quello atteso; avvisi = anomalie che la pulizia sapra' correggere.
    """
    errori, avvisi = [], []

    mancanti = [c for c in SCHEMA if c not in df.columns]
    if mancanti:
        errori.append(f"colonne obbligatorie mancanti: {', '.join(mancanti)}")
    extra = [c for c in df.columns if c not in SCHEMA]
    if extra:
        avvisi.append(f"colonne non previste (ignorate): {', '.join(extra)}")
    if len(df) == 0:
        errori.append("il file non contiene righe")
    if errori:  # senza le colonne giuste gli altri controlli non hanno senso
        return {"valido": False, "errori": errori, "avvisi": avvisi}

    for nome, col in SCHEMA.items():
        testo = df[nome].astype(str).str.strip()
        pieni = testo != ""
        n_pieni = int(pieni.sum())

        vuoti = len(df) - n_pieni
        if col.obbligatoria and vuoti:
            quota = vuoti / len(df)
            (errori if quota > QUOTA_MASSIMA_ANOMALIE else avvisi).append(
                f"{nome}: {vuoti} valori mancanti in una colonna obbligatoria")
        if n_pieni == 0:
            continue

        if col.tipo == "id":
            fuori_formato = int((~testo[pieni].str.match(FORMATO_ID)).sum())
            if fuori_formato:
                errori.append(f"{nome}: {fuori_formato} codici fuori formato (atteso SP-aaaa-nnnnn)")
        elif col.tipo == "data":
            date, _ = _date_miste(df[nome].astype(str))
            illeggibili = int((pieni & date.isna()).sum())
            if illeggibili:
                quota = illeggibili / n_pieni
                (errori if quota > QUOTA_MASSIMA_ANOMALIE else avvisi).append(
                    f"{nome}: {illeggibili} date illeggibili (formati ammessi aaaa-mm-gg e gg/mm/aaaa)")
        elif col.tipo == "numero":
            numeri, _ = _numero(df[nome].astype(str))
            illeggibili = int((pieni & numeri.isna()).sum())
            if illeggibili:
                quota = illeggibili / n_pieni
                (errori if quota > QUOTA_MASSIMA_ANOMALIE else avvisi).append(
                    f"{nome}: {illeggibili} valori non numerici")
            if col.minimo is not None:
                sotto = int((numeri < col.minimo).sum())
                if sotto:
                    avvisi.append(f"{nome}: {sotto} valori sotto il minimo ({col.minimo}), da neutralizzare")
        elif col.tipo == "categoria":
            valori = _normalizza_categoria(nome, df[nome].astype(str))[pieni]
            sconosciuti = valori[~valori.isin(col.ammessi)]
            if len(sconosciuti):
                esempi = ", ".join(sorted(sconosciuti.unique())[:3])
                quota = len(sconosciuti) / n_pieni
                (errori if quota > QUOTA_MASSIMA_ANOMALIE else avvisi).append(
                    f"{nome}: {len(sconosciuti)} valori non ammessi (es. {esempi})")

    return {"valido": not errori, "errori": errori, "avvisi": avvisi}


def valida_pulito(df: pd.DataFrame) -> dict:
    """Controllo rigido del dataframe dopo la pulizia: qui ogni anomalia e' un errore."""
    errori = []
    for nome, col in SCHEMA.items():
        if nome not in df.columns:
            errori.append(f"{nome}: colonna assente dopo la pulizia")
            continue
        s = df[nome]
        if col.tipo == "data" and not pd.api.types.is_datetime64_any_dtype(s):
            errori.append(f"{nome}: tipo {s.dtype}, atteso data")
        if col.tipo == "numero":
            if not pd.api.types.is_float_dtype(s):
                errori.append(f"{nome}: tipo {s.dtype}, atteso numero")
            elif col.minimo is not None and (s < col.minimo).any():
                errori.append(f"{nome}: {int((s < col.minimo).sum())} valori sotto il minimo")
        if col.tipo == "categoria":
            fuori = s.dropna()[~s.dropna().isin(col.ammessi)]
            if len(fuori):
                errori.append(f"{nome}: {len(fuori)} valori non ammessi dopo la pulizia")
        if col.obbligatoria and col.tipo in ("id", "categoria", "data") and s.isna().any():
            errori.append(f"{nome}: {int(s.isna().sum())} valori mancanti dopo la pulizia")
    if "shipment_id" in df.columns and not df["shipment_id"].is_unique:
        errori.append(f"shipment_id: {int(df['shipment_id'].duplicated().sum())} codici ripetuti dopo la pulizia")
    if {"delivery_date", "order_date"} <= set(df.columns) and (df["delivery_date"] < df["order_date"]).any():
        errori.append("delivery_date: consegne precedenti all'ordine rimaste dopo la pulizia")
    return {"valido": not errori, "errori": errori}
