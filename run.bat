@echo off
REM ============================================================
REM  StremMusic — servidor (uso contínuo / rede local)
REM ============================================================
chcp 65001 >nul
title StremMusic Servidor
cd /d "%~dp0"

echo.
echo  =============================================
echo    StremMusic - Servidor
echo  =============================================
echo.

python app.py %*

echo.
echo  Servidor encerrado.
pause
