import sqlite3
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
import atexit
import time
from pathlib import Path

# importa o app.py da raiz do projeto sem depender do cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TEST_DATA_TMP = tempfile.TemporaryDirectory(prefix="stremmusic_test_", ignore_cleanup_errors=True)
atexit.register(_TEST_DATA_TMP.cleanup)
os.environ["DATA_DIR"] = _TEST_DATA_TMP.name
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

    # Testa gravação inteligente com metadados duradouros e isolamento
    A._bump_next("__prev_vid__", "__next_vid__", user_id=1, title="Música Futura", artist="Artista Top")
    with A.db() as con:
        row_bump = con.execute("SELECT title, artist, n FROM next_map WHERE user_id=1 AND prev_id=?", ("__prev_vid__",)).fetchone()
        check("next_map guarda título e artista duradouros", row_bump is not None and row_bump["title"] == "Música Futura")
        con.execute("DELETE FROM next_map WHERE prev_id=?", ("__prev_vid__",))

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
        check("/sw.js tem bypass para /painel", "/painel" in r_sw.text)
        check("/painel gera QR direto sem prefixo legado strem://", "strem://" not in r_painel.text)

        # Verifica comportamento com fechadura ligada (pairing_required=True) para IP externo
        prev_lock = A.SETTINGS.get("pairing_required")
        try:
            A.SETTINGS["pairing_required"] = True
            ext_headers = {"x-forwarded-for": "203.0.113.50"}
            r_locked = client.get("/", headers=ext_headers)
            check("com fechadura ligada, / bloqueia IP externo não pareado (403) com campo de chave",
                  r_locked.status_code == 403 and 'name="sk"' in r_locked.text)
            r_mani_ext = client.get("/manifest.webmanifest", headers=ext_headers)
            check("mesmo com fechadura ligada, /manifest.webmanifest passa sem cookie (200)",
                  r_mani_ext.status_code == 200)
            k = A._pair_key()
            r_mani_auth = client.get(f"/manifest.webmanifest?sk={k}", headers=ext_headers)
            check("/manifest.webmanifest autenticado inclui ?sk= no start_url (suporte iOS PWA)",
                  r_mani_auth.json().get("start_url") == f"/?sk={k}")
        finally:
            A.SETTINGS["pairing_required"] = prev_lock
    except Exception as e:
        check(f"falha nos testes de túnel/PWA: {e}", False)

    print("\n7) playlists do YouTube/YT Music: resiliência e mensagens claras")
    try:
        # Playlist informada pelo usuário (PLECHcdjzLJs8) agora importa com sucesso (200)
        r_pl = client.get("/api/ytplaylist?url=https%3A%2F%2Fmusic.youtube.com%2Fplaylist%3Flist%3DPLECHcdjzLJs8")
        check("playlist do usuário (PLECHcdjzLJs8) carrega com sucesso (200)", r_pl.status_code == 200 and len(r_pl.json().get("tracks", [])) > 0)

        # Playlist inexistente/inválida não deve estourar 502 Bad Gateway (deve responder 4xx)
        r_fake = client.get("/api/ytplaylist?url=https%3A%2F%2Fmusic.youtube.com%2Fplaylist%3Flist%3DPL_fake_id_xyz999")
        check("playlist inexistente/inválida responde 4xx (não 502)", r_fake.status_code in (400, 404))

        # Link de vídeo avulso no importador de playlist
        r_vid = client.get("/api/ytplaylist?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D0VH1Lim8gL8")
        check("link de vídeo avulso responde 400 avisando que é faixa", r_vid.status_code == 400 and "individual" in r_vid.json().get("detail", ""))

        # Limpeza de prefixo VL do YouTube Music
        check("_clean_playlist_id remove prefixo VL do YT Music", A._clean_playlist_id("VLPLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") == "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")
        check("_playlist_id_from_url extrai ID limpo de link com VL", A._playlist_id_from_url("https://music.youtube.com/playlist?list=VLPLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") == "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")
        check("_playlist_id_from_url aceita /browse/VL...", A._playlist_id_from_url("https://music.youtube.com/browse/VLPLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") == "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")
    except Exception as e:
        check(f"falha nos testes de playlist: {e}", False)

    print("\n8) capas de uploads próprios e estabilidade asyncio")
    try:
        # Uploads locais sem capa não devem dar 400 Bad Request
        r_up_thumb = client.get("/api/thumb/up_7f3bdbe631")
        check("/api/thumb/up_... sem capa responde 404 (não 400)", r_up_thumb.status_code == 404)
        check("id totalmente inválido responde 400", client.get("/api/thumb/bad-id").status_code == 400)

        # No Windows, o patch de conexão silenciosa deve estar ativo
        if sys.platform == "win32":
            from asyncio.proactor_events import _ProactorBasePipeTransport
            check("patch silencioso do ProactorBasePipeTransport ativo no Windows",
                  _ProactorBasePipeTransport._call_connection_lost.__name__ == "_quiet_call_connection_lost")
    except Exception as e:
        check(f"falha nos testes de upload/asyncio: {e}", False)

    print("\n9) sistema multi-usuário no painel (logins, QR codes e isolamento)")

    try:

        # 1. Usuário admin (padrão local) responde em /api/me

        r_me1 = client.get("/api/me")

        check("/api/me responde 200 para usuário local", r_me1.status_code == 200 and r_me1.json().get("ok"))

        u1 = r_me1.json().get("user", {})

        check("usuário local é admin", u1.get("is_admin") is True)



        # 2. Criação de novo usuário pelo painel

        r_new_u = client.post("/painel/users", data={"name": "Amigo Teste"}, follow_redirects=False)

        check("POST /painel/users cria usuário e redireciona (303)", r_new_u.status_code == 303)



        # 3. Busca token do novo usuário

        with sqlite3.connect(A.DB) as db:

            db.row_factory = sqlite3.Row
            row_u2 = db.execute("SELECT id, name, token FROM users WHERE name = 'Amigo Teste'").fetchone()

        check("usuário 'Amigo Teste' salvo no banco com token próprio", row_u2 is not None and len(row_u2["token"]) >= 8)

        u2_id = row_u2["id"]

        u2_tok = row_u2["token"]



        # 4. Painel exibe QR Code e card de ambos os usuários

        r_painel_users = client.get("/painel")

        check("/painel exibe card de Amigo Teste", "Amigo Teste" in r_painel_users.text)

        check("/painel contém link com token do amigo", f"?sk={u2_tok}" in r_painel_users.text)



        # 5. Acesso com token do amigo autentica como Amigo Teste

        r_me2 = client.get(f"/api/me?sk={u2_tok}")

        check("GET /api/me?sk=... reconhece Amigo Teste", r_me2.status_code == 200 and r_me2.json().get("user", {}).get("name") == "Amigo Teste")

        check("Amigo Teste não é admin", r_me2.json().get("user", {}).get("is_admin") is False)



        # 6. Isolamento de favoritos entre usuários (dois aparelhos / clientes distintos)
        client_amigo = TestClient(A.app)
        client.cookies.clear()

        client.delete("/api/favorites/vid_teste_admin")
        client.delete("/api/favorites/vid_teste_amigo")
        client.post("/api/favorites/vid_teste_admin?title=FaixaAdmin&artist=ArtistaAdmin")

        client_amigo.delete(f"/api/favorites/vid_teste_admin?sk={u2_tok}")
        client_amigo.delete(f"/api/favorites/vid_teste_amigo?sk={u2_tok}")
        client_amigo.post(f"/api/favorites/vid_teste_amigo?title=FaixaAmigo&artist=ArtistaAmigo&sk={u2_tok}")

        favs_admin = client.get("/api/favorites").json()
        favs_amigo = client_amigo.get("/api/favorites").json()

        vids_admin = [f.get("videoId") or f.get("video_id") for f in favs_admin]
        vids_amigo = [f.get("videoId") or f.get("video_id") for f in favs_amigo]

        check("favoritas do admin NÃO contém a faixa do amigo", "vid_teste_amigo" not in vids_admin and "vid_teste_admin" in vids_admin)
        check("favoritas do amigo NÃO contém a faixa do admin", "vid_teste_admin" not in vids_amigo and "vid_teste_amigo" in vids_amigo)

        # 7. Renovar QR Code (rekey)

        r_rekey = client.post(f"/painel/users/{u2_id}/rekey", follow_redirects=False)

        check("POST /painel/users/{id}/rekey responde 303", r_rekey.status_code == 303)

        with sqlite3.connect(A.DB) as db:

            db.row_factory = sqlite3.Row
            row_rekeyed = db.execute("SELECT token FROM users WHERE id = ?", (u2_id,)).fetchone()

        check("token do amigo mudou após rekey", row_rekeyed["token"] != u2_tok)



        # 8. Excluir usuário e verificar limpeza em cascata

        r_del = client.post(f"/painel/users/{u2_id}/delete", follow_redirects=False)

        check("POST /painel/users/{id}/delete responde 303", r_del.status_code == 303)

        with sqlite3.connect(A.DB) as db:

            db.row_factory = sqlite3.Row
            row_del = db.execute("SELECT id FROM users WHERE id = ?", (u2_id,)).fetchone()

            favs_left = db.execute("SELECT COUNT(*) FROM favorites WHERE user_id = ?", (u2_id,)).fetchone()[0]

        check("usuário removido do banco", row_del is None)

        check("favoritas do usuário excluído removidas em cascata", favs_left == 0)



        client.delete("/api/favorites/vid_teste_admin")

    except Exception as e:

        check(f"falha nos testes multi-usuário: {e}", False)

    print("\n10) segurança e bugs críticos (pacote 1)")
    try:
        # 1. Bloqueio de spoofing de IP por cliente não confiável
        lan_client = TestClient(A.app, client=("192.168.1.50", 54321))
        spoof_hdrs = {"x-forwarded-for": "127.0.0.1", "cf-connecting-ip": "127.0.0.1"}
        r_painel_spoof = lan_client.get("/painel", headers=spoof_hdrs)
        check("LAN com X-Forwarded-For/CF-Connecting-IP=127.0.0.1 NÃO acessa /painel (403)",
              r_painel_spoof.status_code == 403)

        prev_lock2 = A.SETTINGS.get("pairing_required")
        try:
            A.SETTINGS["pairing_required"] = True
            r_home_spoof = lan_client.get("/", headers=spoof_hdrs)
            check("com fechadura ligada, LAN com headers forjados NÃO burla / (403)",
                  r_home_spoof.status_code == 403)
        finally:
            A.SETTINGS["pairing_required"] = prev_lock2

        # 2. CSRF com Origin: null bloqueado no painel
        r_null_origin = client.post("/painel/toggle", headers={"origin": "null"})
        check("Origin: null bloqueado em POST do /painel (403)", r_null_origin.status_code == 403)

        # 3. XSS no /painel escapado nos cards de usuário
        xss_name = "XSS'\"><script>alert(9)</script>"
        client.post("/painel/users", data={"name": xss_name}, follow_redirects=False)
        with sqlite3.connect(A.DB) as db:
            db.row_factory = sqlite3.Row
            u_xss = db.execute("SELECT id, token FROM users WHERE name = ?", (xss_name,)).fetchone()
        try:
            r_p_xss = client.get("/painel")
            check("/painel escapa HTML/JS no nome de usuário",
                  u_xss is not None
                  and "<script>alert(9)</script>" not in r_p_xss.text
                  and "&lt;script&gt;alert(9)&lt;/script&gt;" in r_p_xss.text)
            # 4. /manifest.webmanifest inclui ?sk= para convidado
            r_mani_guest = client.get(f"/manifest.webmanifest?sk={u_xss['token']}",
                                      headers={"x-forwarded-for": "203.0.113.50"})
            check("/manifest.webmanifest inclui ?sk=<token> para usuário convidado",
                  r_mani_guest.json().get("start_url") == f"/?sk={u_xss['token']}")
        finally:
            if u_xss is not None:
                client.post(f"/painel/users/{u_xss['id']}/delete", follow_redirects=False)

        # 5. Validação de IDs (download exige VID_RE; favoritos/histórico/playlist bloqueiam path traversal)
        check("download com video_id inválido retorna 400",
              client.post("/api/download/bad_id_xyz").status_code == 400)
        check("favorito com path/chars inválidos retorna 400",
              client.post("/api/favorites/bad..id").status_code == 400)
        check("histórico com path/chars inválidos retorna 400",
              client.post("/api/history/bad..id").status_code == 400)
        check("playlist item com path/chars inválidos retorna 400",
              client.post("/api/playlists/1/items/bad..id").status_code == 400)

        # 6. _dl_art bloqueia redirect SSRF para 127.0.0.1
        orig_get = A.requests.get
        calls = []
        class _RedirectResp:
            is_redirect = True
            status_code = 302
            ok = False
            headers = {"location": "http://127.0.0.1:8787/capa.jpg"}
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def _fake_get(url, **kwargs):
            calls.append(url)
            return _RedirectResp()
        try:
            A.requests.get = _fake_get
            art_res = A._dl_art("https://1.1.1.1/cover.jpg")
            check("_dl_art bloqueia redirect HTTP para 127.0.0.1",
                  art_res is None and len(calls) == 1)
        finally:
            A.requests.get = orig_get

        # 7. logger definido para evitar NameError em Mix RD...
        check("A.logger definido para fallback de Mix RD...", getattr(A, "logger", None) is not None)

        # 8. _serve_from_cache devolve 416 quando Range ultrapassa tamanho do arquivo
        prev_cache_dir = A.CACHE_DIR
        prev_cache_on = A.SETTINGS.get("cache_enabled")
        try:
            with tempfile.TemporaryDirectory() as td:
                A.CACHE_DIR = Path(td)
                A.SETTINGS["cache_enabled"] = True
                (Path(td) / "Test0000001.audio").write_bytes(b"0123456789")
                (Path(td) / "Test0000001.meta.json").write_text('{"size": 10, "content_type": "audio/mp4"}', encoding="utf-8")
                r416 = A._serve_from_cache("Test0000001", "bytes=999-")
                check("_serve_from_cache retorna HTTP 416 quando start >= size",
                      r416 is not None and r416.status_code == 416 and r416.headers.get("content-range") == "bytes */10")
        finally:
            A.CACHE_DIR = prev_cache_dir
            A.SETTINGS["cache_enabled"] = prev_cache_on
    except Exception as e:
        check(f"falha nos testes de segurança/bugs (pacote 1): {e}", False)

    print("\n11) isolamento multiusuário e concorrência (pacote 2)")
    u_g_id = None
    adm_pl_id = None
    adm_st_id = None
    try:
        # Cria usuário convidado isolado
        client.post("/painel/users", data={"name": "Convidado Pacote2"}, follow_redirects=False)
        with sqlite3.connect(A.DB) as db:
            db.row_factory = sqlite3.Row
            row_g = db.execute("SELECT id, token FROM users WHERE name = 'Convidado Pacote2'").fetchone()
        check("usuário convidado criado para teste do pacote 2", row_g is not None)
        u_g_id = row_g["id"]
        g_tok = row_g["token"]
        g_client = TestClient(A.app)
        g_client.get(f"/api/me?sk={g_tok}")   # fixa cookie sk do convidado

        # 1. Playlists: convidado não pode alterar/excluir/baixar playlist do admin
        r_cpl = client.post("/api/playlists", params={"name": "__smoke_adm_pl__"})
        adm_pl_id = r_cpl.json().get("id")
        client.post(f"/api/playlists/{adm_pl_id}/items/dQw4w9WgXcQ", params={"title": "Keep"})
        check("convidado NÃO adiciona faixa na playlist do admin (403)",
              g_client.post(f"/api/playlists/{adm_pl_id}/items/0VH1Lim8gL8").status_code == 403)
        check("convidado NÃO remove faixa da playlist do admin (403)",
              g_client.delete(f"/api/playlists/{adm_pl_id}/items/dQw4w9WgXcQ").status_code == 403)
        check("convidado NÃO reordena playlist do admin (403)",
              g_client.post(f"/api/playlists/{adm_pl_id}/reorder", json={"order": ["dQw4w9WgXcQ"]}).status_code == 403)
        check("convidado NÃO faz bulk add na playlist do admin (403)",
              g_client.post(f"/api/playlists/{adm_pl_id}/items_bulk", json={"tracks": [{"videoId": "0VH1Lim8gL8"}]}).status_code == 403)
        check("convidado NÃO baixa playlist do admin (403)",
              g_client.post(f"/api/playlists/{adm_pl_id}/download").status_code == 403)
        check("convidado NÃO apaga playlist do admin (403)",
              g_client.delete(f"/api/playlists/{adm_pl_id}").status_code == 403)
        adm_pls = client.get("/api/playlists").json()
        adm_pl_obj = next((p for p in adm_pls if p["id"] == adm_pl_id), None)
        check("itens da playlist do admin seguem intactos após DELETE negado",
              adm_pl_obj is not None and len(adm_pl_obj.get("items") or []) == 1)

        # 2. Rotas admin bloqueadas para convidado (403)
        check("convidado NÃO altera /api/settings (403)",
              g_client.post("/api/settings", json={"cache_limit_mb": 100}).status_code == 403)
        check("convidado NÃO limpa /api/cache (403)",
              g_client.delete("/api/cache").status_code == 403)
        check("convidado NÃO apaga item /api/cache/{vid} (403)",
              g_client.delete("/api/cache/dQw4w9WgXcQ").status_code == 403)
        check("convidado NÃO dispara /api/clients/probe (403)",
              g_client.post("/api/clients/probe").status_code == 403)
        check("convidado NÃO dispara /api/clients/reset (403)",
              g_client.post("/api/clients/reset").status_code == 403)
        check("convidado NÃO exclui download (403)",
              g_client.delete("/api/downloads/fake.mp3").status_code == 403)
        check("convidado NÃO exclui pasta de download (403)",
              g_client.delete("/api/downloads/folders/fake").status_code == 403)
        check("convidado NÃO envia upload (403)",
              g_client.post("/api/uploads", files={"files": ("a.mp3", b"123", "audio/mpeg")}).status_code == 403)
        check("convidado NÃO edita upload (403)",
              g_client.post("/api/uploads/up_abcdef0123/edit", json={"title": "x"}).status_code == 403)
        check("convidado NÃO exclui upload (403)",
              g_client.delete("/api/uploads/up_abcdef0123").status_code == 403)

        # 3. Isolamento de estações (stations)
        r_st_adm = client.post("/api/stations", json={"name": "__smoke_st_adm__", "videoId": "dQw4w9WgXcQ"})
        adm_st_id = r_st_adm.json().get("id")
        r_st_g = g_client.post("/api/stations", json={"name": "__smoke_st_gst__", "videoId": "0VH1Lim8gL8"})
        gst_st_id = r_st_g.json().get("id")
        g_sts = [s["name"] for s in g_client.get("/api/stations").json()]
        a_sts = [s["name"] for s in client.get("/api/stations").json()]
        check("estações isoladas: convidado vê só as suas e admin só as dele",
              "__smoke_st_gst__" in g_sts and "__smoke_st_adm__" not in g_sts and "__smoke_st_gst__" not in a_sts)
        check("convidado NÃO apaga estação do admin (404)",
              g_client.delete(f"/api/stations/{adm_st_id}").status_code == 404)
        if gst_st_id:
            g_client.delete(f"/api/stations/{gst_st_id}")

        # 4. _known_blend isolado por user_id
        client.post("/api/favorites/AdmBlend001?title=AdmTrack")
        g_client.post("/api/favorites/GstBlend002?title=GstTrack")
        blended_guest = A._known_blend([], "dQw4w9WgXcQ", user_id=u_g_id, k=10)
        blend_ids = [t.get("videoId") for t in blended_guest]
        check("_known_blend filtra estritamente por user_id (sem vazar favoritos do admin)",
              "GstBlend002" in blend_ids and "AdmBlend001" not in blend_ids)

        # 5. Exclusão de upload limpa referências órfãs
        fake_up_id = "up_0011223344"
        with sqlite3.connect(A.DB) as db:
            db.execute("INSERT OR REPLACE INTO uploads (id, file, title, artist) VALUES (?,?,?,?)",
                       (fake_up_id, "downloads/upload/up_0011223344.mp3", "FakeUp", "Artist"))
            db.execute("INSERT OR REPLACE INTO favorites (user_id, video_id, title, artist) VALUES (?,?,?,?)",
                       (1, fake_up_id, "FakeUp", "Artist"))
        r_del_up = client.delete(f"/api/uploads/{fake_up_id}")
        with sqlite3.connect(A.DB) as db:
            orphan_cnt = db.execute("SELECT COUNT(*) FROM favorites WHERE video_id=?", (fake_up_id,)).fetchone()[0]
        check("delete_upload remove referências órfãs em favorites/history/playlists",
              r_del_up.status_code == 200 and orphan_cnt == 0)

        # 6. Primitivas de concorrência presentes (_warm_cond, _mem_cache_lock, _settings_lock, _last_seen_lock)
        check("_warm_cond (threading.Condition) e locks ativos",
              isinstance(A._warm_cond, A.threading.Condition)
              and hasattr(A, "_mem_cache_lock")
              and hasattr(A, "_settings_lock")
              and hasattr(A, "_last_seen_lock"))
    except Exception as e:
        check(f"falha nos testes do pacote 2: {e}", False)
    finally:
        client.delete("/api/favorites/AdmBlend001")
        if adm_pl_id:
            client.delete(f"/api/playlists/{adm_pl_id}")
        if adm_st_id:
            client.delete(f"/api/stations/{adm_st_id}")
        if u_g_id:
            client.post(f"/painel/users/{u_g_id}/delete", follow_redirects=False)

    print("\n12) extração de stream, fallback anti-403 e refcount de _wanted")
    check("PLAYER_CLIENTS prioriza padrão (None) na frente",
          A.PLAYER_CLIENTS[0] is None)

    A._want("RefVid00001")
    A._want("RefVid00001")
    A._unwant("RefVid00001")
    kept = "RefVid00001" in A._wanted
    A._unwant("RefVid00001")
    cleared = "RefVid00001" not in A._wanted
    check("_want/_unwant usa contagem de referência (não remove antes da última requisição)",
          kept and cleared)

    orig_ydl = A.yt_dlp.YoutubeDL
    class _FakeVideoOnlyYDL:
        def __init__(self, opts):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download=False):
            return {
                "url": "https://example.googlevideo.com/videoplayback?itag=18",
                "protocol": "https",
                "vcodec": "avc1.42001E",
                "acodec": "mp4a.40.2",
                "formats": [
                    {"url": "https://example.googlevideo.com/videoplayback?itag=18",
                     "protocol": "https", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2"}
                ],
            }
    try:
        A.yt_dlp.YoutubeDL = _FakeVideoOnlyYDL
        rejected = False
        try:
            A._extract_one("dQw4w9WgXcQ", ["android_vr"], ejs=False)
        except RuntimeError as e:
            rejected = "sem áudio direto" in str(e)
        check("_extract_one rejeita vídeo+áudio (ex.: itag=18) quando não há áudio direto", rejected)
    finally:
        A.yt_dlp.YoutubeDL = orig_ydl

    class _FakeResp:
        def __init__(self, st, hdrs=None, chunks=None):
            self.status_code = st
            self.headers = hdrs or {}
            self._chunks = list(chunks or [])
        def iter_content(self, chunk_size=0):
            return iter(self._chunks)
        def close(self):
            pass

    orig_get = A.session.get
    seen_ranges = []
    def _fake_get_range_fallback(url, headers=None, **kw):
        rng_h = (headers or {}).get("Range")
        seen_ranges.append(rng_h)
        if rng_h == "bytes=0-":
            return _FakeResp(403)
        return _FakeResp(200, {"Content-Type": "audio/mp4", "Content-Length": "4"}, [b"abcd"])
    try:
        A.session.get = _fake_get_range_fallback
        r_up, _ = A.open_upstream("dQw4w9WgXcQ", "bytes=0-", data={"url": "https://rr.googlevideo.com/videoplayback", "client": "padrão", "headers": {}})
        check("open_upstream remove Range: bytes=0- após 403 e obtém 200 sem Range",
              r_up.status_code == 200 and seen_ranges == ["bytes=0-", None], f"-> {seen_ranges} / {r_up.status_code}")
    finally:
        A.session.get = orig_get

    from fastapi.testclient import TestClient
    tc = TestClient(A.app)
    orig_get_stream = A.get_stream_data
    orig_sess_get = A.session.get
    last_ok_bak = dict(A._last_ok)
    client_ms_bak = dict(A._CLIENT_MS)
    health_bak = {k: dict(v) for k, v in A._client_health.items()}
    stream_calls = []
    def _fake_gsd(vid, force=False, avoid=(), ignore_cooldown=False, urgent=False):
        avoid = set(avoid)
        for lbl in ("mweb", "android_vr", "padrão"):
            if lbl not in avoid:
                stream_calls.append(lbl)
                return {"url": f"https://{lbl}.googlevideo.com/videoplayback", "client": lbl, "headers": {}, "alts": []}
        raise RuntimeError("sem clients")
    def _fake_sess_swap(url, headers=None, **kw):
        if "padrão.googlevideo.com" in url:
            return _FakeResp(206, {"Content-Type": "audio/mp4", "Content-Range": "bytes 0-3/4", "Content-Length": "4"}, [b"data"])
        return _FakeResp(403)
    try:
        A.get_stream_data = _fake_gsd
        A.session.get = _fake_sess_swap
        with A._cache_lock:
            A._bad_clients.pop("SwapTest001", None)
            A._stream_cache.pop("SwapTest001", None)
        rs = tc.get("/api/stream/SwapTest001", headers={"Range": "bytes=0-"}, follow_redirects=False)
        check("stream percorre clients até achar quem não dá 403 (sem travar na 1ª troca)",
              rs.status_code == 206 and rs.content == b"data"
              and stream_calls == ["mweb", "android_vr", "padrão"]
              and A._last_ok.get("label") == "padrão",
              f"status={rs.status_code} calls={stream_calls} last_ok={A._last_ok}")
    finally:
        A.get_stream_data = orig_get_stream
        A.session.get = orig_sess_get
        A._last_ok.clear(); A._last_ok.update(last_ok_bak)
        with A._health_lock:
            A._CLIENT_MS.clear(); A._CLIENT_MS.update(client_ms_bak)
            A._client_health.clear(); A._client_health.update(health_bak)
        with A._cache_lock:
            A._bad_clients.pop("SwapTest001", None)
            A._stream_cache.pop("SwapTest001", None)
        tc.delete("/api/cache/SwapTest001")
        with sqlite3.connect(A.DB) as db:
            db.execute("DELETE FROM plays WHERE video_id=?", ("SwapTest001",))

    print("\n13) endurecimento de segurança (pacote 1 revisado)")
    prev_lock3 = A.SETTINGS.get("pairing_required")
    prev_pk3 = A.SETTINGS.get("pair_key")
    _settings_bak3 = A.SETTINGS_FILE.read_bytes() if A.SETTINGS_FILE.exists() else None
    with sqlite3.connect(A.DB) as db:
        _u1_tok_bak = (db.execute("SELECT token FROM users WHERE id=1").fetchone() or [None])[0]
    u_sec_id = None
    _rl_bak = dict(A._RATE_LIMITS)
    idx_file = A.DL_DIR / ".index.json"
    idx_existed = idx_file.exists()
    try:
        A.SETTINGS["pairing_required"] = False
        lan_anon = TestClient(A.app, client=("192.168.1.77", 55555))

        r_lan_me = lan_anon.get("/api/me")
        j_lan_me = r_lan_me.json()
        check("LAN anônimo com fechadura desligada NÃO vira admin (id=0, is_admin=False)",
              r_lan_me.status_code == 200 and j_lan_me.get("id") == 0 and j_lan_me.get("is_admin") is False,
              f"-> {j_lan_me}")

        r_lan_set = lan_anon.get("/api/settings")
        j_lan_set = r_lan_set.json()
        check("LAN anônimo recebe /api/settings mínimo (sem cache_dir nem ytdlp_version)",
              r_lan_set.status_code == 200
              and set((j_lan_set.get("settings") or {}).keys()) == {"learn_next"}
              and "cache_dir" not in j_lan_set and "ytdlp_version" not in j_lan_set,
              f"-> {j_lan_set}")

        check("LAN anônimo bloqueado (403) em POST /api/settings",
              lan_anon.post("/api/settings", json={"cache_limit_mb": 100}).status_code == 403)
        check("LAN anônimo bloqueado (403) em GET /api/cache/stats",
              lan_anon.get("/api/cache/stats").status_code == 403)
        check("LAN anônimo bloqueado (403) em GET /api/clients/health",
              lan_anon.get("/api/clients/health").status_code == 403)
        check("LAN anônimo bloqueado (403) em GET /api/tunnel/status",
              lan_anon.get("/api/tunnel/status").status_code == 403)
        check("LAN anônimo bloqueado (403) em POST /api/download/{vid}",
              lan_anon.post("/api/download/dQw4w9WgXcQ").status_code == 403)
        check("LAN anônimo bloqueado (403) em POST /api/ytplaylist/download",
              lan_anon.post("/api/ytplaylist/download", json={"url": "PL_fake"}).status_code == 403)
        check("LAN anônimo bloqueado (403) em GET /api/ytplaylist/job/{jid}",
              lan_anon.get("/api/ytplaylist/job/fakejob").status_code == 403)
        check("LAN anônimo bloqueado (403) em POST /api/ytplaylist/job/{jid}/cancel",
              lan_anon.post("/api/ytplaylist/job/fakejob/cancel").status_code == 403)

        if not idx_existed:
            idx_file.write_text("{}", encoding="utf-8")
        check("GET /downloads/.index.json bloqueado com 404 (dotfile/não-áudio)",
              tc.get("/downloads/.index.json").status_code == 404)

        tc.post("/painel/users", data={"name": "Convidado Sec13"}, follow_redirects=False)
        with sqlite3.connect(A.DB) as db:
            db.row_factory = sqlite3.Row
            row_s = db.execute("SELECT id, token FROM users WHERE name='Convidado Sec13'").fetchone()
        u_sec_id = row_s["id"]
        s_tok = row_s["token"]
        gst_c = TestClient(A.app, client=("192.168.1.88", 55556))
        gst_c.get(f"/api/me?sk={s_tok}")

        r_g_set = gst_c.get("/api/settings")
        j_g_set = r_g_set.json()
        check("convidado autenticado recebe /api/settings reduzido (sem cache_dir/ytdlp_version)",
              r_g_set.status_code == 200
              and set((j_g_set.get("settings") or {}).keys()) == {"learn_next"}
              and "cache_dir" not in j_g_set and "ytdlp_version" not in j_g_set)
        check("convidado autenticado bloqueado (403) em cache/clients/tunnel/downloads/jobs",
              gst_c.get("/api/cache/stats").status_code == 403
              and gst_c.get("/api/clients/health").status_code == 403
              and gst_c.get("/api/tunnel/status").status_code == 403
              and gst_c.post("/api/download/dQw4w9WgXcQ").status_code == 403
              and gst_c.post("/api/ytplaylist/download", json={"url": "PL_fake"}).status_code == 403
              and gst_c.get("/api/ytplaylist/job/fakejob").status_code == 403
              and gst_c.post("/api/ytplaylist/job/fakejob/cancel").status_code == 403)

        # POST /painel/rekey sincroniza pair_key + users.token (id=1)
        old_pk = A._pair_key()
        r_rk = tc.post("/painel/rekey", headers={"origin": "http://testserver"})
        new_pk = r_rk.json().get("pair_key") if r_rk.status_code == 200 else None
        with sqlite3.connect(A.DB) as db:
            u1_new_tok = (db.execute("SELECT token FROM users WHERE id=1").fetchone() or [None])[0]
        check("POST /painel/rekey sincroniza SETTINGS['pair_key'] e users.token (id=1)",
              r_rk.status_code == 200 and new_pk and new_pk != old_pk
              and A.SETTINGS.get("pair_key") == new_pk and u1_new_tok == new_pk)

        # Headers de segurança e cookie Secure sob HTTPS (X-Forwarded-Proto: https de proxy confiável)
        r_https = tc.get(f"/?sk={s_tok}", headers={"x-forwarded-proto": "https"})
        sc_hdr = r_https.headers.get("set-cookie") or ""
        check("cabeçalhos de segurança (nosniff, SAMEORIGIN, same-origin) presentes e cookie Secure sob HTTPS",
              r_https.headers.get("x-content-type-options") == "nosniff"
              and r_https.headers.get("x-frame-options") == "SAMEORIGIN"
              and r_https.headers.get("referrer-policy") == "same-origin"
              and "Secure" in sc_hdr and "HttpOnly" in sc_hdr,
              f"headers={dict(r_https.headers)}")

        # Rate limit (429) em endpoint pesado
        A._RATE_LIMITS["ytplaylist"] = (2, 60.0)
        with A._rl_lock:
            A._rl_hits.clear()
        rl_hdr = {"x-forwarded-for": "198.51.100.99"}
        r_rl1 = tc.get("/api/ytplaylist?url=bad", headers=rl_hdr)
        r_rl2 = tc.get("/api/ytplaylist?url=bad", headers=rl_hdr)
        r_rl3 = tc.get("/api/ytplaylist?url=bad", headers=rl_hdr)
        check("rate limiting devolve HTTP 429 após exceder janela",
              r_rl1.status_code == 400 and r_rl2.status_code == 400 and r_rl3.status_code == 429,
              f"statuses={[r_rl1.status_code, r_rl2.status_code, r_rl3.status_code]}")

        import subprocess
        git_ls = subprocess.run(["git", "ls-files", "settings.json"], capture_output=True, text=True, cwd=str(A.BASE))
        check("settings.json existe no disco e não está rastreado pelo git",
              (A.SETTINGS_FILE.is_file() or (A.BASE / "settings.json").is_file())
              and git_ls.returncode == 0 and git_ls.stdout.strip() == "")
    finally:
        A._RATE_LIMITS.clear()
        A._RATE_LIMITS.update(_rl_bak)
        with A._rl_lock:
            A._rl_hits.clear()
        A.SETTINGS["pairing_required"] = prev_lock3
        if prev_pk3 is not None:
            A.SETTINGS["pair_key"] = prev_pk3
        if _u1_tok_bak is not None:
            with sqlite3.connect(A.DB) as db:
                db.execute("UPDATE users SET token=? WHERE id=1", (_u1_tok_bak,))
        if _settings_bak3 is not None:
            A.SETTINGS_FILE.write_bytes(_settings_bak3)
        if not idx_existed:
            idx_file.unlink(missing_ok=True)
        if u_sec_id is not None:
            tc.post(f"/painel/users/{u_sec_id}/delete", follow_redirects=False)

    print("\n14) escala, banco indexado, cache 10 GB e fila de downloads (pacote 2)")
    expected_idxs = {
        "idx_cache_entries_at",
        "idx_favorites_user_added",
        "idx_history_user_played",
        "idx_local_playlists_user",
        "idx_playlist_items_pl_pos",
        "idx_stations_user",
        "idx_next_map_user_prev_n",
        "idx_plays_n_last",
    }
    with sqlite3.connect(A.DB) as db:
        has_cache_tbl = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache_entries'"
        ).fetchone() is not None
        found_idxs = {
            r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    check("tabela cache_entries existe no SQLite", has_cache_tbl)
    check("todos os 8 índices secundários existem no SQLite",
          expected_idxs.issubset(found_idxs),
          f"faltam={sorted(expected_idxs - found_idxs)}")

    _set_bak4 = dict(A.SETTINGS)
    _set_file_bak4 = A.SETTINGS_FILE.read_bytes() if A.SETTINGS_FILE.exists() else None
    try:
        r_10g = tc.post("/api/settings", json={"cache_limit_mb": 10240})
        r_clamp = tc.post("/api/settings", json={"cache_limit_mb": 20000})
        check("POST /api/settings aceita cache_limit_mb=10240 e limita 20000 a 10240",
              r_10g.status_code == 200 and r_10g.json().get("settings", {}).get("cache_limit_mb") == 10240
              and r_clamp.status_code == 200 and r_clamp.json().get("settings", {}).get("cache_limit_mb") == 10240)
    finally:
        A.SETTINGS.clear()
        A.SETTINGS.update(_set_bak4)
        if _set_file_bak4 is not None:
            A.SETTINGS_FILE.write_bytes(_set_file_bak4)

    test_cid = "CacheIdx001"
    audio_p, meta_p = A._cache_paths(test_cid)
    prev_cache_on4 = A.SETTINGS.get("cache_enabled")
    try:
        A.SETTINGS["cache_enabled"] = True
        audio_p.write_bytes(b"0123456789abcdef")
        now_idx = time.time()
        A._cache_write_meta(test_cid, {
            "video_id": test_cid, "content_type": "audio/mp4", "size": 16,
            "created": now_idx, "at": now_idx, "title": "Track Idx", "artist": "Art Idx", "client": "padrão",
        })
        with sqlite3.connect(A.DB) as db:
            in_db = db.execute("SELECT size, title FROM cache_entries WHERE video_id=?", (test_cid,)).fetchone()
        hit_m = A._cache_lookup(test_cid)
        stats_j = tc.get("/api/cache/stats").json()
        in_stats = any(it.get("video_id") == test_cid for it in (stats_j.get("items") or []))
        with A._cache_touch_lock:
            A._cache_touch_mem.pop(test_cid, None)
        rs_rng = A._serve_from_cache(test_cid, "bytes=0-3")
        with A._cache_touch_lock:
            touched_1 = A._cache_touch_mem.get(test_cid)
        rs_rng2 = A._serve_from_cache(test_cid, "bytes=4-7")
        with A._cache_touch_lock:
            touched_2 = A._cache_touch_mem.get(test_cid)
        del_ok = A._cache_delete(test_cid)
        with sqlite3.connect(A.DB) as db:
            after_del = db.execute("SELECT 1 FROM cache_entries WHERE video_id=?", (test_cid,)).fetchone()
        check("cache indexado grava, consulta em cache_entries/stats, aplica throttle no _touch e remove no delete",
              in_db is not None and in_db[0] == 16
              and hit_m is not None and hit_m.get("title") == "Track Idx"
              and in_stats
              and rs_rng is not None and rs_rng.status_code == 206
              and rs_rng2 is not None and rs_rng2.status_code == 206
              and touched_1 is not None and touched_2 == touched_1
              and del_ok and after_del is None)
    finally:
        A.SETTINGS["cache_enabled"] = prev_cache_on4
        A._cache_delete(test_cid)

    check("_pl_dl_pool limita downloads de playlist a no máximo 2 workers simultâneos",
          isinstance(A._pl_dl_pool, A.cf.ThreadPoolExecutor) and A._pl_dl_pool._max_workers == 2)

    print("\n15) deploy, isolamento de DATA_DIR e modularização (pacote 3)")
    check("DATA_DIR nos testes está isolado em diretório temporário fora da raiz",
          A.DATA.resolve() != A.BASE.resolve() and "stremmusic_test_" in str(A.DATA))

    r_css = tc.get("/static/app.css")
    r_js = tc.get("/static/app.js")
    check("GET /static/app.css e /static/app.js respondem 200 com Content-Type correto",
          r_css.status_code == 200 and "text/css" in (r_css.headers.get("content-type") or "")
          and r_js.status_code == 200 and "javascript" in (r_js.headers.get("content-type") or ""))

    r_sw_p3 = tc.get("/sw.js")
    check("/sw.js lista /static/app.css e /static/app.js no SHELL_ASSETS",
          "/static/app.css" in r_sw_p3.text and "/static/app.js" in r_sw_p3.text)

    prev_base = A.BASE
    try:
        with tempfile.TemporaryDirectory() as ro_dir:
            ro_path = Path(ro_dir)
            A.BASE = ro_path
            # simula filesystem onde write falhe ou manifest não exista em BASE
            manifest_copy = ro_path / "manifest.webmanifest"
            if manifest_copy.exists():
                manifest_copy.unlink()
            orig_ensure = A._ensure_assets
            try:
                A._ensure_assets = lambda: None
                r_mf_ro = tc.get("/manifest.webmanifest")
            finally:
                A._ensure_assets = orig_ensure
            check("GET /manifest.webmanifest devolve 200 via fallback embutido mesmo sem arquivo em BASE",
                  r_mf_ro.status_code == 200 and r_mf_ro.json().get("name") == "StremMusic")
    finally:
        A.BASE = prev_base

    import pwa_assets
    dockerfile_txt = (A.BASE / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    check("pwa_assets importado e Dockerfile copia pwa_assets.py/manifest e define USER stremmusic",
          hasattr(pwa_assets, "_EMBEDDED_ASSETS")
          and "pwa_assets.py" in dockerfile_txt
          and "manifest.webmanifest" in dockerfile_txt
          and "USER stremmusic" in dockerfile_txt)

    print(f"\n{'='*60}\n  {OKS} passaram, {len(FAILS)} falharam")
    if FAILS:
        print("  FALHAS: " + ", ".join(FAILS))
    print("=" * 60)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
