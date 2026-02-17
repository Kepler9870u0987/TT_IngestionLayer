@echo off
REM Launcher script per eseguire gli script PowerShell bypassando l'execution policy
REM Uso: run.bat script-name [args]
REM Esempio: run.bat setup -SkipRedisCheck

if "%1"=="" (
    echo Uso: run.bat ^<script-name^> [args]
    echo.
    echo Script disponibili:
    echo   setup          - Setup completo ambiente
    echo   test           - Esegui test
    echo   oauth-setup    - Gestione OAuth2
    echo   quick-start    - Setup guidato interattivo
    echo   start          - Avvia producer e worker
    echo   stop           - Ferma sistema
    echo   health_check   - Verifica health
    echo   dev            - Development utilities
    echo.
    echo Esempi:
    echo   run.bat setup -SkipRedisCheck
    echo   run.bat test -Coverage
    echo   run.bat oauth-setup -Action Info
    echo   run.bat dev -Action Restart
    exit /b 1
)

set SCRIPT_NAME=%1
shift

REM Costruisci gli argomenti rimanenti
set ARGS=
:loop
if "%1"=="" goto endloop
set ARGS=%ARGS% %1
shift
goto loop
:endloop

REM Esegui lo script
powershell -ExecutionPolicy Bypass -File ".\scripts\%SCRIPT_NAME%.ps1" %ARGS%
