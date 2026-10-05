@echo off
setlocal
set "WFBARNET_HOME=%~dp0"
set "OLLAMA_HOME=D:\1A\Ollama"
set "OLLAMA_MODELS=D:\1A\OllamaModels"
cd /d "%WFBARNET_HOME%"

if not exist ".venv\Scripts\pythonw.exe" (
    echo WFBARNet virtual environment was not found.
    echo Expected: %WFBARNET_HOME%\.venv
    pause
    exit /b 1
)

if exist "%OLLAMA_HOME%\ollama.exe" (
    "%OLLAMA_HOME%\ollama.exe" list >nul 2>&1
    if errorlevel 1 (
        powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%OLLAMA_HOME%\ollama.exe' -ArgumentList 'serve' -WorkingDirectory '%OLLAMA_HOME%' -WindowStyle Hidden"
        timeout /t 3 /nobreak >nul
    )
)

start "" ".venv\Scripts\pythonw.exe" "main.py"
endlocal
