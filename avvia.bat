@echo off
REM Avvio di SupplyBot: apre una finestra per ciascun servizio, nell'ordine giusto
cd /d "%~dp0"

if not exist .env (echo ERRORE: manca il file .env. Copia .env.example in .env e inserisci la chiave. & pause & exit /b 1)
if not exist .venv (echo ERRORE: esegui prima installa.bat & pause & exit /b 1)

echo [1/4] ChromaDB (porta 8001)...
start "SupplyBot - ChromaDB" cmd /k ".venv\Scripts\chroma.exe run --path backend\chroma_db --port 8001"

echo [2/4] Data agent Python (porta 8000)...
start "SupplyBot - Data agent" cmd /k "cd data_agent && ..\.venv\Scripts\python -m uvicorn app:app --port 8000"

echo [3/4] Back end Node (porta 3001)...
start "SupplyBot - Back end" cmd /k "cd backend && npm start"

echo [4/4] Front end React (porta 5173)...
start "SupplyBot - Front end" cmd /k "cd frontend && npm run dev"

echo.
echo Attendo che i servizi siano pronti...
timeout /t 12 /nobreak >nul
start http://localhost:5173
echo SupplyBot e' aperto nel browser. Per fermarlo chiudi le quattro finestre dei servizi.
