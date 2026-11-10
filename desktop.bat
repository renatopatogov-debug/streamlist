@echo off
REM ============================================================
REM  StremMusic — DESKTOP (janela própria, sem navegador)
REM  Primeira vez? Rode install.bat antes (instala tudo).
REM ============================================================
chcp 65001 >nul
title StremMusic Desktop
cd /d "%~dp0"

REM pywebview faz parte da lista de dependências do install.bat
python desktop.py
if errorlevel 1 (
  echo.
  echo  Falhou. Verifique se o pywebview está instalado: pip install pywebview
  pause
)
