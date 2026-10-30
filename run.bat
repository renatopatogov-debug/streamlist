@echo off
REM ============================================================
REM  StremMusic — servidor (uso contínuo / hospedagem)
REM  Diferença do "python app.py" direto: roda SEM auto-reload,
REM  pronto pra ficar ligado e acessado pelo celular de fora.
REM ============================================================
chcp 65001 >nul
title StremMusic Servidor
cd /d "%~dp0"

echo.
echo  =============================================
echo    StremMusic - Servidor
echo  =============================================
echo.

python app.py

echo.
echo  Servidor encerrado.
pause
