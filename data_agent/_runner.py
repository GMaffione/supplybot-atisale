"""
Processo figlio che esegue il codice dell'agente (lanciato da sandbox.py).
Riceve su stdin: {"codice", "df", "grafici"}. Scrive su stdout una riga
"@@RISULTATO@@{json}" con output, eventuale errore e grafici salvati.
"""

import builtins
import contextlib
import io
import json
import sys
import traceback
import uuid
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # nessuna finestra: i grafici si salvano solo su file
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Stile dei grafici: palette categoriale validata, griglia leggera, testo leggibile
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
plt.rcParams.update({
    "figure.figsize": (9, 4.8), "figure.dpi": 110, "figure.facecolor": "#fcfcfb",
    "axes.facecolor": "#fcfcfb", "axes.prop_cycle": matplotlib.cycler(color=PALETTE),
    "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#b8b7b0",
    "axes.grid": True, "axes.grid.axis": "y", "axes.axisbelow": True, "grid.color": "#e6e5e0", "grid.linewidth": 0.8,
    "axes.titlesize": 13, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.labelcolor": "#52514e", "xtick.color": "#52514e", "ytick.color": "#52514e",
    "text.color": "#0b0b0b", "font.size": 10.5, "lines.linewidth": 2, "legend.frameon": False,
})

MODULI_AMMESSI = {"pandas", "numpy", "matplotlib", "seaborn", "math", "datetime", "statistics"}
BUILTINS_AMMESSI = [
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "format", "int", "isinstance",
    "len", "list", "map", "max", "min", "print", "range", "reversed", "round", "set", "sorted", "str",
    "sum", "tuple", "zip", "Exception", "ValueError", "KeyError", "TypeError", "True", "False", "None",
]


def import_controllato(nome, *args, **kwargs):
    if nome.split(".")[0] not in MODULI_AMMESSI:
        raise ImportError(f"Import non ammesso: {nome}")
    return builtins.__import__(nome, *args, **kwargs)


def main():
    richiesta = json.loads(sys.stdin.buffer.read().decode("utf-8"))  # utf-8 esplicito: su Windows il default e' cp1252
    cartella = Path(richiesta["grafici"])
    cartella.mkdir(parents=True, exist_ok=True)
    grafici = []

    def salva_grafico(titolo=None):
        """Salva la figura corrente come PNG e restituisce il nome del file."""
        if titolo:
            plt.title(titolo)
        nome = f"grafico_{uuid.uuid4().hex[:10]}.png"
        plt.tight_layout()
        plt.savefig(cartella / nome)
        plt.close("all")
        grafici.append(nome)
        print(f"[grafico salvato: {nome}]")
        return nome

    sicuri = {n: getattr(builtins, n) for n in BUILTINS_AMMESSI if hasattr(builtins, n)}
    sicuri["__import__"] = import_controllato
    ambiente = {"__builtins__": sicuri, "pd": pd, "np": np, "plt": plt, "salva_grafico": salva_grafico,
                "df": pd.read_pickle(richiesta["df"])}

    buffer = io.StringIO()
    errore = None
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compile(richiesta["codice"], "<codice_agente>", "exec"), ambiente)
    except Exception:
        # solo le ultime righe del traceback: bastano all'LLM per correggersi
        errore = "".join(traceback.format_exc().splitlines(keepends=True)[-6:])

    print("@@RISULTATO@@" + json.dumps({"ok": errore is None, "output": buffer.getvalue(),
                                        "errore": errore, "grafici": grafici}))


if __name__ == "__main__":
    main()
