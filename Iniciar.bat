@echo off
REM ============================================================
REM   StremMusic — Launcher Unificado (1-Click)
REM   Verifica ambiente, dependencias e inicia o modo desejado.
REM ============================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul
title StremMusic Launcher
cd /d "%~dp0"

:CHECK_PYTHON
python --version >nul 2>&1
if errorlevel 1 (
    cls
    echo ============================================================
    echo   StremMusic — Python nao encontrado!
    echo ============================================================
    echo.
    echo   O Python 3.10 ou superior e necessario para executar o StremMusic.
    echo.
    echo   [1] Tentar instalar automaticamente pelo Windows (winget)
    echo   [2] Abrir pagina de download oficial do Python (python.org)
    echo   [3] Sair
    echo.
    set /p pychoice="Escolha uma opcao [1-3]: "
    if "!pychoice!"=="1" (
        echo.
        echo Instalando Python... Aguarde.
        winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        echo.
        echo Se a instalacao concluiu, REINICIE este inicializador (Iniciar.bat).
        pause
        exit /b 0
    )
    if "!pychoice!"=="2" (
        start https://www.python.org/downloads/
        echo Lembre-se de marcar a opcao "Add Python to PATH" durante a instalacao!
        pause
        exit /b 0
    )
    exit /b 1
)

:CHECK_DEPS
REM Verifica silenciosamente se uma das dependencias basicas existe
python -c "import fastapi, uvicorn, yt_dlp, qrcode" >nul 2>&1
if errorlevel 1 (
    echo ============================================================
    echo   Primeira execucao detectada! Instalando dependencias...
    echo ============================================================
    echo.
    echo Aguarde alguns instantes enquanto os pacotes sao preparados...
    echo.
    python -m pip install --upgrade pip >nul 2>&1
    python -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo [ERRO] Falha ao instalar dependencias do requirements.txt.
        echo Verifique sua conexao com a internet e tente novamente.
        pause
        exit /b 1
    )
    echo.
    echo Dependencias instaladas com sucesso!
    timeout /t 2 >nul
)

:MENU
cls
echo ============================================================
echo   🎵 StremMusic — Launcher Oficial
echo ============================================================
echo.
echo   Escolha como deseja iniciar:
echo.
echo   [1] Iniciar com Tunel Cloudflare (Rua 4G/5G + QR Code) [PADRAO]
echo   [2] Iniciar como Aplicativo Desktop (Janela propria)
echo   [3] Iniciar Servidor Local (Apenas Wi-Fi de Casa)
echo   [4] Painel do Host no Navegador (Gerenciar Usuarios/Perfis)
echo   [5] Atualizar yt-dlp e componentes
echo   [0] Sair
echo.
set /p opt="Digite o numero da opcao e pressione Enter [1]: "

if "%opt%"=="" set opt=1
if "%opt%"=="1" goto MODE_TUNNEL
if "%opt%"=="2" goto MODE_DESKTOP
if "%opt%"=="3" goto MODE_LOCAL
if "%opt%"=="4" goto OPEN_PAINEL
if "%opt%"=="5" goto UPDATE_TOOLS
if "%opt%"=="0" exit /b 0

echo Opcao invalida.
timeout /t 1 >nul
goto MENU

:MODE_TUNNEL
cls
title StremMusic — Servidor + Tunel 4G/5G
echo ============================================================
echo   Iniciando StremMusic com Tunel Cloudflare Seguro...
echo ============================================================
echo.
python app.py --tunnel
goto FINISH

:MODE_DESKTOP
cls
title StremMusic — Desktop
echo ============================================================
echo   Iniciando StremMusic em Janela Nativa...
echo ============================================================
echo.
python desktop.py
goto FINISH

:MODE_LOCAL
cls
title StremMusic — Servidor Local
echo ============================================================
echo   Iniciando StremMusic na Rede Local...
echo ============================================================
echo.
python app.py
goto FINISH

:OPEN_PAINEL
start http://127.0.0.1:8787/painel
goto MENU

:UPDATE_TOOLS
cls
echo ============================================================
echo   Atualizando yt-dlp e componentes essenciais...
echo ============================================================
echo.
python -m pip install -U yt-dlp ytmusicapi bgutil-ytdlp-pot-provider
echo.
echo Atualizacao concluida!
pause
goto MENU

:FINISH
echo.
echo O StremMusic foi encerrado.
pause
