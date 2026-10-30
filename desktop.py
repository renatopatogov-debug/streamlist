#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StremMusic — versão DESKTOP (janela própria, sem navegador)

Abre a MESMA interface (static/index.html) dentro de uma janela nativa
Windows via WebView2 (Edge). Mesma aparência, mesmos dados (metrolist.db,
cache, downloads) — só muda a "casca": em vez do Chrome, uma janela de app.

Uso:      python desktop.py     (ou desktop.bat)
Depende:  pip install pywebview   (instalado pelo install.bat novo)

Sem o pywebview, cai com elegância: abre no navegador padrão mesmo.

Detalhe: no desktop o servidor escuta SÓ em 127.0.0.1 (o celular não acessa).
Para compartilhar na rede (celular/TV), rode run.bat (HOST 0.0.0.0).
"""
import os
import socket
import sys
import threading
import time

# yt-dlp desatualizado é a causa nº1 de "o app parou de funcionar um dia":
# no desktop, atualiza sozinho por padrão (desligue com AUTO_UPDATE_YTDLP=0)
os.environ.setdefault("AUTO_UPDATE_YTDLP", "1")

import uvicorn

from app import app   # importa o backend inteiro (rotas, banco, cache, yt-dlp)

PORT = int(os.environ.get("PORT", "8787"))
URL = f"http://127.0.0.1:{PORT}"


def _port_busy(port: int) -> bool:
    """Já existe um StremMusic rodando nesta porta? (evita crash de porta dupla:
    abrir o desktop com o run.bat ligado reaproveita o servidor que já existe)"""
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
                print("⚠ O servidor não subiu — rode `pip install -r requirements.txt` e tente de novo.")
                sys.exit(1)
            time.sleep(0.15)
        print(f"  🎵 StremMusic Desktop — servidor em {URL}")
    else:
        server = None
        print(f"  🎵 StremMusic Desktop — reaproveitando servidor já rodando em {URL}")

    try:
        import webview
    except ImportError:
        import webbrowser
        print("  💡 pywebview não instalado — abrindo no navegador. Para janela nativa: pip install pywebview")
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
        webview.settings["ALLOW_DOWNLOADS"] = True   # botão "Salvar" da aba Downloads funciona na janela
    except Exception:
        pass
    # private_mode=False é ESSENCIAL: no modo privado o WebView não tem cache de
    # disco (fontes/capas recarregavam da internet a cada interação = sensação de
    # atraso), e o IndexedDB ficava em memória (offline do aparelho se perdia).
    # storage_path garante que tudo persista junto aos dados do app.
    from app import BASE
    webview.create_window(
        "StremMusic",
        URL,
        width=1280, height=820,
        min_size=(940, 560),
        background_color="#020617",
    )
    webview.start(private_mode=False, storage_path=str(BASE / ".webdata"))
    if server:
        server.should_exit = True   # janela fechou → desliga o servidor limpo


if __name__ == "__main__":
    main()
