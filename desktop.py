#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StremMusic — versão DESKTOP (janela própria, sem navegador)

Abre a interface dentro de uma janela nativa Windows via WebView2 (Edge).
Mesma aparência, mesmos dados (metrolist.db, cache, downloads).

Uso:      python desktop.py     (ou desktop.bat)
Depende:  pip install pywebview

Sem o pywebview instalado, abre automaticamente no navegador padrão.
"""
import os
import socket
import sys
import threading
import time

os.environ.setdefault("AUTO_UPDATE_YTDLP", "1")

import uvicorn
from app import app, BASE

PORT = int(os.environ.get("PORT", "8787"))
URL = f"http://127.0.0.1:{PORT}"


def _port_busy(port: int) -> bool:
    """Verifica se já existe uma instância rodando nesta porta."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def main():
    own_server = not _port_busy(PORT)
    if own_server:
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT,
                                               log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.time() + 20
        while not _port_busy(PORT):
            if time.time() > deadline or not threading.main_thread().is_alive():
                print("❌ O servidor não subiu — execute `pip install -r requirements.txt` e tente novamente.")
                sys.exit(1)
            time.sleep(0.15)
        print(f"  🎵 StremMusic Desktop — servidor rodando em {URL}")
    else:
        server = None
        print(f"  🎵 StremMusic Desktop — conectando ao servidor já ativo em {URL}")

    try:
        import webview
    except ImportError:
        import webbrowser
        print("  ℹ️ pywebview não instalado — abrindo no navegador padrão.")
        webbrowser.open(URL)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            if server:
                server.should_exit = True
        return

    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass

    webview.create_window(
        "StremMusic",
        URL,
        width=1280, height=820,
        min_size=(940, 560),
        background_color="#020617",
    )
    webview.start(private_mode=False, storage_path=str(BASE / ".webdata"))
    if server:
        server.should_exit = True


if __name__ == "__main__":
    main()
