@echo off
setlocal EnableExtensions

title WFBARNet Launcher
cd /d "%~dp0"
if errorlevel 1 (
    echo [ERROR] Cannot open the project directory: %~dp0
    pause
    exit /b 1
)

set "ENV_NAME=WFBARNet"
set "CONDA_RUNNER="
set "PYTHONUTF8=1"

for %%C in (
    "%CONDA_EXE%"
    "%USERPROFILE%\anaconda3\Scripts\conda.exe"
    "%USERPROFILE%\miniconda3\Scripts\conda.exe"
    "%LOCALAPPDATA%\miniconda3\Scripts\conda.exe"
    "C:\ProgramData\anaconda3\Scripts\conda.exe"
    "D:\Developer\Anaconda\Scripts\conda.exe"
) do (
    if not defined CONDA_RUNNER if exist "%%~C" set "CONDA_RUNNER=%%~fC"
)

if not defined CONDA_RUNNER (
    for /f "delims=" %%C in ('where conda.exe 2^>nul') do (
        if not defined CONDA_RUNNER set "CONDA_RUNNER=%%~fC"
    )
)

if not defined CONDA_RUNNER (
    echo [ERROR] Conda was not found.
    echo Install Anaconda or Miniconda, then add Conda to PATH.
    pause
    exit /b 1
)

echo [WFBARNet] Conda: "%CONDA_RUNNER%"
echo [WFBARNet] Environment: %ENV_NAME%
echo [WFBARNet] Project: "%CD%"
echo.

"%CONDA_RUNNER%" run --no-capture-output -n "%ENV_NAME%" python main.py %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] WFBARNet exited with code %EXIT_CODE%.
    echo Confirm that the Conda environment "%ENV_NAME%" exists and contains the project dependencies.
    pause
)

endlocal & exit /b %EXIT_CODE%
