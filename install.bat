@echo off
REM ============================================================
REM  StremMusic — instalador Windows
REM  Uso: execute install.bat na pasta do projeto
REM ============================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul
title StremMusic Instalador

echo.
echo  =============================================
echo    StremMusic - Instalador (Windows)
echo  =============================================
echo.

REM ---------- 1. Python ----------
echo  [1/5] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERRO: Python nao encontrado no PATH.
    echo.
    echo  Instale pelo site https://python.org/downloads
    echo  ou com:  winget install Python.Python.3.12
    echo.
    echo  IMPORTANTE: marque "Add Python to PATH" no instalador.
    echo.
    pause
    exit /b 1
)
python --version
echo  Python OK.
echo.

REM ---------- 2. ffmpeg (MP3 de verdade) ----------
echo  [2/5] Verificando FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  FFmpeg nao encontrado. Instalando com winget...
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    where ffmpeg >nul 2>&1
    if errorlevel 1 (
        echo  ATENCAO: FFmpeg nao instalado automaticamente.
        echo  Sem ele, os downloads saem como .m4a em vez de .mp3.
        echo  Baixe em https://ffmpeg.org se quiser MP3.
    ) else (
        echo  FFmpeg instalado. Feche e REABRA este terminal antes de rodar o app.
    )
) else (
    echo  FFmpeg OK.
)
echo.

REM ---------- 3. Deno (extracao de streams do YouTube) ----------
echo  [3/5] Verificando Deno...
where deno >nul 2>&1
if errorlevel 1 (
    echo  Deno nao encontrado. Instalando com winget...
    winget install --id DenoLand.Deno -e --accept-source-agreements --accept-package-agreements
    where deno >nul 2>&1
    if errorlevel 1 (
        echo  ATENCAO: Deno nao instalado automaticamente.
        echo  Sem ele, o YouTube pode recusar alguns formatos de audio.
        echo  Baixe em https://deno.com se a busca/stream falhar.
    ) else (
        echo  Deno instalado. Feche e REABRA este terminal antes de rodar o app.
    )
) else (
    echo  Deno OK.
)
echo.

REM ---------- 4. Dependencias Python ----------
echo  [4/5] Instalando dependencias Python...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo  ERRO: falha ao instalar dependencias. Verifique sua conexao com a internet.
    pause
    exit /b 1
)
echo  Dependencias OK.
echo.

REM ---------- 5. Pronto ----------
echo  [5/5] Tudo pronto!
echo.
echo  =============================================
echo    Como app de janela (desktop):  desktop.bat
echo    Para rede local / celular:     run.bat
echo    Para acesso na rua (4G/5G):    run_tunnel.bat
echo    Painel do Host:                http://127.0.0.1:8787/painel
echo  =============================================
echo.
echo  Dicas:
echo   - Se acabou de instalar FFmpeg/Deno, REINICIE O TERMINAL
echo   - Se o Firewall perguntar, autorize o Python (acesso na rede local)
echo   - Primeira vez no desktop: a janela usa o WebView2 do Windows (ja vem no Win 10/11)
echo   - Se a busca/stream parar de funcionar um dia:
echo       pip install -U yt-dlp ytmusicapi
echo.
pause
