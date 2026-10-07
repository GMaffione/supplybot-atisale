@echo off
REM Installazione di SupplyBot su Windows (da eseguire una sola volta)
cd /d "%~dp0"

echo [1/4] Controllo dei prerequisiti...
where python >nul 2>nul || (echo ERRORE: Python non trovato. Installa Python 3.10-3.13 e riprova. & pause & exit /b 1)
where npm >nul 2>nul || (echo ERRORE: Node.js non trovato. Installa Node.js 20 o superiore e riprova. & pause & exit /b 1)

echo [2/4] Ambiente Python e dipendenze del data agent...
if not exist .venv python -m venv .venv
call .venv\Scripts\python -m pip install --upgrade pip
call .venv\Scripts\python -m pip install -r data_agent\requirements.txt || (echo ERRORE nell'installazione Python. & pause & exit /b 1)

echo [3/4] Dipendenze del back end Node...
pushd backend
call npm install || (popd & echo ERRORE nell'installazione del back end. & pause & exit /b 1)
popd

echo [4/4] Dipendenze del front end React...
pushd frontend
call npm install || (popd & echo ERRORE nell'installazione del front end. & pause & exit /b 1)
popd

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Creato il file .env: aprilo e inserisci la tua OPENAI_API_KEY prima di avviare.
)
echo.
echo Installazione completata. Per avviare SupplyBot esegui avvia.bat
pause
