@echo off
REM ============================================================
REM  StremMusic — Servidor com Túnel Cloudflare Automático
REM  Gera link HTTPS seguro (trycloudflare.com) e QR Code no console
REM  para você e seus amigos ouvirem na rua (4G/5G) sem abrir portas.
REM ============================================================
chcp 65001 >nul
title StremMusic + Túnel Cloudflare (Rua/4G)
cd /d "%~dp0"

echo.
echo  ======================================================
echo    StremMusic - Servidor + Túnel Cloudflare (Rua/4G)
echo  ======================================================
echo.

python app.py --tunnel %*

echo.
echo  Servidor encerrado.
pause
