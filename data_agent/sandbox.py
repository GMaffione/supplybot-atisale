"""
Esecuzione controllata del codice Python scritto dall'LLM.

Il pandas agent scrive codice di analisi; questo modulo lo esegue in modo
deterministico ma con tre protezioni:

1. Controllo statico (AST) prima dell'esecuzione: solo import ammessi
   (pandas, numpy, matplotlib, seaborn, math, datetime, statistics),
   nessun accesso a file, rete, sistema operativo o funzioni come eval/exec.
2. Esecuzione in un PROCESSO SEPARATO con tempo massimo: un ciclo infinito
   o un calcolo pesante non blocca il servizio. Funziona anche su Windows.
3. Builtins ridotti: il codice vede solo funzioni innocue (len, sum, print...).

Non e' una sandbox di sicurezza da produzione (per quello servirebbe un
container isolato per ogni esecuzione), ma e' adeguata a un servizio dimostrativo e lo dichiariamo.
Nella versione online il processo figlio riceve un ambiente ridotto: la chiave OpenAI non gli
viene passata, quindi il codice dell'LLM non puo' leggerla.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

# online (piano gratuito, CPU ridotta) il tempo massimo si alza con SANDBOX_TIMEOUT
TIMEOUT_SECONDI = int(os.getenv("SANDBOX_TIMEOUT", "40"))
MAX_OUTPUT = 6000

MODULI_AMMESSI = {"pandas", "numpy", "matplotlib", "seaborn", "math", "datetime", "statistics"}
NOMI_VIETATI = {
    "open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help", "memoryview",
    "os", "sys", "subprocess", "shutil", "socket", "pathlib", "importlib", "builtins",
}
METODI_VIETATI = {
    # scrittura di file
    "to_csv", "to_excel", "to_pickle", "to_parquet", "to_sql", "to_json", "to_html", "to_xml", "to_feather",
    "to_hdf", "to_stata", "tofile", "savetxt", "save", "savez", "savefig",
    # lettura di file
    "read_csv", "read_excel", "read_pickle", "read_sql", "read_table", "read_fwf", "read_json", "read_html",
    "read_xml", "read_parquet", "read_feather", "read_hdf", "read_sas", "read_spss", "read_stata", "read_orc",
    "read_clipboard", "loadtxt", "genfromtxt", "fromfile", "load", "memmap",
    # sistema
    "system", "popen", "remove", "unlink",
}
# variabili d'ambiente che il processo figlio puo' vedere (niente chiavi)
AMBIENTE_AMMESSO = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "LANG", "MPLCONFIGDIR")

RUNNER = Path(__file__).resolve().parent / "_runner.py"


class CodiceNonAmmesso(Exception):
    pass


def controlla_codice(codice: str) -> None:
    """Solleva CodiceNonAmmesso se il codice usa costrutti vietati."""
    try:
        albero = ast.parse(codice)
    except SyntaxError as e:
        raise CodiceNonAmmesso(f"Errore di sintassi alla riga {e.lineno}: {e.msg}") from e
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                if alias.name.split(".")[0] not in MODULI_AMMESSI:
                    raise CodiceNonAmmesso(f"Import non ammesso: {alias.name}")
        elif isinstance(nodo, ast.ImportFrom):
            if (nodo.module or "").split(".")[0] not in MODULI_AMMESSI:
                raise CodiceNonAmmesso(f"Import non ammesso: {nodo.module}")
        elif isinstance(nodo, ast.Name) and nodo.id in NOMI_VIETATI:
            raise CodiceNonAmmesso(f"Uso non ammesso: {nodo.id}")
        elif isinstance(nodo, ast.Attribute):
            if nodo.attr.startswith("__"):
                raise CodiceNonAmmesso(f"Accesso non ammesso ad attributo interno: {nodo.attr}")
            if nodo.attr in METODI_VIETATI:
                raise CodiceNonAmmesso(f"Metodo non ammesso: {nodo.attr} (niente lettura/scrittura di file)")
            # niente scorciatoie verso il sistema passando da un modulo ammesso (es. pd.io.common.os)
            if nodo.attr in NOMI_VIETATI or nodo.attr == "environ" or nodo.attr.startswith(("exec", "spawn", "fork", "kill")):
                raise CodiceNonAmmesso(f"Accesso non ammesso: {nodo.attr}")


def _ambiente_ridotto() -> dict:
    """Solo le variabili necessarie a Python per partire: la chiave OpenAI resta fuori."""
    ambiente = {k: v for k, v in os.environ.items() if k.upper() in AMBIENTE_AMMESSO}
    ambiente["PYTHONIOENCODING"] = "utf-8"
    return ambiente


def esegui(codice: str, percorso_df: str, cartella_grafici: str) -> dict:
    """
    Esegue il codice in un processo separato. Restituisce un dizionario con:
    ok (bool), output (testo stampato), errore (traceback o None), grafici (nomi file PNG).
    """
    try:
        controlla_codice(codice)
    except CodiceNonAmmesso as e:
        return {"ok": False, "output": "", "errore": f"Codice bloccato dal controllo di sicurezza: {e}", "grafici": []}

    richiesta = json.dumps({"codice": codice, "df": percorso_df, "grafici": cartella_grafici})
    try:
        proc = subprocess.run([sys.executable, str(RUNNER)], input=richiesta, capture_output=True,
                              text=True, timeout=TIMEOUT_SECONDI, encoding="utf-8", errors="replace",
                              env=_ambiente_ridotto())
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "errore": f"Tempo massimo superato ({TIMEOUT_SECONDI} s): "
                "semplifica il calcolo.", "grafici": []}

    righe = [r for r in proc.stdout.splitlines() if r.startswith("@@RISULTATO@@")]
    if not righe:
        return {"ok": False, "output": "", "errore": "Il processo di analisi si e' interrotto: "
                + proc.stderr[-1500:], "grafici": []}
    risultato = json.loads(righe[-1][len("@@RISULTATO@@"):])
    risultato["output"] = risultato["output"][:MAX_OUTPUT]
    return risultato
