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



    print(f"\n{'='*60}\n  {OKS} passaram, {len(FAILS)} falharam")
    if FAILS:
        print("  FALHAS: " + ", ".join(FAILS))
    print("=" * 60)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
