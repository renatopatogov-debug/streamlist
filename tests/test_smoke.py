#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes do StremMusic que NÃO dependem de internet/YouTube (rodam em ~1s).

Uso:  python tests/test_smoke.py

Cobrem justamente os defeitos que já apareceram em produção:
  1. cache envenenado por Range  (o <audio> sempre pede "bytes=0-2047")
  2. interruptores do "cache inteligente" que a UI mostrava e o app ignorava
  3. lista efetiva de clients quando não existe cookies.txt
  4. banco: histórico/next_map (o que o "continuar ouvindo" usa)
"""
import os
import sys

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import tempfile
import time
from pathlib import Path

# importa o app.py da raiz do projeto sem depender do cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("YT_CLIENTS", "")   # não deixa o ambiente mudar o teste
os.environ.setdefault("PROBE_ON_BOOT", "0")   # sem sondagem de rede durante os testes

import app as A   # noqa: E402

FAILS = []
OKS = 0


def check(name, cond, detail=""):
    global OKS
    if cond:
        OKS += 1
        print(f"  ✓ {name}")
    else:
        FAILS.append(name)
        print(f"  ✗ {name}   {detail}")


class FakeResp:
    """Imita o mínimo de requests.Response usado por _cache_plan()."""

    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}


def plan(status=200, rng=None, cr=None, cl=None, ct="audio/mp4"):
    h = {"Content-Type": ct}
    if cr is not None:
        h["Content-Range"] = cr
    if cl is not None:
        h["Content-Length"] = str(cl)
    return A._cache_plan(FakeResp(status, h), rng)


def main():
    # o teste mexe em SETTINGS (e alguns caminhos salvam em disco): guarda o
    # arquivo e devolve exatamente como estava — teste não muda preferência de ninguém
    _settings_bak = A.SETTINGS_FILE.read_bytes() if A.SETTINGS_FILE.exists() else None
    _settings_dict = dict(A.SETTINGS)
    try:
        return _run()
    finally:
        A.SETTINGS.clear()
        A.SETTINGS.update(_settings_dict)
        if _settings_bak is not None:
            A.SETTINGS_FILE.write_bytes(_settings_bak)


def _run():
    print("\n1) _cache_plan — só guarda a MÚSICA INTEIRA")
    A.SETTINGS["cache_enabled"] = True

    # o caso do bug: o navegador pede os 2 primeiros KB e o app gravava isso
    # como se fosse a faixa completa (cache envenenado, "para em 1 segundo")
    check("206 de 2 KB de uma faixa de 3,9 MB NÃO vira cache",
          plan(206, rng="bytes=0-2047", cr="bytes 0-2047/3935490", cl=2048) is None,
          "-> aceitou um recorte como arquivo inteiro")

    check("206 que cobre o arquivo inteiro VIRA cache",
          plan(206, rng="bytes=0-", cr="bytes 0-3935489/3935490", cl=3935490) is not None)

    check("206 do meio do arquivo NÃO vira cache",
          plan(206, rng="bytes=1000-2000", cr="bytes 1000-2000/3935490", cl=1001) is None)

    check("206 sem Content-Range NÃO vira cache",
          plan(206, rng="bytes=0-2047", cr=None, cl=2048) is None)

    check("206 com total diferente do Content-Length NÃO vira cache",
          plan(206, rng="bytes=0-1999", cr="bytes 0-1999/3935490", cl=2000) is None)

    check("200 sem Range VIRA cache",
          plan(200, rng=None, cl=3935490) is not None)

    check("resposta comprimida NÃO vira cache",
          A._cache_plan(FakeResp(200, {"Content-Type": "audio/mp4",
                                       "Content-Encoding": "gzip",
                                       "Content-Length": "1000"}), None) is None)

    check("content-type que não é áudio NÃO vira cache",
          A._cache_plan(FakeResp(200, {"Content-Type": "text/html",
                                       "Content-Length": "1000"}), None) is None)

    check("cache desligado no painel = nada é guardado",
          (A.SETTINGS.__setitem__("cache_enabled", False)
           or A._cache_plan(FakeResp(200, {"Content-Type": "audio/mp4",
                                           "Content-Length": "1000"}), None) is None))

    A.SETTINGS["cache_enabled"] = True

    print("\n2) settings: o que o painel manda é o que o app grava")
    # _apply_settings() PERSISTE em settings.json: o teste guarda o arquivo e
    # devolve no fim — teste não pode mexer nas preferências de quem usa o app.
    _bak = A.SETTINGS_FILE.read_bytes() if A.SETTINGS_FILE.exists() else None
    _antes = dict(A.SETTINGS)
    try:
        A.SETTINGS["smart_cache"] = False
        A.SETTINGS["learn_next"] = False
        A._apply_settings({"smart_cache": True, "learn_next": False})
        check("smart_cache é gravado (antes a UI mudava e o app ignorava)",
              A.SETTINGS.get("smart_cache") is True)
        check("learn_next é gravado", A.SETTINGS.get("learn_next") is False)
    finally:
        A.SETTINGS.clear()
        A.SETTINGS.update(_antes)
        if _bak is not None:
            A.SETTINGS_FILE.write_bytes(_bak)
        check("settings.json volta ao que era depois do teste",
              _bak is None or A.SETTINGS_FILE.read_bytes() == _bak)

    print("\n3) clients: web_safari/tv só entram com cookies de verdade")
    old = dict(A.SETTINGS)
    A.SETTINGS["use_cookies"] = True
    had = A.COOKIES_FILE.exists()
    if had:
        A.COOKIES_FILE.rename(A.COOKIES_FILE.with_suffix(".txt.testbak"))
    try:
        lbl = [A._label(c) for c in A._clients_full()]
        check("sem cookies.txt, web_safari/tv ficam FORA da disputa",
              "web_safari" not in lbl and "tv" not in lbl, f"-> {lbl}")
        check("android_vr e mweb continuam na disputa",
              "android_vr" in lbl and "mweb" in lbl, f"-> {lbl}")
        A.COOKIES_FILE.write_text("# vazio\n", encoding="utf-8")
        lbl2 = [A._label(c) for c in A._clients_full()]
        check("com cookies.txt, web_safari/tv VOLTAM",
              "web_safari" in lbl2 and "tv" in lbl2, f"-> {lbl2}")
    finally:
        try:
            if not had:
                A.COOKIES_FILE.unlink(missing_ok=True)
            else:
                A.COOKIES_FILE.with_suffix(".txt.testbak").rename(A.COOKIES_FILE)
        except Exception:
            pass
        A.SETTINGS.clear()
        A.SETTINGS.update(old)

    print("\n4) banco: histórico e next_map")
    with A.db() as con:
        n = con.execute("SELECT COUNT(*) c FROM history").fetchone()["c"]
        check("tabela history existe", n >= 0)
        con.execute("DELETE FROM next_map WHERE prev_id=?", ("__test__",))
        con.execute("INSERT INTO next_map(prev_id, next_id, n) VALUES(?,?,?)",
                    ("__test__", "__test2__", 3))
        row = con.execute("SELECT n FROM next_map WHERE prev_id=?",
                          ("__test__",)).fetchone()
        check("next_map aceita o que o 🧠 aprende", row is not None and row["n"] == 3)
        con.execute("DELETE FROM next_map WHERE prev_id=?", ("__test__",))

    print("\n5) pre-tests: EJS é PLANO B, ordem segue o que foi medido")
    sem = A._base_ydl_opts(ejs=False)
    com = A._base_ydl_opts(ejs=True)
    check("sem o solucionador JS por padrão (economiza ~4s medidos por faixa)",
          "remote_components" not in sem)
    check("com o solucionador JS quando pedido (plano B)",
          com.get("remote_components") == ["ejs:github"])
    check("PO Token continua ligado nos dois caminhos",
          sem["extractor_args"]["youtube"]["fetch_pot"] == ["auto"]
          and com["extractor_args"]["youtube"]["fetch_pot"] == ["auto"])

    _ms_bak = dict(A._CLIENT_MS)
    try:
        A._CLIENT_MS.clear()
        A._CLIENT_MS.update({"lento": 9000, "rapido": 3000})
        ordem = [A._label(c) for c in A._ordered_clients([["lento"], ["rapido"], None])]
        check("quem foi medido mais rápido vai na frente (não medido fica no fim)",
              ordem == ["rapido", "lento", "padrão"], f"-> {ordem}")
        A._CLIENT_MS.clear()
        ordem2 = [A._label(c) for c in A._ordered_clients([["lento"], ["rapido"]])]
        check("sem medição, a ordem original é mantida", ordem2 == ["lento", "rapido"], f"-> {ordem2}")
    finally:
        A._CLIENT_MS.clear()
        A._CLIENT_MS.update(_ms_bak)

    ejs_bak = dict(A._EJS)
    try:
        A._EJS.update({"needed": False, "at": time.time(), "streak_ok": 0, "streak_fail": 0})
        A._set_ejs(True, "teste")
        check("_set_ejs liga e zera as sequências", A._EJS["needed"] is True
              and A._EJS["streak_fail"] == 0 and A._EJS["streak_ok"] == 0)
        A._set_ejs(False, "")
        check("_set_ejs desliga", A._EJS["needed"] is False)
        check("existe janela de reavaliação (EJS_TTL) — não fica ligado para sempre",
              A.EJS_TTL >= 3600 and A.EJS_FALHAS_PARA_LIGAR >= 2)
    finally:
        A._EJS.clear()
        A._EJS.update(ejs_bak)

    print("\n6) cloudflare quick tunnel & pwa (rua 4g/5g e modo app no celular)")
    try:
        from fastapi.testclient import TestClient
        client = TestClient(A.app)
        r_sw = client.get("/sw.js")
        check("/sw.js responde 200", r_sw.status_code == 200, f"status {r_sw.status_code}")
        check("/sw.js define Service-Worker-Allowed: /", r_sw.headers.get("Service-Worker-Allowed") == "/")
        check("/sw.js tem bypass para /api/stream e Range", "/api/stream" in r_sw.text and "range" in r_sw.text)

        r_mani = client.get("/manifest.webmanifest")
        check("/manifest.webmanifest responde 200", r_mani.status_code == 200)

        r_painel = client.get("/painel")
        check("/painel exibe controles do Cloudflare Tunnel", "Cloudflare" in r_painel.text and "/painel/tunnel/toggle" in r_painel.text)

        r_stat = client.get("/api/tunnel/status")
        check("/api/tunnel/status responde JSON válido", r_stat.status_code == 200 and "status" in r_stat.json())

        r_csrf = client.post("/painel/tunnel/toggle", headers={"origin": "http://evil-site.com"})
        check("/painel/tunnel/toggle bloqueia CSRF de sites externos", r_csrf.status_code == 403)

        cf_bin = A._find_cloudflared()
        check("binário cloudflared localizável", cf_bin is not None and cf_bin.is_file())
    except Exception as e:
        check(f"falha nos testes de túnel/PWA: {e}", False)

    print(f"\n{'='*60}\n  {OKS} passaram, {len(FAILS)} falharam")
    if FAILS:
        print("  FALHAS: " + ", ".join(FAILS))
    print("=" * 60)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
