#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StremMusic — backend (FastAPI + yt-dlp + ytmusicapi)

Variáveis de ambiente (todas opcionais):
  YT_PROXY=http://usuario:senha@host:porta   → sair por um proxy
  YT_CLIENTS=android_vr,tv                   → sobrescreve a ordem de clients do yt-dlp
  FORCE_IPV4=1                               → força IPv4
  USE_COOKIES=1 (com cookies.txt na pasta)   → usa cookies do navegador
  AUTO_UPDATE_YTDLP=1                        → atualiza o yt-dlp automaticamente ao iniciar

A aba "Config" do app grava settings.json na pasta do app (cache local + limite,
clients em paralelo, timeout por onda, IPv4, cookies, qualidade dos MP3) — as
variáveis de ambiente acima valem só como padrão inicial.

Playlist do YouTube/YT Music: cola o link (?list=…) na busca ou na aba Playlists
para importar/adicionar; "Baixar tudo" cria downloads/playlists/<nome>/ e pré-baixa
as próximas faixas no cache para a leitura sequencial não engasgar.
"""
import os, sys, json, re, uuid, shutil, socket, sqlite3, time, threading, subprocess, datetime, random, secrets, ipaddress, html, logging

logger = logging.getLogger("stremmusic")

# Garante que o console Windows nunca quebre em cp1252 com emojis ou caracteres especiais
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # No Windows, quando o cliente (navegador/celular) cancela o stream de áudio
    # ou pula de faixa no meio do buffering, o Winsock dispara WSAECONNRESET (10054).
    # O asyncio padrão tenta chamar sock.shutdown() no socket já resetado e emite
    # um traceback inócuo no console. O tratamento abaixo silencia esse erro esperado.
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _quiet_call_connection_lost(self, exc):
            try:
                _orig_call_connection_lost(self, exc)
            except (ConnectionResetError, OSError):
                try:
                    if self._sock is not None:
                        self._sock.close()
                        self._sock = None
                    self._called_connection_lost = True
                except Exception:
                    pass

        _ProactorBasePipeTransport._call_connection_lost = _quiet_call_connection_lost
    except Exception:
        pass
import importlib
import urllib.parse
import concurrent.futures as cf
from contextlib import contextmanager
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from fastapi import (FastAPI, HTTPException, Query, Request, UploadFile, File)
from fastapi.responses import (HTMLResponse, FileResponse, StreamingResponse,
                               Response, RedirectResponse)
from starlette.concurrency import run_in_threadpool
from ytmusicapi import YTMusic

# ---- yt-dlp + plugin de PO Token: carga ÚNICA, à prova de corrida ----
# O loader de plugins do yt-dlp re-executa os módulos de plugin a cada
# YoutubeDL() criado, SEM trava — com as nossas extrações em paralelo, uma
# thread via o namespace do plugin PELA METADE da re-execução de outra e
# explodia "ImportError: cannot import name 'BgUtilPTPBase'" (reproduzido
# com 8 threads concorrentes). Solução: desligamos o auto-loader
# (YTDLP_NO_PLUGINS) e importamos o plugin UMA VEZ, aqui, sem concorrência —
# o registro dele é global e vale para todas as instâncias do processo.
os.environ["YTDLP_NO_PLUGINS"] = "1"
import yt_dlp

def _load_pot_plugin_once():
    for mod in ("yt_dlp_plugins.extractor.getpot_bgutil",
                "yt_dlp_plugins.extractor.getpot_bgutil_script",
                "yt_dlp_plugins.extractor.getpot_bgutil_http"):
        try:
            importlib.import_module(mod)
        except Exception:
            return False
    return True

_POT_PLUGIN_OK = _load_pot_plugin_once()

# ---- Servidor PO Token persistente (modo HTTP do bgutil, porta 4416) ----
# No modo script, cada token spawna um subprocesso Deno: sob extrações
# concorrentes isso vira "timeout (todas as ondas)" em cascata. No modo
# servidor, o Deno fica RODANDO com cache de token entre vídeos/processos —
# medido nesta máquina: 10,2s para resolver a 1ª faixa contra 16,2s sem ele.
# O provedor bgutil:http do plugin já procura 127.0.0.1:4416 sozinho.
def _resolve_bgutil_dir() -> Path:
    for cand in (
        Path.home() / "bgutil-ytdlp-pot-provider" / "server",
        Path("/opt/bgutil-ytdlp-pot-provider/server"),
        Path("/root/bgutil-ytdlp-pot-provider/server"),
    ):
        if (cand / "src" / "main.ts").is_file():
            return cand
    return Path.home() / "bgutil-ytdlp-pot-provider" / "server"

BGUTIL_SERVER_DIR = _resolve_bgutil_dir()
_BGUTIL_SERVER_OK = False

def _bgutil_ping() -> bool:
    try:
        return requests.get("http://127.0.0.1:4416/ping", timeout=2).ok
    except Exception:
        return False

def _start_bgutil_server() -> bool:
    if _bgutil_ping():
        return True
    if not (BGUTIL_SERVER_DIR / "src" / "main.ts").is_file():
        return False
    deno = shutil.which("deno")
    if not deno:
        return False
    try:
        subprocess.Popen(
            [deno, "run", "--allow-env", "--allow-net", "--allow-ffi=.",
             "--allow-read=.", "src/main.ts"],
            cwd=str(BGUTIL_SERVER_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    for _ in range(60):            # 1º boot compila (~50s); depois é instantâneo
        if _bgutil_ping():
            return True
        time.sleep(1.5)
    return False

def _bgutil_boot():
    global _BGUTIL_SERVER_OK
    _BGUTIL_SERVER_OK = _start_bgutil_server()
    if _BGUTIL_SERVER_OK:
        print("  🔑 servidor PO Token persistente em :4416 ✓ (tokens com cache entre vídeos)")
    else:
        print("  🔑 PO Token em modo script (fallback — sem o servidor HTTP em :4416)")

if _POT_PLUGIN_OK:
    threading.Thread(target=_bgutil_boot, daemon=True).start()

BASE = Path(__file__).parent
# ---- Dados separados do código (para servidor/container) ----
# Sem DATA_DIR, tudo continua onde sempre foi (a pasta do app) — nada muda para
# quem já usa. Com DATA_DIR=/data, o BANCO, as configurações, o cache e os
# downloads vão para lá, e a pasta do código pode ser somente-leitura (imagem de
# container, /opt protegido, volume persistente).
DATA = Path(os.environ.get("DATA_DIR") or BASE).expanduser()
try:
    DATA.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA = BASE
# metrolist.db mantém o nome antigo DE PROPÓSITO: é o banco com favoritos/playlists
# que o usuário já tem — trocar o nome perderia os dados dele.
DB = DATA / "metrolist.db"
DL_DIR = DATA / "downloads"
DL_DIR.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# ---------- Rede: IPv4 por padrão nas sessões internas ----------
# Diagnóstico real (out/2026): em rede dual-stack com IPv6 quebrado, o TCP
# connect para music.youtube.com fica pendurado ~2 MINUTOS antes de cair pro
# IPv4 — a busca chegou a levar 170s (aparecia como "Busca falhou ()" vazio,
# porque o timeout estourava antes). Forçar IPv4 nas sessões internas elimina
# o stall. Escape hatch: ALLOW_IPV6=1 volta ao comportamento dual-stack.
_ALLOW_IPV6 = os.environ.get("ALLOW_IPV6") == "1"

class _TimeoutSession(requests.Session):
    """Nenhuma chamada interna sem timeout: pendurar é pior que falhar."""
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", 15)
        return super().request(*args, **kwargs)

def _mount_ipv4(s: requests.Session) -> requests.Session:
    if not _ALLOW_IPV6:
        import urllib3
        def _v4_adapter(pools=8, maxsize=32):
            a = HTTPAdapter(pool_connections=pools, pool_maxsize=maxsize)
            # source_address "0.0.0.0" (AF_INET): os sockets abrem só em IPv4
            a.poolmanager = urllib3.PoolManager(num_pools=pools, maxsize=maxsize,
                                                source_address=("0.0.0.0", 0))
            return a
        s.mount("https://", _v4_adapter())
        s.mount("http://", _v4_adapter(maxsize=16))
    return s

# ---------- Índice de downloads (video_id → arquivo baixado) ----------
# Ideia estilo Koel/Navidrome: música baixada é BIBLIOTECA LOCAL, não cache.
# O índice permite o /api/stream servir o MP3 direto do disco (sem YouTube,
# sem gastar cache) e a aba Downloads oferecer "Tocar" em cada arquivo.
_DL_INDEX_FILE = DL_DIR / ".index.json"
_dl_index: dict[str, dict] = {}
_dl_index_lock = threading.RLock()

def _dl_index_load():
    with _dl_index_lock:
        if _dl_index:
            return _dl_index
        try:
            d = json.loads(_DL_INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _dl_index.update(d)
        except Exception:
            pass
        return _dl_index

def _dl_index_save():
    try:
        tmp = DL_DIR / f".index.{uuid.uuid4().hex[:6]}.tmp"
        tmp.write_text(json.dumps(_dl_index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _DL_INDEX_FILE)
    except Exception as e:
        print(f"⚠️ índice de downloads não salvou: {e}")

def _dl_index_add(video_id: str, path: Path):
    if not VID_RE.match(video_id or ""):
        return
    with _dl_index_lock:
        _dl_index_load()
        try:
            rel = str(path.resolve().relative_to(DATA.resolve()))
        except Exception:
            return
        if _dl_index.get(video_id, {}).get("file") == rel:
            return
        _dl_index[video_id] = {"file": rel, "added": time.time()}
        _dl_index_save()

def _dl_index_get(video_id: str):
    with _dl_index_lock:
        rec = _dl_index_load().get(video_id)
        if not rec:
            return None
        p = (DATA / rec["file"]).resolve()
    try:
        in_dl = p == DL_DIR.resolve() or DL_DIR.resolve() in p.parents
    except Exception:
        return None
    return p if (in_dl and p.is_file() and p.stat().st_size > 0) else None

YT_PROXY = os.environ.get("YT_PROXY")                      # ex: http://usuario:senha@host:porta
FORCE_IPV4 = os.environ.get("FORCE_IPV4") == "1"
USE_COOKIES = os.environ.get("USE_COOKIES") == "1"
COOKIES_FILE = DATA / "cookies.txt"
AUTO_UPDATE_YTDLP = os.environ.get("AUTO_UPDATE_YTDLP") == "1"

app = FastAPI(title="StremMusic")   # cedo: TODAS as rotas abaixo se registram aqui

# ---------- Acesso opcional por token (APP_TOKEN) ----------
# Sem APP_TOKEN definido, tudo funciona como sempre (rede local/confiança).
# Com APP_TOKEN=uma_senha_longa no ambiente, TODA rota /api/* passa a exigir o
# token (cookie sm_token definido pelo próprio frontend, ?token=… ou Bearer).
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()

def _tok_ok(tok: str) -> bool:
    try:
        return secrets.compare_digest(str(tok or ""), APP_TOKEN)
    except Exception:
        return False

@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    if APP_TOKEN:
        p = request.url.path
        # /api/* E /downloads/* (os arquivos baixados também são conteúdo privado:
        # antes só a API era protegida e, num servidor público, qualquer um com o
        # link levava o MP3). O frontend manda o token pelo cookie sm_token (path=/),
        # então os links de "Salvar" continuam funcionando normalmente.
        protegido = (p.startswith("/api/") and p != "/api/auth/check") or p.startswith("/downloads/")
        if protegido:
            tok = (request.query_params.get("token")
                   or request.cookies.get("sm_token")
                   or (request.headers.get("authorization") or "").removeprefix("Bearer ")
                   ).strip()
            if not _tok_ok(tok):
                return Response('{"detail":"token de acesso inválido"}',
                                status_code=401, media_type="application/json")
    return await call_next(request)

@app.get("/api/auth/check")
def auth_check(request: Request):
    if not APP_TOKEN:
        return {"required": False, "ok": True}
    tok = (request.cookies.get("sm_token")
           or (request.headers.get("authorization") or "").removeprefix("Bearer ")).strip()
    return {"required": True, "ok": _tok_ok(tok)}

# ---------- Pareamento por QR + fechadura do servidor (portado da v2.1) ----------
# O dono abre o PAINEL só no PC (localhost): vê os IPs da rede, o QR pro celular
# escanear (abre o app já conectado, com a chave ?sk=) e a FECHADURA — quando
# ligada, quem não tem a chave recebe a página de bloqueio. O painel nunca
# atravessa a rede: pedidos de fora levam 403. É o modo "amigável" de travar o
# servidor; o APP_TOKEN (acima) é o modo "paranoico" por variável de ambiente.
# Os dois podem coexistir.

def _pair_key() -> str:
    k = str(SETTINGS.get("pair_key") or "")
    if not k:
        k = secrets.token_urlsafe(9)
        SETTINGS["pair_key"] = k
        _save_settings()
    return k

def _safe_eq(a: str, b: str) -> bool:
    try:
        return secrets.compare_digest(str(a), str(b))
    except Exception:
        return False

def _candidate_ips() -> list:
    ips, seen = [], set()

    def add(ip):
        if ip and ip not in seen and not ip.startswith("127.") \
                and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", ip):
            seen.add(ip)
            ips.append(ip)

    try:  # IP da rota padrão (o "principal") — UDP connect não envia pacote
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:  # demais interfaces (Tailscale 100.x, segunda placa etc.)
        if os.name == "nt":
            out = subprocess.run(["ipconfig"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=5).stdout
        else:
            try:
                out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=5).stdout
            except Exception:
                out = subprocess.run(["ifconfig"], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=5).stdout
        for m in re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", out):
            add(m)
    except Exception:
        pass
    ips.sort(key=lambda ip: 1 if ip.startswith("100.") else 0)  # Tailscale por último
    return ips

# ---------- Cloudflare Quick Tunnel (acesso seguro HTTPS na rua sem abrir portas) ----------
_CF_TUNNEL_PROC: subprocess.Popen | None = None
_CF_TUNNEL_URL: str | None = None
_CF_TUNNEL_STATUS: str = "desligado"
_CF_TUNNEL_LOCK = threading.Lock()

def _cloudflared_binary_name() -> str:
    return "cloudflared.exe" if os.name == "nt" else "cloudflared"

def _cloudflared_download_url() -> str | None:
    import platform
    sys_name = platform.system().lower()
    mach = platform.machine().lower()
    if sys_name == "windows":
        if "64" in mach or "amd64" in mach or "x86_64" in mach:
            return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-386.exe"
    elif sys_name == "linux":
        if "aarch64" in mach or "arm64" in mach:
            return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
        elif "arm" in mach:
            return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm"
        return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    elif sys_name == "darwin":
        if "arm64" in mach:
            return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64"
        return "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64"
    return None

def _find_cloudflared() -> Path | None:
    w = shutil.which("cloudflared")
    if w:
        return Path(w)
    bin_name = _cloudflared_binary_name()
    for cand in (DATA / "bin" / bin_name, BASE / "bin" / bin_name, BASE / bin_name):
        if cand.is_file():
            return cand
    return None

def _ensure_cloudflared() -> Path | None:
    found = _find_cloudflared()
    if found:
        return found
    url = _cloudflared_download_url()
    if not url:
        return None
    dest_dir = DATA / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _cloudflared_binary_name()
    part = dest_dir / f".{uuid.uuid4().hex[:6]}.cftmp"
    print(f"  ☁️  Baixando Cloudflare Tunnel ({_cloudflared_binary_name()})...")
    try:
        r = requests.get(url, stream=True, timeout=30)
        if not r.ok:
            return None
        with open(part, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        part.replace(dest)
        if os.name != "nt":
            dest.chmod(0o755)
        print("  ☁️  Cloudflare Tunnel baixado com sucesso! ✓")
        return dest
    except Exception as e:
        part.unlink(missing_ok=True)
        print(f"  ⚠️ Falha ao baixar Cloudflare Tunnel: {e}")
        return None

def _start_cloudflared_tunnel(port: int = 8787):
    global _CF_TUNNEL_PROC, _CF_TUNNEL_URL, _CF_TUNNEL_STATUS
    with _CF_TUNNEL_LOCK:
        if _CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None:
            return _CF_TUNNEL_URL
        _CF_TUNNEL_STATUS = "conectando"

    def _worker():
        global _CF_TUNNEL_PROC, _CF_TUNNEL_URL, _CF_TUNNEL_STATUS
        bin_path = _ensure_cloudflared()
        if not bin_path or not bin_path.is_file():
            with _CF_TUNNEL_LOCK:
                _CF_TUNNEL_STATUS = "erro: binário não disponível"
            return

        cmd = [str(bin_path), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags
            )
            with _CF_TUNNEL_LOCK:
                _CF_TUNNEL_PROC = proc

            url_announced = False
            for line in iter(proc.stdout.readline, ""):
                if url_announced:
                    continue
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if m:
                    url_announced = True
                    url = m.group(0)
                    with _CF_TUNNEL_LOCK:
                        _CF_TUNNEL_URL = url
                        _CF_TUNNEL_STATUS = "ativo"
                    k = _pair_key()
                    full_link = f"{url}/?sk={k}"
                    print(f"\n  ☁️  TÚNEL CLOUDFLARE ATIVO!")
                    print(f"     Acesso mundial (4G/5G com HTTPS): {full_link}")
                    print("     📷 Escaneie no celular para ouvir na rua ou instalar como PWA:\n")
                    try:
                        import qrcode
                        qr = qrcode.QRCode(border=1)
                        qr.add_data(full_link)
                        qr.print_ascii(invert=True)
                    except Exception:
                        pass

            proc.wait()
        except Exception as e:
            print(f"  ⚠️ Erro no processo do túnel Cloudflare: {e}")
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            with _CF_TUNNEL_LOCK:
                _CF_TUNNEL_STATUS = "desligado"
                _CF_TUNNEL_URL = None

    threading.Thread(target=_worker, daemon=True).start()

def _stop_cloudflared_tunnel():
    global _CF_TUNNEL_PROC, _CF_TUNNEL_URL, _CF_TUNNEL_STATUS
    with _CF_TUNNEL_LOCK:
        if _CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None:
            try:
                _CF_TUNNEL_PROC.terminate()
            except Exception:
                pass
        _CF_TUNNEL_PROC = None
        _CF_TUNNEL_URL = None
        _CF_TUNNEL_STATUS = "desligado"

import atexit
atexit.register(_stop_cloudflared_tunnel)


def _qr_svg(data: str):
    try:  # SVG puro: não precisa de Pillow
        import io
        import qrcode
        import qrcode.image.svg
        buf = io.BytesIO()
        qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage,
                    box_size=12, border=2).save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return None

from pwa_assets import _LOCKED_HTML, _PAINEL_CSS, _EMBEDDED_ASSETS

_last_seen_mem: dict[int, float] = {}
_last_seen_lock = threading.Lock()
_LAST_SEEN_TTL = 60.0

def _touch_last_seen(con, uid: int):
    now = time.time()
    with _last_seen_lock:
        if now - _last_seen_mem.get(uid, 0.0) < _LAST_SEEN_TTL:
            return
        _last_seen_mem[uid] = now
    try:
        con.execute("UPDATE users SET last_seen=CURRENT_TIMESTAMP WHERE id=?", (uid,))
    except Exception:
        pass

def _get_user_by_token(token: str) -> dict | None:
    """Busca o perfil do usuário pelo seu token exclusivo de login/QR."""
    if not token:
        return None
    token = str(token).strip()
    with db() as con:
        r = con.execute("SELECT id, name, token, is_admin FROM users WHERE token=?", (token,)).fetchone()
        if r:
            _touch_last_seen(con, int(r["id"]))
            return dict(r)
        if _safe_eq(token, _pair_key()):
            r1 = con.execute("SELECT id, name, token, is_admin FROM users WHERE id=1").fetchone()
            if r1:
                return dict(r1)
    return None

def _current_user(request: Request) -> dict:
    """Retorna o perfil do usuário atual autenticado na requisição (ou Admin apenas no localhost)."""
    u = getattr(request.state, "user", None)
    if isinstance(u, dict) and u.get("id") is not None:
        return u
    token = request.query_params.get("sk") or request.cookies.get("sk")
    if token:
        found = _get_user_by_token(token)
        if found:
            request.state.user = found
            return found
    is_local = getattr(request.state, "is_local", None)
    if is_local is None:
        is_local = _is_local_client(_resolve_client_ip(request))
    if not is_local:
        guest = {"id": 0, "name": "Convidado", "token": "", "is_admin": 0}
        request.state.user = guest
        return guest
    with db() as con:
        r = con.execute("SELECT id, name, token, is_admin FROM users ORDER BY id ASC LIMIT 1").fetchone()
        if r:
            found = dict(r)
            request.state.user = found
            return found
    return {"id": 1, "name": "Principal (Admin)", "token": _pair_key(), "is_admin": 1}

def _require_admin(request: Request) -> dict:
    u = _current_user(request)
    if not u.get("is_admin"):
        raise HTTPException(403, "Apenas o administrador pode realizar esta ação")
    return u

def _require_playlist_owner(con, pid: int, u: dict, allow_admin: bool = False):
    pl = con.execute("SELECT * FROM local_playlists WHERE id=?", (pid,)).fetchone()
    if not pl:
        raise HTTPException(404, "Playlist não encontrada")
    if pl["user_id"] != u["id"] and not (allow_admin and u.get("is_admin")):
        raise HTTPException(403, "Acesso negado: esta playlist pertence a outro usuário")
    return pl

def _resolve_guard_user(sk_param: str | None, sk_cookie: str | None, local: bool):
    matched_user = None
    if sk_param:
        matched_user = _get_user_by_token(sk_param)
    elif sk_cookie:
        matched_user = _get_user_by_token(sk_cookie)
    effective_user = matched_user
    if effective_user is None and local:
        with db() as con:
            r1 = con.execute("SELECT id, name, token, is_admin FROM users ORDER BY id ASC LIMIT 1").fetchone()
            if r1:
                effective_user = dict(r1)
    return matched_user, effective_user

_PUBLIC_PWA_PATHS = {
    "/manifest.webmanifest", "/sw.js", "/favicon.ico",
    "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png",
    "/static/app.css", "/static/app.js",
}

_LOCAL_HOSTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")
_TRUSTED_PROXY_HOSTS = (
    set(_LOCAL_HOSTS)
    | {"testclient"}
    | {h.strip() for h in os.environ.get("TRUSTED_PROXIES", "").split(",") if h.strip()}
)

def _is_local_client(client: str) -> bool:
    return client in _LOCAL_HOSTS or client == "testclient"

def _apply_sec_headers(resp: Response) -> Response:
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp

_rl_lock = threading.Lock()
_rl_hits: dict[tuple[str, str], list[float]] = {}
_RATE_LIMITS: dict[str, tuple[int, float]] = {
    "search": (40, 60.0),
    "ytplaylist": (20, 60.0),
    "stream": (90, 60.0),
    "download": (30, 60.0),
}

def _check_rate_limit(request: Request, bucket: str):
    max_req, window_s = _RATE_LIMITS.get(bucket, (60, 60.0))
    ip = _resolve_client_ip(request) or "unknown"
    now = time.time()
    cutoff = now - window_s
    key = (ip, bucket)
    with _rl_lock:
        if len(_rl_hits) > 2000:
            for k in list(_rl_hits):
                arr = [t for t in _rl_hits[k] if t > now - 60.0]
                if arr:
                    _rl_hits[k] = arr
                else:
                    _rl_hits.pop(k, None)
        arr = [t for t in _rl_hits.get(key, ()) if t > cutoff]
        if len(arr) >= max_req:
            _rl_hits[key] = arr
            raise HTTPException(429, "Muitas requisições em pouco tempo — aguarde alguns segundos.")
        arr.append(now)
        _rl_hits[key] = arr

def _resolve_client_ip(request: Request) -> str:
    """Resolve o IP real confiando em cabeçalhos de proxy só quando o peer é confiável."""
    peer = (request.client.host if request.client else "") or ""
    if peer not in _TRUSTED_PROXY_HOSTS:
        return peer
    cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_ip:
        return cf_ip
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        for hop in reversed(parts):
            if hop not in _TRUSTED_PROXY_HOSTS:
                return hop
        if parts:
            return parts[0]
    return peer

@app.middleware("http")
async def _pair_guard(request: Request, call_next):
    path = request.url.path
    client = _resolve_client_ip(request)
    local = _is_local_client(client)
    request.state.is_local = local
    if path == "/painel" or path.startswith("/painel/"):
        if not local:  # painel é coisa do dono: nunca atravessa a rede
            return _apply_sec_headers(Response("Painel disponível só no PC do dono (localhost).",
                            status_code=403, media_type="text/plain; charset=utf-8"))
        return _apply_sec_headers(await call_next(request))
    # Identifica o perfil do usuário por token de link (?sk=...) ou cookie fora do event loop
    sk_param = request.query_params.get("sk")
    sk_cookie = request.cookies.get("sk")
    matched_user, effective_user = await run_in_threadpool(_resolve_guard_user, sk_param, sk_cookie, local)

    if effective_user:
        request.state.user = effective_user

    # Verificação de bloqueio se a fechadura estiver ativada
    if not (path in _PUBLIC_PWA_PATHS or not SETTINGS.get("pairing_required") or local):
        if not getattr(request.state, "user", None):
            return _apply_sec_headers(HTMLResponse(_LOCKED_HTML, status_code=403))

    resp = await call_next(request)

    # Grava o cookie se o usuário veio por link com token (?sk=...)
    if sk_param and matched_user:
        try:
            peer = (request.client.host if request.client else "") or ""
            xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower() if peer in _TRUSTED_PROXY_HOSTS else ""
            is_https = (request.url.scheme == "https") or (xfp == "https")
            resp.set_cookie("sk", matched_user["token"], max_age=60 * 60 * 24 * 365,
                            samesite="lax", httponly=True, secure=is_https)
        except Exception:
            pass

    return _apply_sec_headers(resp)

@app.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    port = request.url.port or 80
    key = _pair_key()
    on = bool(SETTINGS.get("pairing_required"))
    cf_on = bool(_CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None)
    cf_connecting = cf_on and not _CF_TUNNEL_URL
    primary_ip = _candidate_ips()[0] if _candidate_ips() else "127.0.0.1"

    with db() as con:
        users = [dict(r) for r in con.execute(
            "SELECT id, name, token, created_at, last_seen, is_admin FROM users ORDER BY is_admin DESC, id ASC"
        ).fetchall()]

    user_cards = []
    for u in users:
        uid = int(u["id"])
        raw_name = str(u["name"] or "")
        uname = html.escape(raw_name, quote=True)
        utok = urllib.parse.quote(str(u["token"] or ""), safe="-_.~")
        is_adm = bool(u.get("is_admin"))
        initial = html.escape((raw_name[:1] or "U").upper(), quote=True)
        seen = u.get("last_seen")
        seen_txt = html.escape(f"Visto em {seen[:16]}" if seen else "Nunca conectou", quote=True)
        badge = "<span class='u-badge adm'>Admin</span>" if is_adm else "<span class='u-badge usr'>Convidado</span>"

        cf_link = f"{_CF_TUNNEL_URL}/?sk={utok}" if _CF_TUNNEL_URL else None
        local_link = f"http://{primary_ip}:{port}/?sk={utok}"
        qr_target = cf_link or local_link
        qr_svg = _qr_svg(qr_target) or "<div class='hint'>QR indisponível</div>"

        links_html = ""
        if cf_link:
            cf_attr = html.escape(cf_link, quote=True)
            links_html += (
                f'<div class="u-link" title="Clique para copiar link da rua" data-url="{cf_attr}" '
                f'onclick="navigator.clipboard.writeText(this.dataset.url);this.querySelector(\'.u-link-url\').textContent=\'Link copiado ✓\';">'
                f'<span class="u-link-type">☁️ Link na Rua (4G/5G)</span>'
                f'<div class="u-link-url">{cf_attr}</div></div>'
            )
        local_attr = html.escape(local_link, quote=True)
        links_html += (
            f'<div class="u-link" title="Clique para copiar link local" data-url="{local_attr}" '
            f'onclick="navigator.clipboard.writeText(this.dataset.url);this.querySelector(\'.u-link-url\').textContent=\'Link copiado ✓\';">'
            f'<span class="u-link-type">📶 Link no Wi-Fi</span>'
            f'<div class="u-link-url">{local_attr}</div></div>'
        )

        del_btn = ""
        if not is_adm:
            confirm_attr = html.escape(f"Excluir o usuário {raw_name} e todos os seus favoritos/histórico?", quote=True)
            del_btn = (
                f'<form method="POST" action="/painel/users/{uid}/delete" style="display:inline" '
                f'data-confirm="{confirm_attr}" onsubmit="return confirm(this.dataset.confirm);">'
                f'<button type="submit" class="ghost btn-sm btn-del">🗑️ Excluir</button></form>'
            )

        qr_attr = html.escape(qr_target, quote=True)
        admin_cls = 'admin' if is_adm else ''
        u_card = (
            f"<div class='u-card {admin_cls}'>"
            f"<div class='u-top'>"
            f"<div class='u-avatar'>{initial}</div>"
            f"<div class='u-meta'><div class='u-name' title='{uname}'>{uname} {badge}</div><div class='u-seen'>{seen_txt}</div></div>"
            f"</div>"
            f"<div class='u-links'>{links_html}</div>"
            f"<div class='qr'>{qr_svg}</div>"
            f"<div class='hint'>📷 Aponte a câmera do celular de <b>{uname}</b></div>"
            f"<div class='u-actions'>"
            f"<button class='ghost btn-sm' data-url=\"{qr_attr}\" onclick=\"navigator.clipboard.writeText(this.dataset.url);this.textContent='Copiado ✓';\">📋 Copiar Link</button>"
            f"<form method='POST' action='/painel/users/{uid}/rekey' style='display:inline'>"
            f"<button type='submit' class='ghost btn-sm' title='Regera a chave de acesso deste usuário e desloga aparelhos antigos'>🔄 Renovar QR</button></form>"
            f"{del_btn}"
            f"</div>"
            f"</div>"
        )
        user_cards.append(u_card)

    btn = (
        "<button class='ghost' onclick=\"fetch('/painel/toggle',{method:'POST'}).then(r=>r.json()).then(()=>location.reload())\">"
        + ("Destrancar servidor" if on else "Trancar (só pareados)")
        + "</button>"
    )
    badge_lock = (
        "<span class='badge on'>🔒 Trancado · só pareados</span>" if on
        else "<span class='badge off'>🔓 Aberto na rede local</span>"
    )
    cf_cls = "ghost" if cf_on else ""
    cf_btn = (
        f"<button class='{cf_cls}' onclick=\"this.disabled=true;this.textContent='Aguarde…';fetch('/painel/tunnel/toggle',{{method:'POST'}}).then(r=>r.json()).then(()=>location.reload())\">"
        + ("Desligar Túnel" if cf_on else "☁️ Ligar Túnel Cloudflare (4G/5G)")
        + "</button>"
    )
    cf_badge = ("<span class='badge cf-on'>☁️ Túnel Ativo</span>" if _CF_TUNNEL_URL
                else ("<span class='badge cf-on'><span class='spin' style='width:10px;height:10px;border-width:1.5px;margin-right:5px'></span>Conectando…</span>" if cf_on
                      else "<span class='badge cf-off'>Desligado</span>"))

    poll_script = (
        "<script>setInterval(()=>{fetch('/api/tunnel/status').then(r=>r.json()).then(d=>{if(d.url||!d.active)location.reload()}).catch(()=>{})},1200);</script>"
        if cf_connecting else ""
    )

    return HTMLResponse(
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>StremMusic · Painel do Host</title><style>" + _PAINEL_CSS + "</style></head><body>"
        "<div class='wrap'>"
        "<div class='top'>"
        "<div class='brand'><div class='logo'>▶</div><div><h1>Strem<span>Music</span> · Host</h1>"
        "<p class='sub'>Painel de gerenciamento de usuários, conexões e QR Codes</p></div></div>"
        f"<a class='btn-open' href='http://127.0.0.1:{port}/'>↗ Abrir Player no PC</a>"
        "</div>"
        "<div class='controls-grid'>"
        "<div class='ctrl-card'>"
        "<div><div class='ctrl-head'><span class='ctrl-title'>Acesso na Rua (Cloudflare 4G/5G)</span>" + cf_badge + "</div>"
        "<p class='ctrl-desc'>Gera link HTTPS seguro (trycloudflare.com). Ao ligar, os QR Codes dos usuários atualizam automaticamente para a URL da rua.</p></div>"
        "<div class='ctrl-foot'>" + cf_btn + "</div>"
        "</div>"
        "<div class='ctrl-card'>"
        "<div><div class='ctrl-head'><span class='ctrl-title'>Fechadura de Segurança</span>" + badge_lock + "</div>"
        "<p class='ctrl-desc'>Quando trancado, apenas dispositivos que escanearam o QR Code de um dos usuários cadastrados conseguem acessar.</p></div>"
        "<div class='ctrl-foot'>" + btn + "</div></div>"
        "</div>"
        "<div class='users-box'>"
        "<div class='users-head'>"
        "<div><div class='ctrl-title'>👥 Usuários & Perfis (Logins com QR Code Exclusivo)</div>"
        "<p class='ctrl-desc' style='margin-top:2px'>Cada usuário tem seu próprio login. Os favoritos, histórico e playlists são separados para cada pessoa.</p></div>"
        "<form method='POST' action='/painel/users' class='user-add-form'>"
        "<input name='name' placeholder='Nome do amigo / cômodo…' maxlength='35' required autocomplete='off'>"
        "<button type='submit'>＋ Criar Novo Login &amp; QR Code</button>"
        "</form>"
        "</div>"
        "<div class='users-grid'>" + "".join(user_cards) + "</div>"
        "</div>"
        "<div class='foot'>"
        f"<span>Player no PC: <a href='http://127.0.0.1:{port}/'>http://127.0.0.1:{port}/</a></span>"
        "<span>Para instalar o PWA: abra a câmera no celular, escaneie o QR Code e toque em <b>Adicionar à tela inicial</b></span>"
        "</div>"
        "</div>" + poll_script + "</body></html>")

def _check_csrf(request: Request):
    sec_site = request.headers.get("sec-fetch-site")
    if sec_site in ("cross-site",):
        raise HTTPException(403, "Acesso cross-site negado")
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host") or ""
    if origin:
        parsed = urllib.parse.urlparse(origin)
        if not parsed.netloc or parsed.netloc != host:
            raise HTTPException(403, "Origem inválida")
    elif referer:
        parsed = urllib.parse.urlparse(referer)
        if not parsed.netloc or parsed.netloc != host:
            raise HTTPException(403, "Referer inválido")

@app.post("/painel/toggle")
def painel_toggle(request: Request):
    _check_csrf(request)
    SETTINGS["pairing_required"] = not bool(SETTINGS.get("pairing_required"))
    _save_settings()
    return {"pairing_required": SETTINGS["pairing_required"]}

@app.post("/painel/rekey")
def painel_rekey(request: Request):
    _check_csrf(request)
    new_key = secrets.token_urlsafe(12)
    SETTINGS["pair_key"] = new_key
    _save_settings()
    with db() as con:
        con.execute("UPDATE users SET token=? WHERE id=1", (new_key,))
    return {"pair_key": new_key}

@app.post("/painel/users")
async def painel_create_user(request: Request):
    _check_csrf(request)
    form = await request.form()
    name = str(form.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "O nome do usuário não pode ser vazio")
    tok = secrets.token_urlsafe(12)
    with db() as con:
        con.execute("INSERT INTO users (name, token, is_admin) VALUES (?, ?, 0)", (name, tok))
    return RedirectResponse("/painel", status_code=303)

@app.post("/painel/users/{uid}/rekey")
def painel_rekey_user(uid: int, request: Request):
    _check_csrf(request)
    new_tok = secrets.token_urlsafe(12)
    with db() as con:
        con.execute("UPDATE users SET token=? WHERE id=?", (new_tok, uid))
        if uid == 1:
            SETTINGS["pair_key"] = new_tok
            _save_settings()
    return RedirectResponse("/painel", status_code=303)

@app.post("/painel/users/{uid}/delete")
def painel_delete_user(uid: int, request: Request):
    _check_csrf(request)
    if uid == 1:
        raise HTTPException(400, "Não é permitido excluir o usuário principal/admin")
    with db() as con:
        con.execute("DELETE FROM favorites WHERE user_id=?", (uid,))
        con.execute("DELETE FROM history WHERE user_id=?", (uid,))
        con.execute("DELETE FROM next_map WHERE user_id=?", (uid,))
        con.execute("DELETE FROM stations WHERE user_id=?", (uid,))
        con.execute("DELETE FROM playlist_items WHERE playlist_id IN (SELECT id FROM local_playlists WHERE user_id=?)", (uid,))
        con.execute("DELETE FROM local_playlists WHERE user_id=?", (uid,))
        con.execute("DELETE FROM users WHERE id=?", (uid,))
    return RedirectResponse("/painel", status_code=303)

@app.post("/painel/tunnel/toggle")
def painel_tunnel_toggle(request: Request):
    _check_csrf(request)
    cf_on = bool(_CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None)
    if cf_on:
        _stop_cloudflared_tunnel()
        SETTINGS["cloudflare_tunnel"] = False
    else:
        port = request.url.port or int(os.environ.get("PORT", "8787"))
        _start_cloudflared_tunnel(port)
        SETTINGS["cloudflare_tunnel"] = True
    _save_settings()
    return {"ok": True, "active": not cf_on}

@app.get("/api/tunnel/status")
def tunnel_status(request: Request):
    _require_admin(request)
    cf_on = bool(_CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None)
    return {"status": _CF_TUNNEL_STATUS, "active": cf_on, "url": _CF_TUNNEL_URL}

# ---------- Banco ----------
@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def init_db():
    with db() as con:
        try:
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT,
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS favorites (user_id INTEGER NOT NULL DEFAULT 1, video_id TEXT NOT NULL, title TEXT, artist TEXT, thumbnail TEXT, added_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, video_id));
        CREATE TABLE IF NOT EXISTS history (user_id INTEGER NOT NULL DEFAULT 1, video_id TEXT NOT NULL, title TEXT, artist TEXT, thumbnail TEXT, played_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, video_id));
        CREATE TABLE IF NOT EXISTS local_playlists (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1, name TEXT NOT NULL, UNIQUE(user_id, name));
        CREATE TABLE IF NOT EXISTS playlist_items (playlist_id INTEGER, video_id TEXT, title TEXT, artist TEXT, thumbnail TEXT, position INTEGER, PRIMARY KEY (playlist_id, video_id));
        CREATE TABLE IF NOT EXISTS stations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL DEFAULT 1, name TEXT, seed_id TEXT, seed_title TEXT, seed_artist TEXT, seed_thumb TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS plays (video_id TEXT PRIMARY KEY, n INTEGER DEFAULT 0, last_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS next_map (
            user_id INTEGER NOT NULL DEFAULT 1,
            prev_id TEXT NOT NULL,
            next_id TEXT NOT NULL,
            title TEXT,
            artist TEXT,
            thumbnail TEXT,
            n INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, prev_id, next_id)
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY, file TEXT UNIQUE, title TEXT, artist TEXT,
            album TEXT, dur REAL, art TEXT, added TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS cache_entries (
            video_id TEXT PRIMARY KEY,
            content_type TEXT DEFAULT 'audio/mp4',
            size INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL DEFAULT 0,
            at REAL NOT NULL DEFAULT 0,
            title TEXT DEFAULT '',
            artist TEXT DEFAULT '',
            client TEXT DEFAULT ''
        );
        """)
        # Garante usuário 1 (Principal / Admin)
        u1 = con.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
        if not u1:
            tok = ""
            try:
                sf = DATA / "settings.json"
                if sf.exists():
                    tok = json.loads(sf.read_text(encoding="utf-8")).get("pair_key") or ""
            except Exception:
                pass
            if not tok:
                tok = secrets.token_urlsafe(9)
            con.execute("INSERT INTO users (id, name, token, is_admin) VALUES (1, 'Principal (Admin)', ?, 1)",
                        (tok,))

        # Migração suave de bases antigas para multi-usuário
        fav_cols = [r[1] for r in con.execute("PRAGMA table_info(favorites)").fetchall()]
        if "user_id" not in fav_cols:
            con.execute("""CREATE TABLE IF NOT EXISTS favorites_v2 (
                user_id INTEGER NOT NULL DEFAULT 1,
                video_id TEXT NOT NULL,
                title TEXT, artist TEXT, thumbnail TEXT,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, video_id)
            )""")
            con.execute("""INSERT OR IGNORE INTO favorites_v2 (user_id, video_id, title, artist, thumbnail, added_at)
                           SELECT 1, video_id, title, artist, thumbnail, added_at FROM favorites""")
            con.execute("DROP TABLE favorites")
            con.execute("ALTER TABLE favorites_v2 RENAME TO favorites")

        hist_cols = [r[1] for r in con.execute("PRAGMA table_info(history)").fetchall()]
        if "user_id" not in hist_cols:
            con.execute("""CREATE TABLE IF NOT EXISTS history_v2 (
                user_id INTEGER NOT NULL DEFAULT 1,
                video_id TEXT NOT NULL,
                title TEXT, artist TEXT, thumbnail TEXT,
                played_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, video_id)
            )""")
            con.execute("""INSERT OR IGNORE INTO history_v2 (user_id, video_id, title, artist, thumbnail, played_at)
                           SELECT 1, video_id, title, artist, thumbnail, played_at FROM history""")
            con.execute("DROP TABLE history")
            con.execute("ALTER TABLE history_v2 RENAME TO history")

        pl_cols = [r[1] for r in con.execute("PRAGMA table_info(local_playlists)").fetchall()]
        if "user_id" not in pl_cols:
            con.execute("""CREATE TABLE IF NOT EXISTS local_playlists_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                name TEXT NOT NULL,
                UNIQUE (user_id, name)
            )""")
            con.execute("""INSERT OR IGNORE INTO local_playlists_v2 (id, user_id, name)
                           SELECT id, 1, name FROM local_playlists""")
            con.execute("DROP TABLE local_playlists")
            con.execute("ALTER TABLE local_playlists_v2 RENAME TO local_playlists")

        nm_cols = [r[1] for r in con.execute("PRAGMA table_info(next_map)").fetchall()]
        if "user_id" not in nm_cols:
            con.execute("""CREATE TABLE IF NOT EXISTS next_map_v2 (
                user_id INTEGER NOT NULL DEFAULT 1,
                prev_id TEXT NOT NULL,
                next_id TEXT NOT NULL,
                title TEXT,
                artist TEXT,
                thumbnail TEXT,
                n INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, prev_id, next_id)
            )""")
            con.execute("""INSERT OR IGNORE INTO next_map_v2 (user_id, prev_id, next_id, n)
                           SELECT 1, prev_id, next_id, n FROM next_map""")
            con.execute("DROP TABLE next_map")
            con.execute("ALTER TABLE next_map_v2 RENAME TO next_map")
        # Migração suave de estações para multi-usuário
        st_cols = [r[1] for r in con.execute("PRAGMA table_info(stations)").fetchall()]
        if "user_id" not in st_cols:
            con.execute("ALTER TABLE stations ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        # migração suave: ano e gênero das faixas importadas (colunas novas)
        for col, typ in (("year", "INTEGER DEFAULT 0"), ("genre", "TEXT DEFAULT ''")):
            try:
                con.execute(f"ALTER TABLE uploads ADD COLUMN {col} {typ}")
            except Exception:
                pass   # coluna já existe
        con.executescript("""
        CREATE INDEX IF NOT EXISTS idx_cache_entries_at ON cache_entries(at DESC);
        CREATE INDEX IF NOT EXISTS idx_favorites_user_added ON favorites(user_id, added_at DESC);
        CREATE INDEX IF NOT EXISTS idx_history_user_played ON history(user_id, played_at DESC);
        CREATE INDEX IF NOT EXISTS idx_local_playlists_user ON local_playlists(user_id);
        CREATE INDEX IF NOT EXISTS idx_playlist_items_pl_pos ON playlist_items(playlist_id, position);
        CREATE INDEX IF NOT EXISTS idx_stations_user ON stations(user_id);
        CREATE INDEX IF NOT EXISTS idx_next_map_user_prev_n ON next_map(user_id, prev_id, n DESC);
        CREATE INDEX IF NOT EXISTS idx_plays_n_last ON plays(n DESC, last_at DESC);
        """)
init_db()

# ---------- YouTube Music (API pública anônima: busca e letras não precisam de login) ----------
def _new_yt() -> YTMusic:
    """YTMusic com a sessão interna sob nosso controle: IPv4 forçado (anti-stall
    de IPv6 quebrado) e timeout em TODAS as chamadas (a lib não põe nenhum)."""
    inst = YTMusic()
    try:
        inst._session = _mount_ipv4(_TimeoutSession())
    except Exception:
        pass
    return inst

yt = _new_yt()

# ---------- Extração de URL de áudio ----------
class _QuietLogger:
    """Silencia o stdout/stderr do yt-dlp (os 'ERROR: [youtube] ...' que poluíam o console
    vinham dos clients perdedores da disputa paralela; os erros reais chegam via exceção)."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

URL_TTL = 4 * 3600
# Ordem de tentativa, em ondas de RACE_WAVE clients em paralelo (guia de PO Token do yt-dlp, out/2026):
#   android_vr  → NÃO exige PO Token; quando está de pé vence em 1–3s — porém não
#                 serve vídeos "Made for kids" e o YouTube vem restringindo
#   mweb        → funciona bem COM o provedor de PO Token (bgutil :4416) — foi o
#                 vencedor mais consistente nos logs reais (7–13s; mais lento, porém
#                 quase nunca morre — cobre o android_vr quando ele tomba)
#   None        → padrão do yt-dlp instalado (muda conforme a versão)
#   web_safari  → formatos HLS; SEM PO Token válido devolve só DRM
#                 ("Requested format is not available")
#   tv          → sem cookies falha sempre ("The page needs to be reloaded"); só
#                 fica de pé com cookies.txt (USE_COOKIES=1 / switch no painel)
# (o client "ios" foi testado e removido: falhou sempre com "Requested format is not available")
PLAYER_CLIENTS = [None, ["mweb"], ["android_vr"]]
# android_vr em 1º, mweb em 2º (2ª análise independente — mistral/out-2026 — bateu
# com a nossa): com o race_wave padrão (2) os dois CAEM NA MESMA 1ª ONDA e correm
# em paralelo — android_vr de pé ganha em 1–3s (sem PO Token); vídeo "Made for
# kids" derruba SÓ ele e o mweb cobre na mesma onda; android_vr morto sai de
# circulação pela saúde de clients (_client_health) e a sondagem periódica o
# reavalia sem custo de parede. Com race_wave=1 a ordem estática importa:
# android_vr primeiro = partida rápida, e o _prefer_last_ok aprende o vencedor
# real depois do 1º sucesso de qualquer jeito.
# web_safari/tv saíram da lista FIXA e viraram CONDICIONAIS a cookies
# (_clients_full abaixo — apontamento do gemini 3.8 com o SEU log real, out/2026):
# sem cookies os dois têm win-rate ~0 — web_safari "Requested format is not
# available" (DRM sem PO Token, 7 falhas seguidas) e tv "The page needs to be
# reloaded" (4 seguidas) — e cada tentativa doente custava 5–12s nas correntes
# onde eram tentados: URLs de 17–23s vs 7–9s quando a onda acerta direto.
# Com cookies.txt ativo eles VOLTAM (o resgate clássico da 2026-10-24.2 fica
# de pé; a saúde de clients continua valendo pra todo mundo).
_COOKIE_CLIENTS = [["web_safari"], ["tv"]]
if os.environ.get("YT_CLIENTS"):
    PLAYER_CLIENTS = [[c.strip() for c in os.environ["YT_CLIENTS"].split(",") if c.strip()]]

def _clients_full():
    """Lista EFETIVA de clients desta rodada. Base = PLAYER_CLIENTS; web_safari/tv
    só entram com cookies de verdade (switch do painel + cookies.txt na pasta).
    Recalculado a cada extração: ligar/desligar cookies no painel muda a disputa
    NA HORA, sem reiniciar o servidor. YT_CLIENTS explícito manda mais que tudo
    (re-lido a cada chamada — exportou outro valor, a próxima faixa já obedece)."""
    if os.environ.get("YT_CLIENTS"):
        return [[c.strip() for c in os.environ["YT_CLIENTS"].split(",") if c.strip()]]
    if SETTINGS.get("use_cookies") and COOKIES_FILE.exists():
        return list(PLAYER_CLIENTS) + [list(c) for c in _COOKIE_CLIENTS]
    return list(PLAYER_CLIENTS)
RACE_WAVE = 2            # era 4 em paralelo → 4x mais requisições = rate limit do YouTube
EXTRACT_TIMEOUT = 25     # era 15: com só o mweb de pé (out/2026), os sucessos reais no
                         # PC do usuário chegam a 8–13s (PO Token + SigSolve) — teto de
                         # 15s cortava o mweb no meio e derrubava a onda INTEIRA junto
                         # (a causa dos timeouts em cascata). 25s dá folga sem estourar
                         # o teto total; clients mortos já não participam (saúde).
TOTAL_EXTRACT_TIMEOUT = 48  # teto da soma das ondas (com panes, mweb ainda alcança)
FAIL_COOLDOWN = 12.0     # evita martelar o YouTube quando ele acabou de bloquear
MAX_CACHE = 400

_stream_cache: dict[str, tuple[float, dict]] = {}
_fail_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()
# Dois pools separados de propósito:
#   _extract_pool → só _extract_one, que nunca espera outra tarefa (sempre progride)
#   _orch_pool    → _extract, que espera as ondas. Se ficassem no mesmo pool, os
#                   "gerentes" ocupariam todos os workers e travariam esperando
#                   workers que nunca ficariam livres (deadlock até o timeout).
_extract_pool = cf.ThreadPoolExecutor(max_workers=12)
_orch_pool = cf.ThreadPoolExecutor(max_workers=8)
# Clients cuja URL já devolveu 403 no stream (por vídeo): a renovação passa a
# EVITAR esses clients em vez de repetir o mesmo e esbarrar no mesmo 403.
_bad_clients: dict[str, tuple[float, set]] = {}
BAD_CLIENT_TTL = 300.0                    # 5 min de "memória" por vídeo (o 403 do googlevideo costuma ser transitório)
_inflight: dict[tuple, cf.Future] = {}    # extrações em andamento (single-flight)
_inflight_lock = threading.Lock()

# ---------- Saúde dos clients (memória global de quem está doente) ----------
# O YouTube (out/2026) deixou android_vr/web_safari/tv intermitentes: tentar
# client morto custa de 1 a 8s por vídeo (e cheira a erro no log). Depois de
# CLIENT_FAIL_STREAK falhas seguidas o client tira folga de CLIENT_SKIP_TTL;
# mesmo de folga, é sondado 1x a cada CLIENT_PROBE_EVERY pra reavaliar — o
# YouTube melhora/piora sem avisar, então ninguém é banido pra sempre.
_client_health: dict[str, dict] = {}   # label → {"fails": int, "until": float, "last_try": float}
_health_lock = threading.Lock()
CLIENT_FAIL_STREAK = 2
CLIENT_SKIP_TTL = 600.0      # 10 min de folga
CLIENT_PROBE_EVERY = 90.0    # sondagem periódica mesmo de folga

def _client_healthy(clients_list) -> tuple[list, list]:
    """Separa (aptos, de folga). Client de folga ainda é liberado 1x a cada
    CLIENT_PROBE_EVERY s — a sondagem reavalia sem nunca banir pra sempre."""
    now = time.time()
    ok, skipped = [], []
    with _health_lock:
        for c in clients_list:
            lbl = _label(c)
            h = _client_health.get(lbl)
            if h and h["fails"] >= CLIENT_FAIL_STREAK and now < h["until"]:
                if now - h.get("last_try", 0.0) >= CLIENT_PROBE_EVERY:
                    h["last_try"] = now      # libera esta rodada como sondagem
                    ok.append(c)
                else:
                    skipped.append(lbl)
            else:
                ok.append(c)
    return ok, skipped

def _client_mark_fail(lbl: str):
    if not lbl:
        return
    with _health_lock:
        h = _client_health.setdefault(lbl, {"fails": 0, "until": 0.0, "last_try": 0.0})
        h["fails"] += 1
        h["last_try"] = time.time()
        if h["fails"] >= CLIENT_FAIL_STREAK:
            h["until"] = time.time() + CLIENT_SKIP_TTL
            print(f"🩶 client {lbl} tira folga de {CLIENT_SKIP_TTL/60:.0f} min "
                  f"({h['fails']} falhas seguidas) — volta a ser sondado a cada {CLIENT_PROBE_EVERY:.0f}s")

def _client_mark_ok(lbl: str):
    if not lbl:
        return
    with _health_lock:
        _client_health[lbl] = {"fails": 0, "until": 0.0, "last_try": time.time()}

# ---------- "Pre-tests": medir os clients ANTES de o usuário clicar ----------
# Medição real (out/2026, esta máquina, mesmo vídeo, 2 rodadas cada):
#   enxuto (sem EJS) .................. 4,07s
#   + remote_components= ejs:github .... 7,07s   ← ~3s por faixa, e só rende
#                                                  1 formato a mais
# ou seja: o solucionador JS remoto é caro e quase sempre desnecessário. Aqui
# ele vira PLANO B (degral na falha), não pedágio em toda extração. As medições
# abaixo decidem a ordem dos clients e se o EJS é mesmo preciso — guardadas em
# settings.json entre execuções.
_CLIENT_MS: dict[str, int] = {}      # label → latência medida (ms) do caminho enxuto
_EJS: dict = {"needed": None,       # None = não medido · True = obrigatório · False = dispensável
              "at": 0.0,             # quando foi medido
              "probe_video": "dQw4w9WgXcQ",
              "last_ms": None,
              "streak_fail": 0,     # falhas seguidas no caminho enxuto
              "streak_ok": 0}       # sucessos seguidos no caminho enxuto
_probe_lock = threading.Lock()
# Um vídeo problemático NÃO pode congelar o app no caminho lento: só liga o EJS
# depois de EJS_FALHAS falhas seguidas, e volta a tentar o enxuto após EJS_TTL
# (o YouTube muda de comportamento sem avisar).
EJS_FALHAS_PARA_LIGAR = 2
EJS_ACERTOS_PARA_DESLIGAR = 3
EJS_TTL = 6 * 3600


def _mark_client_ms(lbl: str, seconds: float):
    """Média móvel (2/3 do histórico + 1/3 da medição nova) — suaviza o ruído
    de rede sem esconder uma piora de verdade."""
    if not lbl or seconds <= 0:
        return
    ms = int(seconds * 1000)
    with _health_lock:
        old = _CLIENT_MS.get(lbl)
        _CLIENT_MS[lbl] = ms if old is None else int(old * 0.66 + ms * 0.34)


def _set_ejs(needed: bool, motivo: str = ""):
    with _health_lock:
        prev = _EJS.get("needed")
        _EJS["needed"] = bool(needed)
        _EJS["at"] = time.time()
        _EJS["streak_fail"] = 0
        _EJS["streak_ok"] = 0
    if prev != needed:
        print(f"🧪 solucionador JS (EJS): {'LIGADO — ' + motivo if needed else 'dispensado — extração enxuta resolveu'}")
        try:
            SETTINGS["ejs_needed"] = bool(needed)
            _save_settings()
        except Exception:
            pass


def _ordered_clients(clients_list: list) -> list:
    """Ordem por latência MEDIDA (quem não foi medido vai depois, na ordem
    original). Com race_wave=1 isso é a diferença entre 4s e 9s na 1ª faixa."""
    with _health_lock:
        if not _CLIENT_MS:
            return clients_list
        ms_snap = dict(_CLIENT_MS)
    def chave(item):
        i, c = item
        ms = ms_snap.get(_label(c))
        return (0 if ms is not None else 1, ms or 10 ** 9, i)
    return [c for _, c in sorted(enumerate(clients_list), key=chave)]


def _extract_one_adaptive(video_id: str, clients) -> dict:
    """Caminho enxuto primeiro (~4s mais rápido nas medições); o solucionador JS
    remoto só entra se ele falhar — e só fica ligado depois de falhar seguido."""
    with _health_lock:
        need = _EJS.get("needed")
        ejs_at = float(_EJS.get("at") or 0)
    if need and (time.time() - ejs_at < EJS_TTL):
        return _extract_one(video_id, clients, ejs=True)
    t0 = time.time()
    try:
        data = _extract_one(video_id, clients, ejs=False)
        _mark_client_ms(data.get("client"), time.time() - t0)
        with _health_lock:
            _EJS["streak_ok"] = int(_EJS.get("streak_ok") or 0) + 1
            _EJS["streak_fail"] = 0
            turn_off = bool(need and _EJS["streak_ok"] >= EJS_ACERTOS_PARA_DESLIGAR)
        if turn_off:
            _set_ejs(False, "")
        return data
    except Exception as first:
        msg_low = str(first).lower()
        if any(tok in msg_low for tok in ("requested format is not available", "sign in", "unavailable", "private video", "cancelado")):
            raise first
        with _health_lock:
            _EJS["streak_fail"] = int(_EJS.get("streak_fail") or 0) + 1
            _EJS["streak_ok"] = 0
            turn_on_count = _EJS["streak_fail"]
        try:
            data = _extract_one(video_id, clients, ejs=True)
            if turn_on_count >= EJS_FALHAS_PARA_LIGAR:
                _set_ejs(True, f"{_label(clients)} só entregou a URL com o EJS")
            return data
        except Exception:
            raise first

def _probe_url_ok(data: dict) -> tuple[bool, int | None]:
    """Confirma que o CDN (googlevideo) aceita a URL extraída (sem 403/410)."""
    url = (data or {}).get("url") or ""
    if not url:
        return False, None
    hdrs = {"User-Agent": UA, **(data.get("headers") or {})}
    proxies = {"https": YT_PROXY} if YT_PROXY else None
    for rh in ({"Range": "bytes=0-1023"}, {}):
        try:
            r = session.get(url, headers={**hdrs, **rh}, stream=True, timeout=(3, 5), proxies=proxies)
            st = r.status_code
            r.close()
            if st < 400:
                return True, st
            if st != 403:
                return False, st
        except Exception:
            return False, None
    return False, 403


def _probe_engine(full: bool = True) -> dict:
    """Sondagem: mede os clients de verdade (vídeo-canário) e calibra o motor.
    Roda em segundo plano e PARA se alguém começar a tocar — sondagem nunca
    disputa banda com o play do usuário."""
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "motivo": "sondagem já em andamento"}
    try:
        with _health_lock:
            canario = _EJS["probe_video"]
        resultado = {}
        try:
            if not full:
                alvos = [PLAYER_CLIENTS[0]]
            else:
                alvos = _ordered_clients([c for c in _clients_full()])[:4]
            for clients in alvos:
                if _wanted:                     # usuário tocando: sai de cena
                    print("🧪 sondagem pausada: já tem faixa tocando")
                    break
                lbl = _label(clients)
                t0 = time.time()
                try:
                    d = _extract_one(canario, clients, ejs=False)
                    dt = time.time() - t0
                    ok_cdn, st_cdn = _probe_url_ok(d)
                    if ok_cdn:
                        _mark_client_ms(lbl, dt)
                        resultado[lbl] = {"ms": int(dt * 1000), "ok": True}
                    else:
                        with _health_lock:
                            _CLIENT_MS.pop(lbl, None)
                        resultado[lbl] = {"ms": None, "ok": False, "erro": f"CDN HTTP {st_cdn}"}
                        print(f"🧪 {lbl}: extraiu URL mas CDN devolveu HTTP {st_cdn}")
                except Exception as e:
                    with _health_lock:
                        _CLIENT_MS.pop(lbl, None)
                    resultado[lbl] = {"ms": None, "ok": False, "erro": str(e)[:90]}
                    print(f"🧪 {lbl}: enxuto falhou na sondagem ({str(e)[:70]})")
                time.sleep(0.8)                  # não vira rajada no YouTube
        finally:
            with _health_lock:
                _EJS["last_ms"] = {k: v.get("ms") for k, v in resultado.items()}
                _EJS["at"] = time.time()
                ms_snap = dict(_CLIENT_MS)
                ejs_val = _EJS.get("needed")
            if resultado and not any(v["ok"] for v in resultado.values()):
                _set_ejs(True, "nenhum client entregou URL no caminho enxuto")
            elif any(v["ok"] for v in resultado.values()):
                _set_ejs(False, "")
            try:
                SETTINGS["client_ms"] = ms_snap
                _save_settings()
            except Exception:
                pass
            with _health_lock:
                ejs_val = _EJS.get("needed")
        print(f"🧪 sondagem concluída: {resultado}")
        return {"ok": True, "resultado": resultado, "ejs": ejs_val}
    finally:
        _probe_lock.release()


def _probe_boot():
    """Chamada no arranque: espera o servidor respirar e mede sem pressa."""
    time.sleep(6)
    try:
        _probe_engine(full=True)
    except Exception as e:
        print(f"🧪 sondagem de arranque falhou: {str(e)[:120]}")


def _warm_ttfb(data: dict):
    """Aquece DNS+TCP+TLS do host do googlevideo com um Range de 1 KB. O <audio>
    paga ~1,4s no 1º byte; parte disso é handshake que dá para adiantar aqui."""
    url = (data or {}).get("url")
    if not url:
        return
    def run():
        try:
            headers = {"User-Agent": UA, "Range": "bytes=0-1023", **(data.get("headers") or {})}
            r = session.get(url, headers=headers, stream=True, timeout=(4, 8),
                            proxies={"https": YT_PROXY} if YT_PROXY else None)
            r.close()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()

# ---------- Configurações em tempo de execução (settings.json) ----------
SETTINGS_FILE = DATA / "settings.json"
SETTINGS: dict = {}

def _load_settings():
    d = {
        "cache_enabled": True,                 # cache de áudio em disco (pasta cache/)
        "cache_limit_mb": 512,                 # limite do cache (50–1024)
        "prefetch_next": True,                 # pré-baixa a próxima faixa da fila para o cache
        "race_wave": RACE_WAVE,                # clients em paralelo por onda (1–4)
        "extract_timeout": EXTRACT_TIMEOUT,    # timeout por onda (10–60s)
        "mp3_quality": "192",                  # bitrate dos downloads (128/192/320)
        "force_ipv4": FORCE_IPV4,              # era variável de ambiente; agora dá pra ligar no painel
        "use_cookies": USE_COOKIES,            # idem (precisa do cookies.txt na pasta)
        "smart_cache": True,                   # cache inteligente: repetidas/favoritas sobrevivem mais
        "learn_next": True,                    # aprende o que você ouve em seguida e pré-guarda
        "pairing_required": False,             # fechadura: só entra quem pareou pelo QR
        "pair_key": "",                        # chave do pareamento; vazia = gera na 1ª necessidade
        "cloudflare_tunnel": False,            # túnel Cloudflare Quick Tunnel (acesso na rua sem abrir portas)
        "ejs_needed": None,                    # None = sondagem ainda não decidiu
        "client_ms": {},                       # latência medida por client (ms)
    }
    if SETTINGS_FILE.exists():
        try:
            d.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"⚠️ settings.json inválido ({e}); usando padrões.")
    # migração: teto por onda abaixo de 15s zerava ondas inteiras (sucessos reais
    # em 7.8–8.8s e o stall de IPv6 já resolvido — valores velhos do painel, 10/12s,
    # eram calibração de outra era). Sobe para o novo padrão.
    if int(d.get("extract_timeout") or 0) < EXTRACT_TIMEOUT:
        d["extract_timeout"] = EXTRACT_TIMEOUT
    SETTINGS.clear()
    SETTINGS.update(d)
    # calibração medida em execuções anteriores (economiza a 1ª extração lenta)
    try:
        if d.get("ejs_needed") is not None:
            _EJS["needed"] = bool(d["ejs_needed"])
        for k, v in (d.get("client_ms") or {}).items():
            if isinstance(v, (int, float)) and v > 0:
                _CLIENT_MS[str(k)] = int(v)
    except Exception:
        pass

_settings_lock = threading.Lock()

def _save_settings():
    with _settings_lock:
        try:
            payload = json.dumps(dict(SETTINGS), indent=2, ensure_ascii=False)
            tmp = SETTINGS_FILE.with_name(f"settings.{uuid.uuid4().hex[:6]}.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, SETTINGS_FILE)
        except Exception as e:
            print(f"⚠️ não consegui salvar settings.json: {e}")

_load_settings()

# Vídeos com pedido de stream ATIVO. Suporta vários aparelhos na rede ao mesmo
# tempo: a troca de música num aparelho NÃO cancela a extração do outro — uma
# extração "urgent" só é cancelada quando NINGUÉM está mais pedindo aquele vídeo.
_wanted: dict[str, int] = {}
_wanted_lock = threading.Lock()

def _want(video_id: str):
    with _wanted_lock:
        _wanted[video_id] = _wanted.get(video_id, 0) + 1

def _unwant(video_id: str):
    with _wanted_lock:
        n = _wanted.get(video_id, 0) - 1
        if n <= 0:
            _wanted.pop(video_id, None)
        else:
            _wanted[video_id] = n

def _wait_if_user_busy(max_wait: float = 15.0):
    """Pausa extrações de fundo (pré-cache/busca) enquanto há play ativo."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        with _wanted_lock:
            busy = bool(_wanted)
        if not busy:
            return
        time.sleep(0.35)

_last_ok = {"label": None}    # último client que entregou áudio SEM 403 → tenta primeiro na próxima

def _record_cdn_ok(lbl: str):
    if not lbl:
        return
    with _health_lock:
        _last_ok["label"] = lbl
    _client_mark_ok(lbl)

def _prefer_last_ok(clients):
    with _health_lock:
        last = _last_ok["label"]
    if not last:
        return clients
    first = [c for c in clients if _label(c) == last]
    return first + [c for c in clients if _label(c) != last]

def _label(clients):
    return ",".join(clients) if clients else "padrão"

def _mark_bad_client(video_id: str, client: str):
    if not client:
        return
    with _cache_lock:
        ts, s = _bad_clients.get(video_id, (0.0, set()))
        if time.time() - ts > BAD_CLIENT_TTL:
            s = set()
        s.add(client)
        _bad_clients[video_id] = (time.time(), s)

def _bad_clients_for(video_id: str) -> set:
    with _cache_lock:
        ts, s = _bad_clients.get(video_id, (0.0, set()))
        return set(s) if s and time.time() - ts <= BAD_CLIENT_TTL else set()

def _base_ydl_opts(ejs: bool | None = None) -> dict:
    """ejs: força (True) ou dispensa (False) o solucionador JS remoto. None =
    decide pelo que a sondagem mediu (_EJS). Medido nesta máquina: o EJS custa
    ~3s POR EXTRAÇÃO (4,07s → 7,07s) e só acrescenta 1 formato de áudio."""
    if ejs is None:
        ejs = bool(_EJS.get("needed"))
    opts = {
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 15,
        "retries": 3,
        "extractor_retries": 3,      # "The page needs to be reloaded" é intermitente: retry resolve
        "file_access_retries": 3,
        "geo_bypass": True,
        # PO Token automático via plugin bgutil (o equivalente do zemer-cipher que
        # o Metrolist usa): sem ele, os formatos https do mweb/web vêm SEM token e
        # "may yield HTTP Error 403" — exatamente os 403 do stream que víamos.
        # O Deno resolve sozinho (script mode, já instalado); token fica em cache.
        "extractor_args": {"youtube": {"fetch_pot": ["auto"]}},
    }
    if ejs:
        # Plano B, não pedágio: só entra quando o caminho enxuto falha (ou quando
        # a sondagem provou que o player atual exige). Era o maior custo oculto
        # da extração — ~3s por faixa.
        opts["remote_components"] = ["ejs:github"]
    if YT_PROXY:
        opts["proxy"] = YT_PROXY
    if SETTINGS.get("force_ipv4"):
        opts["source_address"] = "0.0.0.0"
    if SETTINGS.get("use_cookies") and COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts

def _is_direct_media(url: str, proto: str) -> bool:
    """True para links de mídia direta (googlevideo). Manifest HLS/DASH não toca
    no <audio> do navegador — nesse caso preferimos falhar e deixar a onda
    tentar o próximo client."""
    if not url:
        return False
    low = (proto or "").lower()
    if "m3u8" in low or "dash" in low or "manifest" in low:
        return False
    if ".m3u8" in url or "/manifest/" in url or url.endswith(".mpd"):
        return False
    return True

def _audio_alts(fmts, chosen: str) -> list:
    """Até 2 URLs de áudio direto ALTERNATIVAS (outros itags) da mesma extração.
    O 403 do googlevideo às vezes vale só para um formato (SABR trava o opus e
    libera o m4a, p.ex.) — ter um plano B do MESMO client evita re-extração."""
    out, seen = [], set()
    for f in fmts or []:
        fu = f.get("url") or ""
        fp = (f.get("protocol") or "").split("+")[0]
        if not fu or fu == chosen:
            continue
        if f.get("vcodec") not in (None, "none") or f.get("acodec") in (None, "none"):
            continue
        if fp not in ("https", "http") or not _is_direct_media(fu, fp):
            continue
        m = re.search(r"[?&]itag=(\d+)", fu)
        k = m.group(1) if m else fu[:80]
        if k in seen:
            continue
        seen.add(k)
        out.append((f.get("ext") == "m4a", f.get("abr") or f.get("tbr") or 0, fu))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [u for _, _, u in out[:2]]

def _extract_one(video_id: str, clients, ejs: bool | None = None) -> dict:
    opts = _base_ydl_opts(ejs)
    opts["format"] = "bestaudio[ext=m4a]/bestaudio"
    if clients:
        opts["extractor_args"]["youtube"]["player_client"] = clients
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if not info:
        raise RuntimeError(f"sem dados ({_label(clients)})")
    hdrs = info.get("http_headers") or {}
    url = info.get("url") or ""
    if info.get("vcodec") in (None, "none") and _is_direct_media(url, info.get("protocol") or ""):
        return {"url": url, "headers": hdrs, "client": _label(clients),
                "alts": _audio_alts(info.get("formats"), url)}
    # O formato escolhido veio como manifest (HLS/DASH) ou vídeo+áudio: procura áudio direto.
    cands = []
    for f in info.get("formats") or []:
        fu = f.get("url") or ""
        fp = (f.get("protocol") or "").split("+")[0]
        if f.get("vcodec") not in (None, "none"):
            continue
        if f.get("acodec") in (None, "none"):
            continue
        if fp not in ("https", "http") or not _is_direct_media(fu, fp):
            continue
        cands.append(f)
    if not cands:
        raise RuntimeError(f"sem áudio direto disponível ({_label(clients)})")
    cands.sort(key=lambda f: (f.get("ext") == "m4a", f.get("abr") or f.get("tbr") or 0), reverse=True)
    f = cands[0]
    return {"url": f["url"], "headers": f.get("http_headers") or hdrs, "client": _label(clients),
            "alts": _audio_alts(cands[1:], f["url"])}

def _extract(video_id: str, avoid=(), urgent: bool = False) -> dict:
    avoid = set(avoid)
    # evita clients marcados como ruins para este vídeo (403 recente no stream)
    _full = _clients_full()   # web_safari/tv só entram com cookies (gemini 3.8, log real)
    clients_list = [c for c in _full if _label(c) not in avoid] or list(_full)
    clients_list, de_folga = _client_healthy(clients_list)
    if de_folga:
        print(f"ℹ️ {video_id}: clients de folga nesta rodada: {', '.join(de_folga)}")
    if not clients_list:            # teoricamente impossível (sondagem libera 1x); por segurança
        clients_list = list(_full)
    # quem a sondagem mediu mais rápido vai na frente, e o último validado sem 403 no CDN vence empates
    clients_list = _prefer_last_ok(_ordered_clients(clients_list))
    wave_n = max(1, min(4, int(SETTINGS.get("race_wave") or RACE_WAVE)))
    wave_to = max(5, min(90, int(SETTINGS.get("extract_timeout") or EXTRACT_TIMEOUT)))
    # 1ª onda = UM client só (o mais rápido medido). Medido nesta máquina: 3
    # clients em paralelo gastam 13,9s onde 1 gasta 8,9s, porque o YouTube
    # serializa pedidos do MESMO vídeo e o PO token/Deno disputa CPU. O
    # paralelismo continua valendo — mas como RESGATE (ondas seguintes), não
    # como caminho crítico, onde ele só atrasa o primeiro byte.
    resto = clients_list[1:]
    passo = max(1, wave_n - 1) if wave_n > 1 else 1
    waves = [clients_list[:1]] + ([resto[i:i + passo] for i in range(0, len(resto), passo)] if resto else [])
    start = time.time()
    deadline = start + TOTAL_EXTRACT_TIMEOUT
    errors = []
    for wi, wave in enumerate(waves):
        # ninguém está mais pedindo este vídeo? larga esta antes de gastar mais rede
        # (com vários aparelhos, só cancela quando o vídeo ficou sem pedido ativo)
        if urgent and video_id not in _wanted:
            errors.append("cancelado: ninguém está mais pedindo este vídeo")
            break
        remaining = deadline - time.time()
        if remaining <= 0:
            errors.append("tempo esgotado")
            break
        # teto de 18s na 1ª onda evita abandonar uma extração quase pronta (12–15s)
        # e disparar a 2ª onda em paralelo competindo por Deno/YouTube
        teto = wave_to if (wi or _EJS.get("needed")) else min(wave_to, 18)
        futs = {_extract_pool.submit(_extract_one_adaptive, video_id, c): c for c in wave}
        try:
            for f in cf.as_completed(futs, timeout=min(teto, remaining)):
                try:
                    data = f.result()
                    print(f"✅ URL de {video_id} via {data['client']} em {time.time() - start:.2f}s")
                    return data
                except Exception as e:
                    errors.append(f"{_label(futs[f])}: {str(e)[:160]}")
                    _client_mark_fail(_label(futs[f]))
                    print(f"⚠️ {_label(futs[f])} falhou para {video_id}: {str(e)[:140]}")
        except cf.TimeoutError:
            errors.append(f"timeout ({','.join(_label(c) for c in wave)})")
    raise RuntimeError(" | ".join(errors) or "falha desconhecida")

def get_stream_data(video_id: str, force: bool = False, avoid=(),
                    ignore_cooldown: bool = False, urgent: bool = False) -> dict:
    now = time.time()
    avoid = set(avoid)
    # Com 'avoid' (renovação pós-403) o cache é ignorado: a URL guardada é
    # justamente a do client que acabou de falhar.
    if not force and not avoid:
        with _cache_lock:
            hit = _stream_cache.get(video_id)
        if hit and now - hit[0] < URL_TTL:
            return hit[1]
        # O cooldown de falha existe pra proteger as tentativas DE FUNDO
        # (pré-cache, rádio) de martelar o YouTube. O clique do usuário
        # (urgent=True) NUNCA pode levar 502 instantâneo "aguardando Xs"
        # porque uma tentativa de fundo falhou há 3s: ele espera a extração
        # em andamento (single-flight abaixo) ou tenta de novo na hora.
        if not urgent and not ignore_cooldown:
            with _cache_lock:
                f = _fail_cache.get(video_id)
                if f and now - f[0] < FAIL_COOLDOWN:
                    restante = FAIL_COOLDOWN - (now - f[0])
                    raise RuntimeError(f"[aguardando {restante:.0f}s antes de tentar de novo] {f[1]}")
    # single-flight: se já há extração em andamento do mesmo vídeo, aguarda ela
    # em vez de disparar outra (o prefetch /api/resolve cruzando com /api/stream
    # duplicava o trabalho e dobrava as requisições ao YouTube)
    key = (video_id, tuple(sorted(avoid)))
    with _inflight_lock:
        fut = _inflight.get(key)
        mine = fut is None
        if mine:
            fut = _orch_pool.submit(_extract, video_id, avoid, urgent)
            _inflight[key] = fut
    try:
        data = fut.result()
    except Exception as e:
        # cancelamento (troca de música) não é falha do vídeo: não grava cooldown
        if mine and "cancelado" not in str(e):
            with _cache_lock:
                _fail_cache[video_id] = (time.time(), str(e)[:300])
                if len(_fail_cache) > 200:
                    for k_old, _ in sorted(_fail_cache.items(), key=lambda kv: kv[1][0])[:100]:
                        _fail_cache.pop(k_old, None)
        raise
    finally:
        with _inflight_lock:
            if _inflight.get(key) is fut:
                _inflight.pop(key, None)
    with _cache_lock:
        _stream_cache[video_id] = (time.time(), data)
        _fail_cache.pop(video_id, None)
        if len(_stream_cache) > MAX_CACHE:   # limpeza simples dos mais antigos
            for k, _ in sorted(_stream_cache.items(), key=lambda kv: kv[1][0])[:MAX_CACHE // 2]:
                _stream_cache.pop(k, None)
    return data

def _safe_prewarm(vid: str):
    try:
        _wait_if_user_busy()
        data = get_stream_data(vid)
        # Aquece DNS/TCP/TLS do googlevideo AQUI (pré-carga), não no clique: quando
        # o play chegar, o /api/stream reusa a conexão e o 1º byte vem pronto.
        _warm_ttfb(data)
    except Exception:
        pass

def _enqueue_warm(ids: list):
    """Manda pra fila de pré-cache sequencial (respeita os switches do painel)."""
    if not (SETTINGS.get("cache_enabled") and SETTINGS.get("prefetch_next", True)):
        return
    _ensure_warm_worker()
    added = False
    with _warm_cond:
        for v in ids[:50]:
            v = str(v or "")
            if VID_RE.match(v) and v not in _warm_set:
                _warm_set.add(v)
                _warm_q.append(v)
                added = True
        if added:
            _warm_cond.notify_all()

def prewarm(video_ids: list[str]):
    # Enfileira sequencialmente em vez de competir em paralelo com o play atual.
    if not video_ids:
        return
    _enqueue_warm(video_ids)

# Sessão HTTP reaproveitada para os trechos de áudio
session = _mount_ipv4(requests.Session())

# ---------- Cache de áudio em disco (LRU, limite ajustável no painel) ----------
CACHE_DIR = DATA / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")     # formato de videoId do YouTube
UP_RE = re.compile(r"^up_[a-f0-9]{10}$")         # formato de id de arquivo local próprio (upload)
_TRACK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

def _valid_track_id(video_id: str) -> str:
    vid = str(video_id or "")
    if not _TRACK_ID_RE.match(vid):
        raise HTTPException(400, "video_id inválido")
    return vid
UP_DIR = DL_DIR / "upload"
UP_DIR.mkdir(exist_ok=True)

# ---------- Capas (thumbnails) via proxy local ----------
# Serve as capas DO PRÓPRIO SERVIDOR com cache em disco: não depende de
# i.ytimg.com/googleusercontent carregarem no cliente (era o motivo das capas
# falharem no desktop/WebView), acelera as listas e funciona até no offline.
THUMB_DIR = CACHE_DIR / "thumbs"
THUMB_DIR.mkdir(exist_ok=True)

def _thumb_bytes(video_id: str):
    f = THUMB_DIR / f"{video_id}.jpg"
    if f.is_file() and f.stat().st_size > 0:
        return f.read_bytes()
    for url in (f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                f"https://i.ytimg.com/vi/{video_id}/default.jpg"):
        try:
            r = session.get(url, timeout=(4, 8), headers={"User-Agent": UA})
            if r.status_code == 200 and len(r.content) > 500:
                tmp = THUMB_DIR / f"{video_id}.{uuid.uuid4().hex[:6]}.tmp"
                tmp.write_bytes(r.content)
                os.replace(tmp, f)
                return r.content
        except Exception:
            continue
    return None

@app.get("/api/thumb/{video_id}")
def thumb(video_id: str):
    if UP_RE.match(video_id):
        with db() as con:
            rec = con.execute("SELECT art FROM uploads WHERE id=?", (video_id,)).fetchone()
        if rec and rec["art"]:
            p = (DATA / rec["art"]).resolve()
            if p.parent == UP_DIR.resolve() and p.is_file():
                return FileResponse(p, media_type="image/jpeg",
                                    headers={"Cache-Control": "public, max-age=86400"})
        raise HTTPException(404, "upload sem capa embutida")

    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    data = _thumb_bytes(video_id)
    if data is None:
        raise HTTPException(404, "capa não encontrada")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=604800"})

_disk_lock = threading.Lock()
_prefetch_lock = threading.Lock()
_prefetch_active: set = set()
_DEFAULT_CACHE_DIR = CACHE_DIR
_cache_touch_mem: dict[str, float] = {}
_cache_touch_lock = threading.Lock()
_CACHE_TOUCH_TTL = 60.0
_last_part_prune = 0.0

def _use_cache_db() -> bool:
    return CACHE_DIR == _DEFAULT_CACHE_DIR

def _cache_paths(vid: str):
    return CACHE_DIR / f"{vid}.audio", CACHE_DIR / f"{vid}.meta.json"

def _cache_db_upsert(vid: str, m: dict):
    if not _use_cache_db():
        return
    try:
        now = time.time()
        with db() as con:
            con.execute(
                """INSERT INTO cache_entries (video_id, content_type, size, created, at, title, artist, client)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(video_id) DO UPDATE SET
                       content_type=excluded.content_type,
                       size=excluded.size,
                       created=excluded.created,
                       at=excluded.at,
                       title=COALESCE(NULLIF(excluded.title, ''), cache_entries.title),
                       artist=COALESCE(NULLIF(excluded.artist, ''), cache_entries.artist),
                       client=COALESCE(NULLIF(excluded.client, ''), cache_entries.client)""",
                (vid, str(m.get("content_type") or "audio/mp4"), int(m.get("size") or 0),
                 float(m.get("created") or m.get("at") or now), float(m.get("at") or now),
                 str(m.get("title") or ""), str(m.get("artist") or ""), str(m.get("client") or ""))
            )
    except Exception:
        pass

def _cache_db_delete(vid: str):
    if not _use_cache_db():
        return
    try:
        with db() as con:
            con.execute("DELETE FROM cache_entries WHERE video_id=?", (vid,))
    except Exception:
        pass

def _cache_sync_db():
    """Sincroniza .meta.json existentes no disco para a tabela cache_entries no arranque."""
    if not _use_cache_db():
        return
    try:
        rows = []
        for meta_p in CACHE_DIR.glob("*.meta.json"):
            vid = meta_p.name[:-len(".meta.json")]
            audio, _ = _cache_paths(vid)
            if not audio.is_file():
                meta_p.unlink(missing_ok=True)
                continue
            try:
                m = json.loads(meta_p.read_text(encoding="utf-8"))
                sz = audio.stat().st_size
                if sz <= 0 or int(m.get("size") or 0) != sz:
                    meta_p.unlink(missing_ok=True)
                    continue
                now = time.time()
                rows.append((
                    vid, str(m.get("content_type") or "audio/mp4"), sz,
                    float(m.get("created") or m.get("at") or now),
                    float(m.get("at") or now),
                    str(m.get("title") or ""), str(m.get("artist") or ""), str(m.get("client") or "")
                ))
            except Exception:
                meta_p.unlink(missing_ok=True)
        if rows:
            with db() as con:
                con.executemany(
                    """INSERT OR REPLACE INTO cache_entries
                       (video_id, content_type, size, created, at, title, artist, client)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows
                )
    except Exception:
        pass

_cache_sync_db()

def _cache_write_meta(vid: str, m: dict):
    _, meta_p = _cache_paths(vid)
    tmp = CACHE_DIR / f"{vid}.{uuid.uuid4().hex[:6]}.mtmp"
    tmp.write_text(json.dumps(m), encoding="utf-8")
    os.replace(tmp, meta_p)
    _cache_db_upsert(vid, m)

def _cache_lookup(vid: str):
    """Meta válido (áudio existe e o tamanho bate) ou None — se corrompido, apaga."""
    if not VID_RE.match(vid or ""):
        return None
    audio, meta_p = _cache_paths(vid)
    if not audio.is_file():
        meta_p.unlink(missing_ok=True)
        _cache_db_delete(vid)
        return None
    try:
        actual_sz = audio.stat().st_size
        if actual_sz <= 0:
            raise ValueError("arquivo vazio")
    except Exception:
        return None
    if _use_cache_db():
        try:
            with db() as con:
                r = con.execute(
                    "SELECT video_id, content_type, size, created, at, title, artist, client FROM cache_entries WHERE video_id=?",
                    (vid,)
                ).fetchone()
            if r and int(r["size"] or 0) == actual_sz:
                return dict(r)
        except Exception:
            pass
    if not meta_p.is_file():
        _cache_db_delete(vid)
        return None
    try:
        m = json.loads(meta_p.read_text(encoding="utf-8"))
        if int(m.get("size") or 0) != actual_sz:
            raise ValueError("tamanho divergente")
        _cache_db_upsert(vid, m)
        return m
    except Exception:
        try:
            meta_p.unlink(missing_ok=True)
        except Exception:
            pass
        _cache_db_delete(vid)
        return None

def _cache_limit_bytes() -> int:
    return max(50, int(SETTINGS.get("cache_limit_mb") or 512)) * 1024 * 1024

def _cache_evict():
    """Apaga itens até caber no limite. Com o cache INTELIGENTE ligado, a ordem de
    saída não é só "o mais antigo": cada repetição prorroga a permanência (música
    que você volta a ouvir fica) e favoritos só saem em último caso. Limpa órfãos."""
    global _last_part_prune
    limit = _cache_limit_bytes()
    smart = bool(SETTINGS.get("smart_cache", True))
    now = time.time()

    with _disk_lock:
        prune_parts = (now - _last_part_prune) > 300
        if prune_parts:
            _last_part_prune = now

    if prune_parts:
        for junk in CACHE_DIR.glob("*.part"):
            try:
                if now - junk.stat().st_mtime > 3600:
                    junk.unlink()
            except Exception:
                pass

    entries = []   # (vid, sz, at, audio, meta_p)
    total = 0

    if _use_cache_db():
        try:
            with db() as con:
                row_sum = con.execute("SELECT COALESCE(SUM(size), 0), COUNT(*) FROM cache_entries").fetchone()
                total_db, cnt_db = int(row_sum[0] or 0), int(row_sum[1] or 0)
            if cnt_db == 0 and any(CACHE_DIR.glob("*.meta.json")):
                _cache_sync_db()
                with db() as con:
                    row_sum = con.execute("SELECT COALESCE(SUM(size), 0), COUNT(*) FROM cache_entries").fetchone()
                    total_db = int(row_sum[0] or 0)
            if total_db <= limit:
                return
            with db() as con:
                db_rows = con.execute("SELECT video_id, size, at FROM cache_entries").fetchall()
            stale = []
            for r in db_rows:
                vid = r["video_id"]
                audio, meta_p = _cache_paths(vid)
                if not audio.is_file():
                    meta_p.unlink(missing_ok=True)
                    stale.append(vid)
                    continue
                sz = int(r["size"] or 0) or audio.stat().st_size
                at = float(r["at"] or 0)
                total += sz
                entries.append((vid, sz, at, audio, meta_p))
            if stale:
                with db() as con:
                    con.executemany("DELETE FROM cache_entries WHERE video_id=?", [(v,) for v in stale])
            if total <= limit:
                return
        except Exception:
            entries = []
            total = 0

    if not entries and not _use_cache_db():
        for meta_p in CACHE_DIR.glob("*.meta.json"):
            vid = meta_p.name[:-len(".meta.json")]
            audio, _ = _cache_paths(vid)
            if not audio.is_file():
                meta_p.unlink(missing_ok=True)
                continue
            try:
                m = json.loads(meta_p.read_text(encoding="utf-8"))
                sz = audio.stat().st_size
                at = float(m.get("at") or 0)
                total += sz
                entries.append((vid, sz, at, audio, meta_p))
            except Exception:
                meta_p.unlink(missing_ok=True)
        if total <= limit:
            return

    if not entries:
        return

    favs, plays_map = set(), {}
    if smart:
        try:
            with db() as con:
                favs = {r[0] for r in con.execute("SELECT DISTINCT video_id FROM favorites ORDER BY added_at DESC LIMIT 500")}
                plays_map = {r[0]: int(r[1] or 0) for r in con.execute("SELECT video_id, n FROM plays")}
        except Exception:
            favs, plays_map = set(), {}

    scored = [
        (at + (min(int(plays_map.get(vid, 0)), 48) * 3 * 3600 if smart else 0), at, vid, audio, meta_p, sz)
        for vid, sz, at, audio, meta_p in entries
    ]

    evicted_vids = []
    with _disk_lock:
        dead = set()

        def _kill(e):
            nonlocal total
            try:
                e[3].unlink(missing_ok=True)
                e[4].unlink(missing_ok=True)
            except Exception:
                pass
            total -= e[5]
            dead.add(e[2])
            evicted_vids.append(e[2])

        # passada 1: não-favoritos (mais antigos primeiro, já com o bônus de replay)
        pool = [e for e in scored if not (smart and e[2] in favs)]
        for e in sorted(pool, key=lambda x: x[0]):
            if total <= limit:
                break
            _kill(e)
        # passada 2: mesmo favorito sai se NADA mais resolver (mais antigo primeiro)
        if total > limit:
            for e in sorted([e for e in scored if e[2] not in dead], key=lambda x: x[1]):
                if total <= limit:
                    break
                _kill(e)

    if evicted_vids:
        with _cache_touch_lock:
            for v in evicted_vids:
                _cache_touch_mem.pop(v, None)
        if _use_cache_db():
            try:
                with db() as con:
                    con.executemany("DELETE FROM cache_entries WHERE video_id=?", [(v,) for v in evicted_vids])
            except Exception:
                pass

def _cache_plan(resp, rng):
    """(tamanho_esperado, content_type) se a resposta puder virar arquivo de cache."""
    if not SETTINGS.get("cache_enabled"):
        return None
    try:
        if resp.headers.get("Content-Encoding"):
            return None                       # comprimida: o tamanho não confere
        ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ct and not ct.startswith("audio/"):
            return None
        if resp.status_code not in (200, 206):
            return None
        expected = int(resp.headers.get("Content-Length") or 0)
        if resp.status_code == 206:
            # Só entra no cache o que é o ARQUIVO INTEIRO. Antes bastava começar em 0:
            # um pedido `Range: bytes=0-2047` do <audio> (o navegador sempre manda
            # Range!) gravava 2 KB como se fossem a música toda — o cache envenenava
            # e a faixa "parava" em 1 segundo nas próximas vezes. Reproduzido.
            cr = re.match(r"^bytes (\d+)-(\d+)/(\d+)$",
                          (resp.headers.get("Content-Range") or "").strip())
            if not cr:
                return None
            _start, _end, _total = (int(x) for x in cr.groups())
            if _start != 0 or _end != _total - 1 or expected != _total:
                return None                   # recorte do meio: nada de cache
        if expected <= 0 or expected > _cache_limit_bytes():
            return None
        if rng:
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if not m:
                return None
            a, b = m.groups()
            if a == "" and b:
                return None                   # "N últimos bytes" não vira cache
            if a and int(a) != 0:
                return None                   # pediu o meio do arquivo: nada de cache
        return (expected, ct or "audio/mp4")
    except Exception:
        return None

def _cache_promote(vid: str, tmp: Path, n: int, ct: str,
                   title: str = "", artist: str = "", client: str = "") -> bool:
    """.part completo → arquivo final + meta (atômico)."""
    try:
        audio, _ = _cache_paths(vid)
        with _disk_lock:
            os.replace(tmp, audio)
            now = time.time()
            _cache_write_meta(vid, {"video_id": vid, "content_type": ct, "size": n,
                                    "created": now, "at": now, "title": title or "",
                                    "artist": artist or "", "client": client or ""})
        _cache_evict()
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False

def _cache_store(vid: str, resp, expected: int, ct: str,
                 title: str = "", artist: str = "", client: str = "") -> bool:
    """Baixa a resposta inteira para o cache (usado pelo pré-carregamento)."""
    tmp = CACHE_DIR / f"{vid}.{uuid.uuid4().hex[:8]}.part"
    try:
        n = 0
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    f.write(chunk)
                    n += len(chunk)
        if n != expected:
            tmp.unlink(missing_ok=True)
            return False
        return _cache_promote(vid, tmp, n, ct, title=title, artist=artist, client=client)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False

def _serve_file(p: Path, rng):
    """Serve um arquivo de áudio do disco com suporte a Range (seek incluído)."""
    ctype = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".webm": "audio/webm",
             ".opus": "audio/ogg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
             ".flac": "audio/flac", ".wav": "audio/wav", ".mp4": "audio/mp4",
             }.get(p.suffix.lower(), "audio/mpeg")
    size = p.stat().st_size
    start, end, status = 0, size - 1, 200
    if rng:
        mt = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
        if not mt:
            return None
        a, b = mt.groups()
        if a == "" and b == "":
            return None
        if a == "":
            start = max(0, size - int(b))
        else:
            start = int(a)
            if start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            end = int(b) if b else size - 1
        end = min(end, size - 1)
        status = 206
    length = end - start + 1
    headers = {
        "Content-Type": ctype,
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def _dl_iter():
        with open(p, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(_dl_iter(), status_code=status, headers=headers)

def _serve_from_download(video_id: str, rng):
    """Música baixada = biblioteca local: serve o MP3/m4a DIRETO do disco,
    sem YouTube e sem gastar cache (estilo Koel/Navidrome). None = sem download."""
    p = _dl_index_get(video_id)
    if p is None:
        return None
    return _serve_file(p, rng)

def _serve_from_cache(vid: str, rng):
    """Responde do cache local com suporte a Range; None = cache miss."""
    if not SETTINGS.get("cache_enabled"):
        return None
    m = _cache_lookup(vid)
    if not m:
        return None
    audio, _ = _cache_paths(vid)
    size = int(m["size"])
    start, end, status = 0, size - 1, 200
    if rng:
        mt = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
        if not mt:
            return None
        a, b = mt.groups()
        if a == "" and b == "":
            return None
        if a == "":
            start = max(0, size - int(b))
        else:
            start = int(a)
            if start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            end = int(b) if b else size - 1
        end = min(end, size - 1)
        status = 206
    length = end - start + 1
    headers = {
        "Content-Type": m.get("content_type") or "audio/mp4",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def _disk_iter():
        with open(audio, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    now_t = time.time()
    should_touch = False
    with _cache_touch_lock:
        if now_t - _cache_touch_mem.get(vid, 0.0) >= _CACHE_TOUCH_TTL:
            _cache_touch_mem[vid] = now_t
            should_touch = True
    if should_touch:
        def _touch():
            m2 = dict(m)
            m2["at"] = now_t
            try:
                _cache_write_meta(vid, m2)
            except Exception:
                pass
        threading.Thread(target=_touch, daemon=True).start()
    return StreamingResponse(_disk_iter(), status_code=status, headers=headers)

def _prefetch_audio(vid: str, title: str = "", artist: str = ""):
    """Pré-baixa a próxima faixa da fila para o cache (não bloqueia o play atual)."""
    if not VID_RE.match(vid or ""):
        return
    with _prefetch_lock:
        if vid in _prefetch_active:
            return
        _prefetch_active.add(vid)
    try:
        if _cache_lookup(vid):
            return
        upstream, data = open_upstream(vid, None)
        if upstream.status_code >= 400:
            st = upstream.status_code
            c = (data or {}).get("client", "")
            upstream.close()
            _mark_bad_client(vid, c)
            if st in (403, 410, 429) and c:
                _client_mark_fail(c)
                with _health_lock:
                    _CLIENT_MS.pop(c, None)
                    if _last_ok.get("label") == c:
                        _last_ok["label"] = None
            with _cache_lock:
                cur = _stream_cache.get(vid)
                if cur is not None and cur[1] is data:
                    _stream_cache.pop(vid, None)
            upstream, data = open_upstream(vid, None, force=True, avoid=_bad_clients_for(vid))
        try:
            if upstream.status_code < 400:
                _record_cdn_ok((data or {}).get("client", ""))
            plan = _cache_plan(upstream, None)
            if plan:
                expected, ct = plan
                if _cache_store(vid, upstream, expected, ct,
                                title=title, artist=artist, client=data.get("client", "")):
                    print(f"💾 Pré-cache de {vid} concluído ({expected / 1048576:.1f} MB)")
        finally:
            upstream.close()
    except Exception as e:
        print(f"⚠️ Pré-cache de {vid} falhou: {str(e)[:120]}")
    finally:
        with _prefetch_lock:
            _prefetch_active.discard(vid)

# ---------- Fila de pré-cache sequencial (leitura à frente da fila) ----------
# O painel já pré-baixa a próxima faixa; aqui vai uma fila em ORDEM para quando
# o usuário está ouvindo uma playlist: as próximas N vão entrando no disco uma
# por vez — quando a música atual acaba, a seguinte já está lida do cache.
_warm_q: list = []
_warm_set: set = set()
_warm_lock = threading.Lock()
_warm_cond = threading.Condition(_warm_lock)
_warm_worker_on = False

def _ensure_warm_worker():
    global _warm_worker_on
    with _warm_cond:
        if _warm_worker_on:
            return
        _warm_worker_on = True
    threading.Thread(target=_warm_worker, daemon=True).start()

def _warm_worker():
    while True:
        with _warm_cond:
            while not _warm_q:
                _warm_set.clear()
                _warm_cond.wait()
            vid = _warm_q.pop(0)
        try:
            _wait_if_user_busy()
            _prefetch_audio(vid)   # pula sozinho os que já estão no cache
        except Exception:
            pass

# ---------- Cache inteligente (aprende o que você ouve e protege o que você ama) ----------
# plays     → quantas vezes cada faixa tocou (base da eviction esperta)
# next_map  → transições "quem costuma vir depois de quem" (aprendido com suas trocas de faixa)
# sessão    → quanto o cache te poupou de YouTube agora
_last_stream_lock = threading.Lock()
_user_last_stream: dict[int, tuple[str, float]] = {}   # uid -> (video_id, timestamp)
_stats_lock = threading.Lock()
_session_stats = {"cache": 0, "net": 0, "bytes": 0}

def _count_play(video_id: str):
    """Sobe o contador de reproduções (a eviction inteligente usa pra decidir quem fica)."""
    try:
        with db() as con:
            # upsert atômico: duas abas tocando juntas não perdem incremento
            con.execute("""INSERT INTO plays(video_id, n, last_at) VALUES (?,1,CURRENT_TIMESTAMP)
                           ON CONFLICT(video_id) DO UPDATE SET n = n + 1, last_at = CURRENT_TIMESTAMP""",
                        (video_id,))
    except Exception:
        pass

def _bump_next(prev_id: str, next_id: str, user_id: int = 1, title: str = "", artist: str = "", thumbnail: str = ""):
    """Anota a transição prev→next isolada por usuário, persistindo metadados duradouros."""
    try:
        with db() as con:
            if not title:
                h = con.execute("SELECT title, artist, thumbnail FROM history WHERE user_id=? AND video_id=? LIMIT 1",
                                (user_id, next_id)).fetchone()
                if h:
                    title, artist, thumbnail = h["title"] or "", h["artist"] or "", h["thumbnail"] or ""
            con.execute("""INSERT INTO next_map(user_id, prev_id, next_id, title, artist, thumbnail, n, updated_at)
                           VALUES (?,?,?,?,?,?,1,CURRENT_TIMESTAMP)
                           ON CONFLICT(user_id, prev_id, next_id) DO UPDATE SET
                               n = n + 1,
                               title = COALESCE(NULLIF(excluded.title, ''), next_map.title),
                               artist = COALESCE(NULLIF(excluded.artist, ''), next_map.artist),
                               thumbnail = COALESCE(NULLIF(excluded.thumbnail, ''), next_map.thumbnail),
                               updated_at = CURRENT_TIMESTAMP""",
                        (user_id, prev_id, next_id, title, artist, thumbnail))
    except Exception:
        pass

def _smart_warm_after(video_id: str, user_id: int = 1):
    """Pré-guarda no cache o que costuma vir DEPOIS desta faixa — priorizando o perfil do usuário."""
    if not SETTINGS.get("learn_next", True) or not SETTINGS.get("cache_enabled") \
            or not SETTINGS.get("prefetch_next", True):
        return
    try:
        with db() as con:
            rows = con.execute("SELECT next_id FROM next_map WHERE user_id=? AND prev_id=? ORDER BY n DESC LIMIT 6",
                               (user_id, video_id)).fetchall()
            if not rows:
                rows = con.execute("SELECT next_id FROM next_map WHERE prev_id=? ORDER BY n DESC LIMIT 6",
                                   (video_id,)).fetchall()
    except Exception:
        return
    picked = 0
    for r in rows:
        if picked >= 2:
            break
        nid = r["next_id"]
        if not VID_RE.match(nid or "") or nid == video_id or _cache_lookup(nid):
            continue
        with _warm_lock:
            if nid in _warm_set:
                continue
            _warm_set.add(nid)
            _warm_q.append(nid)
        picked += 1
    if picked:
        _ensure_warm_worker()
        print(f"🧠 cache inteligente: depois de {video_id} você costuma ouvir {picked} faixa(s) — já tô guardando")

def _session_serve(kind: str, nbytes: int = 0):
    """Conta quem foi servido por onde ('cache' = disco local, 'net' = YouTube) + bytes poupados."""
    with _stats_lock:
        _session_stats[kind] = _session_stats.get(kind, 0) + 1
        if kind != "net":
            _session_stats["bytes"] += max(0, nbytes)

def open_upstream(video_id: str, range_header, force: bool = False,
                  avoid=(), ignore_cooldown: bool = False, urgent: bool = False,
                  data: dict | None = None, alt_url: str | None = None):
    """data/alt_url: reutiliza uma extração já feita (mesmo client) e/ou busca um
    formato alternativo dela — evita nova extração só pra trocar de itag."""
    if data is None:
        data = get_stream_data(video_id, force=force, avoid=avoid,
                               ignore_cooldown=ignore_cooldown, urgent=urgent)
    headers = {"User-Agent": UA, **(data.get("headers") or {})}
    if range_header:
        headers["Range"] = range_header
    target = alt_url or data["url"]
    proxies = {"https": YT_PROXY} if YT_PROXY else None
    resp = session.get(target, headers=headers, stream=True,
                       timeout=(5, 30), proxies=proxies)
    # Alguns streams do googlevideo (ex.: TVHTML5 itag=140) rejeitam "Range: bytes=0-"
    # com 403 mas entregam 200 OK sem Range — satisfaz igualmente pedidos do byte 0.
    if resp.status_code == 403 and range_header and re.match(r"^bytes=0-$", range_header.strip(), re.I):
        resp.close()
        headers.pop("Range", None)
        resp = session.get(target, headers=headers, stream=True,
                           timeout=(5, 30), proxies=proxies)
    return resp, data

def _friendly_error(e: Exception) -> str:
    s = str(e)
    low = s.lower()
    extra = ""
    if "requested format" in low or "reloaded" in low:
        extra = ("\n\n💡 O YouTube mudou algo do lado dele e o yt-dlp precisa de uma versão mais nova.\n"
                 "    Resolva com:  pip install -U yt-dlp\n"
                 "    (ou inicie o app com AUTO_UPDATE_YTDLP=1 para atualizar sozinho)")
    elif "sign in to confirm" in low or "bot" in low:
        extra = ("\n\n💡 O YouTube está desconfiando do seu IP. Opções:\n"
                 "    1) USE_COOKIES=1 com um cookies.txt exportado do seu navegador\n"
                 "    2) YT_PROXY=http://host:porta para sair por outro IP")
    return (s + extra)[:600]

# ---------- Busca ----------
SEARCH_LIMIT = 10
SEARCH_TIMEOUT = 10    # era 20: YTMusic travando custava 20s ANTES do plano B
SEARCH_TTL = 600
_mem_cache_lock = threading.Lock()
_search_cache: dict[str, tuple[float, list]] = {}
_search_pool = cf.ThreadPoolExecutor(max_workers=4)   # separado da extração
_ytm_down_until = 0.0   # circuit breaker: YT Music falhando → yt-dlp direto por 5 min

def _do_search(query: str, limit: int):
    # ignore_spelling=False tolera erros de digitação (ex.: "2zdiniz")
    return yt.search(query, filter="songs", limit=limit, ignore_spelling=False)

def _ytdlp_search(query: str, limit: int) -> list:
    """Busca de emergência via yt-dlp caso a API do YouTube Music falhe/esteja bloqueada."""
    opts = _base_ydl_opts()
    opts["extract_flat"] = "in_playlist"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max(limit, 2)}:{query}", download=False)
        out = []
        for e in (info or {}).get("entries") or []:
            vid = e.get("id")
            if not vid:
                continue
            dur = e.get("duration")
            dur_s = f"{int(dur // 60)}:{int(dur % 60):02d}" if isinstance(dur, (int, float)) and dur else ""
            thumbs = e.get("thumbnails") or []
            thumb = (thumbs[-1].get("url") if thumbs else "") or e.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
            out.append({
                "videoId": vid,
                "title": e.get("title") or "Sem título",
                "artist": e.get("uploader") or e.get("channel") or "YouTube",
                "channelId": e.get("channel_id") or "",
                "thumbnail": f"/api/thumb/{vid}",
                "duration": dur_s,
            })
        return out[:limit]
    except Exception as e:
        print(f"Erro na busca (yt-dlp): {e}")
        return []

def search_songs(query: str, limit: int = SEARCH_LIMIT):
    global _ytm_down_until, yt
    limit = max(1, min(limit, SEARCH_LIMIT))   # teto duro: 10 resultados, sempre
    key = f"{query.lower().strip()}|{limit}"
    with _mem_cache_lock:
        hit = _search_cache.get(key)
        if hit and time.time() - hit[0] < SEARCH_TTL:
            return hit[1]
    results = None
    if time.time() >= _ytm_down_until:
        try:
            results = _search_pool.submit(_do_search, query, limit).result(timeout=SEARCH_TIMEOUT)
        except Exception as e:
            msg = str(e) or "(sem mensagem)"
            print(f"⚠️ Busca via YouTube Music falhou ({type(e).__name__}: {msg[:120]}); tentando yt-dlp...")
            # circuit breaker: se o YT Music está quebrado, não paga o timeout
            # de novo a cada busca — 5 min direto no plano B (yt-dlp)
            _ytm_down_until = time.time() + 300
            try:
                yt = _new_yt()   # sessão interna pode ter envenenado: instancia nova
            except Exception:
                pass
    if results is None:
        results = _ytdlp_search(query, limit)
    results = results[:limit]   # teto duro: nunca mais do que o pedido (10)
    out = []
    for r in results:
        if not r.get("videoId"):
            continue
        artists = ", ".join(a["name"] for a in r.get("artists", [])) if r.get("artists") else "Desconhecido"
        # YT Music manda o browseId do artista em artists[].id — só serve se for canal (UC…)
        ch_id = next((a.get("id") for a in r.get("artists") or []
                      if isinstance(a, dict) and str(a.get("id") or "").startswith("UC")), "")
        out.append({
            "videoId": r["videoId"], "title": r.get("title"), "artist": artists,
            "channelId": ch_id,
            "thumbnail": f"/api/thumb/{r['videoId']}", "duration": r.get("duration"),
        })
    with _mem_cache_lock:
        _search_cache[key] = (time.time(), out)
        if len(_search_cache) > 200:   # nunca era limpo: crescia sem limite na sessão longa
            for k, _ in sorted(_search_cache.items(), key=lambda kv: kv[1][0])[:100]:
                _search_cache.pop(k, None)
    return out

# ---------- Sugestões de busca (mesma engine de autocomplete do YouTube) ----------
_sugg_cache: dict[str, tuple[float, list]] = {}

@app.get("/api/suggest")
def suggest(q: str = Query(..., min_length=2)):
    q = q.strip()[:80]
    if len(q) < 2:
        return {"items": []}
    key = q.lower()
    with _mem_cache_lock:
        hit = _sugg_cache.get(key)
        if hit and time.time() - hit[0] < 3600:
            return {"items": hit[1]}
    items: list = []
    try:
        r = session.get("https://suggestqueries.google.com/complete/search",
                        params={"client": "firefox", "hl": "pt-BR", "ds": "yt", "q": q},
                        headers={"User-Agent": UA}, timeout=5)
        data = r.json()
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
            items = [s for s in data[1] if isinstance(s, str)][:8]
    except Exception:
        pass
    with _mem_cache_lock:
        _sugg_cache[key] = (time.time(), items)
        if len(_sugg_cache) > 300:
            _sugg_cache.clear()
    return {"items": items}

# ---------- Rádio (mix estilo "Start radio" do Metrolist: RD{video_id}) ----------
_radio_cache: dict[str, tuple[float, list]] = {}

def _radio_fetch(video_id: str) -> list:
    # 1º: YT Music (get_watch_playlist): UMA chamada de API (~0.5s) e imune à
    # flakiness da página web do mix — o yt-dlp na URL RD às vezes devolve
    # 0 entradas SEM erro (mix intermitente do YouTube; reproduzido em teste:
    # 26 → 0 → 25 faixas no mesmo minuto). Era a causa da estação vazia.
    try:
        r = yt.get_watch_playlist(video_id, radio=True, limit=26)
        out = []
        for t in (r.get("tracks") or []):
            vid = t.get("videoId")
            if not vid or vid == video_id:      # pula a própria semente
                continue
            d = t.get("duration_seconds")
            dur_s = f"{int(d // 60)}:{int(d % 60):02d}" if isinstance(d, (int, float)) and d else (t.get("duration") or "")
            art = t.get("artist")
            art = art.get("name") if isinstance(art, dict) else (art or "YouTube")
            out.append({"videoId": vid, "title": t.get("title") or "Sem título",
                        "artist": art, "thumbnail": f"/api/thumb/{vid}", "duration": dur_s})
            if len(out) >= 25:
                break
        if out:
            return out
    except Exception as e:
        print(f"⚠️ rádio via YT Music falhou ({str(e)[:100]}); caindo pro yt-dlp…")
    # 2º (fallback): página do mix RD via yt-dlp, como antes
    opts = _base_ydl_opts()
    opts.update({"noplaylist": False, "extract_flat": "in_playlist", "playlistend": 26})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}",
                                download=False)
    out = []
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        vid = e.get("id")
        if not vid or vid == video_id:      # pula a própria semente
            continue
        dur = e.get("duration")
        dur_s = f"{int(dur // 60)}:{int(dur % 60):02d}" if isinstance(dur, (int, float)) and dur else ""
        thumbs = e.get("thumbnails") or []
        thumb = (thumbs[-1].get("url") if thumbs else "") or e.get("thumbnail") \
                or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
        out.append({"videoId": vid, "title": e.get("title") or "Sem título",
                    "artist": e.get("uploader") or e.get("channel") or "YouTube",
                    "thumbnail": f"/api/thumb/{vid}", "duration": dur_s})
        if len(out) >= 25:
            break
    if not out:
        raise RuntimeError("mix de rádio veio vazio (YouTube intermitente) — tente de novo")
    return out

def _known_blend(tracks: list, seed_id: str, user_id: int = 1, k: int = 4) -> list:
    """Mistura até k faixas que ESTE usuário já conhece (favoritos; se faltar, histórico)."""
    try:
        with db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT video_id, title, artist, thumbnail FROM favorites WHERE user_id=? ORDER BY RANDOM() LIMIT ?",
                (user_id, k))]
            if len(rows) < k:
                got = {r["video_id"] for r in rows} | {seed_id}
                for r in con.execute(
                    "SELECT video_id, title, artist, thumbnail FROM history WHERE user_id=? ORDER BY RANDOM() LIMIT ?",
                    (user_id, k * 3),
                ):
                    if r["video_id"] not in got:
                        rows.append(dict(r))
                        got.add(r["video_id"])
                    if len(rows) >= k:
                        break
    except Exception:
        return tracks
    have = {t.get("videoId") for t in tracks} | {seed_id}
    out = list(tracks)
    for r in rows:
        vid = r.get("video_id") or ""
        if not VID_RE.match(vid) or vid in have:
            continue
        have.add(vid)
        out.insert(random.randint(0, len(out)),
                   {"videoId": vid, "title": r.get("title") or "", "artist": r.get("artist") or "",
                    "thumbnail": r.get("thumbnail") or "", "duration": "", "known": True})
    return out

@app.get("/api/radio/{video_id}")
def radio(video_id: str, request: Request, known: int = 0):
    """Mix RD da semente. Com ?known=1 mistura faixas do usuário sem vazar entre perfis."""
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    u = _current_user(request)
    known = bool(known)
    with _mem_cache_lock:
        hit = _radio_cache.get(video_id)
    if hit and time.time() - hit[0] < 6 * 3600:
        tracks = _known_blend(hit[1], video_id, user_id=u["id"]) if known else list(hit[1])
        return {"id": f"RD{video_id}", "title": "Rádio", "tracks": tracks, "cached": True}
    try:
        raw_tracks = _pl_pool.submit(_radio_fetch, video_id).result(timeout=75)
    except Exception as e:
        raise HTTPException(502, _friendly_error(e))
    with _mem_cache_lock:
        _radio_cache[video_id] = (time.time(), raw_tracks)
        if len(_radio_cache) > 100:
            for k_old, _ in sorted(_radio_cache.items(), key=lambda kv: kv[1][0])[:50]:
                _radio_cache.pop(k_old, None)
    tracks = _known_blend(raw_tracks, video_id, user_id=u["id"]) if known else list(raw_tracks)
    return {"id": f"RD{video_id}", "title": "Rádio", "tracks": tracks, "cached": False}

def row(r): return {k: r[k] for k in r.keys()} if r else None

def track_row(r):
    """Converte linha do banco (video_id) para o formato do frontend (videoId).
    Corrige o bug em que favoritos/histórico/playlists não tocavam."""
    d = row(r)
    if d and "video_id" in d:
        d["videoId"] = d.pop("video_id")
    return d

# ---------- PWA: ícone, manifest (o navegador pede sozinho; sem isso a aba fica "genérica") ----------
# Auto-reparo (restaurado da build 2026-10-20.2 pelo fix glm): os assets ficam
# EMBUTIDOS em base64 e _ensure_assets() recria os arquivos se faltarem —
# servindo só por arquivo (como o zcode deixou), ícone apagado virava 404 eterno.
import base64 as _b64

def _ensure_assets():
    for _name, _b64s in _EMBEDDED_ASSETS.items():
        try:
            _p = BASE / _name
            if not _p.is_file() or _p.stat().st_size == 0:
                _p.write_bytes(_b64.b64decode(_b64s))
        except Exception:
            pass

_ensure_assets()

def _icon_response(size: int, filename: str):
    _ensure_assets()
    f = BASE / filename
    if f.is_file():
        return FileResponse(str(f), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
    b64s = _EMBEDDED_ASSETS.get(filename)
    if b64s:
        return Response(content=_b64.b64decode(b64s), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(404, "ícone não encontrado")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return _icon_response(192, "icon-192.png")

@app.get("/icon-192.png", include_in_schema=False)
def icon_192(): return _icon_response(192, "icon-192.png")

@app.get("/icon-512.png", include_in_schema=False)
def icon_512(): return _icon_response(512, "icon-512.png")

@app.get("/icon-maskable-512.png", include_in_schema=False)
def icon_maskable(): return _icon_response(512, "icon-maskable-512.png")

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest(request: Request):
    _ensure_assets()
    f = BASE / "manifest.webmanifest"
    try:
        raw = f.read_text(encoding="utf-8") if f.is_file() else _b64.b64decode(_EMBEDDED_ASSETS["manifest.webmanifest"]).decode("utf-8")
        data = json.loads(raw)
        sk_tok = request.query_params.get("sk") or request.cookies.get("sk") or ""
        key = _pair_key()
        if sk_tok:
            if _safe_eq(sk_tok, key):
                data["start_url"] = f"/?sk={urllib.parse.quote(key, safe='-_.~')}"
            else:
                matched = _get_user_by_token(sk_tok)
                if matched:
                    tok_q = urllib.parse.quote(matched["token"], safe="-_.~")
                    data["start_url"] = f"/?sk={tok_q}"
        return Response(content=json.dumps(data, ensure_ascii=False),
                        media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})
    except Exception:
        fallback = f.read_bytes() if f.is_file() else _b64.b64decode(_EMBEDDED_ASSETS["manifest.webmanifest"])
        return Response(content=fallback, media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=3600"})

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    f = BASE / "static" / "sw.js"
    if not f.is_file():
        raise HTTPException(404, "sw.js não encontrado")
    return FileResponse(str(f), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

@app.get("/static/app.css", include_in_schema=False)
def static_app_css():
    f = BASE / "static" / "app.css"
    if not f.is_file():
        raise HTTPException(404, "app.css não encontrado")
    return FileResponse(str(f), media_type="text/css; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})

@app.get("/static/app.js", include_in_schema=False)
def static_app_js():
    f = BASE / "static" / "app.js"
    if not f.is_file():
        raise HTTPException(404, "app.js não encontrado")
    return FileResponse(str(f), media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})

# ---------- Letras multi-fonte (LRCLIB sincronizada + YouTube Music) ----------
# 1º LRCLIB (lrclib.net — gratuito, sem chave): letra SIMPLES + SINCRONIZADA (LRC)
# 2º YouTube Music via ytmusicapi (letra simples) — funciona mesmo sem login
_lyr_pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="lyrics")
_lyr_lock = threading.Lock()
_lyr_cache = {}          # video_id -> dict|None (None = confirmado sem letras)
_lyr_inflight = {}       # video_id -> Future (single-flight)
_lyr_anon = None         # YTMusic anônimo preguiçoso (só criado se o principal falhar)
_lyr_anon_lock = threading.Lock()

LRCLIB_UA = "StremMusic/1.0 (player web pessoal)"   # LRCLIB pede identificação do client
_JUNK_RE = re.compile(
    r"\((?:[^)]*?(?:official|video|audio|lyrics?|visualizer|hd|hq|4k)[^)]*)\)"
    r"|\[[^\]]*\]"
    r"|\((?:feat|ft|prod)\.?\s[^)]*\)", re.I)

def _clean_title_for_lyrics(t: str) -> str:
    """'Musica (Official Video) [HD]' → 'Musica' — busca no LRCLIB fica muito melhor."""
    t = _JUNK_RE.sub(" ", t or "")
    return re.sub(r"\s+", " ", t).strip(" -–—|")

def _anon_lazy():
    global _lyr_anon
    with _lyr_anon_lock:
        if _lyr_anon is None:
            _lyr_anon = _new_yt()
        return _lyr_anon

def _lrclib_fetch(title: str, artist: str, duration):
    """Procura no LRCLIB. Devolve (plain, synced) ou None.
    A duração desempata: recorde com ±2s do áudio ganha bônus grande."""
    if not title:
        return None
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    try:
        r = requests.get("https://lrclib.net/api/search", params=params,
                         headers={"User-Agent": LRCLIB_UA}, timeout=8)
        if r.status_code != 200:
            return None
        recs = r.json() or []
    except Exception:
        return None
    if not recs:
        return None
    def score(rec):
        s = 0.0
        if rec.get("syncedLyrics"):
            s += 2.0
        if rec.get("plainLyrics"):
            s += 0.5
        try:
            if duration and rec.get("duration"):
                d = abs(float(rec["duration"]) - float(duration))
                if d < 2:
                    s += 3.0
                elif d < 6:
                    s += 1.0
                elif d > 25:
                    s -= 2.0
        except Exception:
            pass
        return s
    recs.sort(key=score, reverse=True)
    best = recs[0]
    if score(best) <= 0:
        return None
    return ((best.get("plainLyrics") or "").strip() or None,
            (best.get("syncedLyrics") or "").strip() or None)

def _ytm_lyrics_fetch(video_id: str):
    """get_watch_playlist devolve o browseId da letra; get_lyrics devolve o texto."""
    def _try(inst):
        try:
            watch = inst.get_watch_playlist(videoId=video_id)
            bid = (watch or {}).get("lyrics")
            if not bid:
                return None
            d = inst.get_lyrics(bid) or {}
            return (d.get("lyrics") or "").strip() or None
        except Exception:
            return None
    for inst in (yt, _anon_lazy()):   # principal e, na falha, anônima compartilhada
        txt = _try(inst)
        if txt:
            return txt
    return None

def _lyrics_fetch(video_id: str, title: str = "", artist: str = "", duration=None):
    """Multi-fonte: LRCLIB (com LRC sincronizada) → YouTube Music.
    Devolve {source, lyrics, synced} ou None."""
    title = _clean_title_for_lyrics(title)
    artist = (artist or "").split(",")[0].strip()   # "A, B" → só o artista principal
    if title:
        got = _lrclib_fetch(title, artist, duration)
        if got and (got[0] or got[1]):
            plain, synced = got
            return {"source": "LRCLIB", "lyrics": plain or synced, "synced": synced or None}
    txt = _ytm_lyrics_fetch(video_id)
    if txt:
        return {"source": "YouTube Music", "lyrics": txt, "synced": None}
    return None

@app.get("/api/lyrics/{video_id}")
def lyrics(video_id: str, title: str = "", artist: str = "", duration: float = 0):
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    # sem título enviado pelo frontend? tenta o meta guardado pelo stream/prefetch
    if not title:
        m = _cache_lookup(video_id)
        if m:
            title, artist = m.get("title") or "", m.get("artist") or ""
    with _lyr_lock:
        if video_id in _lyr_cache:
            cached = _lyr_cache[video_id]
            if cached is None:
                raise HTTPException(404, "Sem letras disponíveis para esta faixa.")
            return {**cached, "cached": True}
        fut = _lyr_inflight.get(video_id)
        if fut is None:
            fut = _lyr_pool.submit(_lyrics_fetch, video_id, title, artist, duration or None)
            _lyr_inflight[video_id] = fut
    timed_out = False
    try:
        res = fut.result(timeout=45)
    except Exception:
        res = None
        timed_out = True   # timeout/rede: não grava "sem letras" — a busca pode dar certo depois
    finally:
        with _lyr_lock:
            _lyr_inflight.pop(video_id, None)
            if len(_lyr_cache) > 200:
                # descarta as mais antigas por inserção (dict preserva ordem)
                for k_old in list(_lyr_cache)[:100]:
                    _lyr_cache.pop(k_old, None)
            if not timed_out:
                _lyr_cache[video_id] = res
    if res is None:
        raise HTTPException(404, "Sem letras disponíveis para esta faixa.")
    return {**res, "cached": False}

@app.get("/", response_class=HTMLResponse)
def index():
    for candidate in [BASE / "static" / "index.html", BASE / "index.html"]:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise HTTPException(500, "index.html não encontrado.")

@app.get("/api/search")
def search(request: Request, q: str = Query(..., min_length=1), limit: int = Query(SEARCH_LIMIT, ge=1, le=SEARCH_LIMIT)):
    _check_rate_limit(request, "search")
    results = search_songs(q, limit)
    if results:
        # pré-aquece as 3 primeiras: clicar em qualquer uma delas toca quase na hora
        prewarm([r["videoId"] for r in results[:3]])
    return results

# ---------- Página do artista ----------
# O "artista" aqui é o canal do YouTube: puxa os uploads do canal via yt-dlp
# (modo flat, barato) e ordena por views — as mais tocadas primeiro. Sem canal
# conhecido, cai num ytsearch pelo nome.
ARTIST_LIMIT = 30
ARTIST_TTL = 1800
_artist_cache: dict[str, tuple[float, dict]] = {}
_artist_lock = threading.Lock()

def _flat_track(e) -> dict | None:
    vid = (e or {}).get("id")
    if not vid or not VID_RE.match(vid):
        return None
    dur = e.get("duration")
    dur_s = f"{int(dur // 60)}:{int(dur % 60):02d}" if isinstance(dur, (int, float)) and dur else ""
    return {"videoId": vid, "title": e.get("title") or "Sem título",
            "artist": e.get("uploader") or e.get("channel") or "YouTube",
            "channelId": e.get("channel_id") or "",
            "thumbnail": f"/api/thumb/{vid}", "duration": dur_s}

@app.get("/api/artist")
def artist_tracks(request: Request, id: str = "", name: str = ""):
    _check_rate_limit(request, "search")
    name = (name or "").strip()
    aid = (id or "").strip()
    if aid and not aid.startswith("UC"):
        aid = ""   # browseId do YT Music não vira URL de canal
    if not aid and not name:
        raise HTTPException(400, "informe o id ou o nome do artista")
    key = aid or ("n:" + name.lower())
    with _artist_lock:
        hit = _artist_cache.get(key)
        if hit and time.time() - hit[0] < ARTIST_TTL:
            return {**hit[1], "cached": True}
    if aid:
        disp_name = name or aid
    else:
        disp_name = name
    def _fetch(url: str, pl_end: int):
        opts = _base_ydl_opts()
        opts["extract_flat"] = "in_playlist"
        opts["playlistend"] = pl_end
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    info = None
    if aid:
        try:
            info = _search_pool.submit(_fetch, f"https://www.youtube.com/channel/{aid}/videos", 60).result(timeout=60)
        except Exception:
            info = None   # canal de artista do YT Music não tem aba "Videos" — cai na busca
    if not info or not (info.get("entries") or []):
        if not name:
            raise HTTPException(502, "não consegui puxar as músicas deste canal e não sei o nome do artista")
        try:
            info = _search_pool.submit(_fetch, f"ytsearch{ARTIST_LIMIT}:{name}", ARTIST_LIMIT).result(timeout=60)
        except Exception as e:
            raise HTTPException(502, _friendly_error(e))
    seen, tracks = set(), []
    for e in (info or {}).get("entries") or []:
        t = _flat_track(e)
        if not t or t["videoId"] in seen:
            continue
        seen.add(t["videoId"])
        tracks.append(t)
    if aid:
        # canal: uploads vêm por data — guarda as views do entry e reordena
        # pelas mais tocadas (as principais do artista primeiro)
        views = {e.get("id"): (e.get("view_count") or 0) for e in (info or {}).get("entries") or []}
        tracks.sort(key=lambda t: views.get(t["videoId"] or "", 0), reverse=True)
    tracks = tracks[:ARTIST_LIMIT]
    payload = {"name": disp_name, "channelId": aid, "tracks": tracks}
    with _artist_lock:
        _artist_cache[key] = (time.time(), payload)
        if len(_artist_cache) > 50:
            for k_old, _ in sorted(_artist_cache.items(), key=lambda kv: kv[1][0])[:25]:
                _artist_cache.pop(k_old, None)
    return payload

@app.get("/api/resolve/{video_id}")
def resolve(video_id: str, request: Request, title: str = "", artist: str = ""):
    _check_rate_limit(request, "stream")
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    try:
        data = get_stream_data(video_id)
    except Exception as e:
        # Pré-aquecimento da próxima faixa: falhar aqui NÃO é erro do usuário —
        # devolve 200 com url nula em vez de 502 (log limpo; o play real, no
        # /api/stream, resolve na hora porque lá urgent=True ignora cooldown).
        print(f"ℹ️ pré-aquecimento de {video_id} adiado: {str(e)[:120]}")
        return {"url": None, "cached": False}
    hit = _cache_lookup(video_id)
    if SETTINGS.get("cache_enabled") and SETTINGS.get("prefetch_next", True):
        if hit:
            # já está em cache: só anexa título/artista se o meta ainda não tem
            if (title or artist) and not (hit.get("title") or hit.get("artist")):
                hit["title"], hit["artist"], hit["at"] = title, artist, time.time()
                try:
                    _cache_write_meta(video_id, hit)
                except Exception:
                    pass
        else:
            threading.Thread(target=_prefetch_audio, args=(video_id, title, artist),
                             daemon=True).start()
    return {"url": data["url"], "cached": hit is not None}

def _wanted_scope(fn):
    """Marca o vídeo como 'pedido ativo' durante a requisição de stream.
    Multi-aparelho: cada request protege o seu próprio vídeo; a extração
    urgent só é cancelada quando NINGUÉM está mais pedindo aquele vídeo."""
    def wrapper(video_id: str, request: Request):
        _want(video_id)
        try:
            return fn(video_id, request)
        finally:
            _unwant(video_id)
    return wrapper

@app.get("/api/stream/{video_id}")
@_wanted_scope
def stream(video_id: str, request: Request):
    _check_rate_limit(request, "stream")
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    rng = request.headers.get("range")
    retry = bool(request.query_params.get("r"))   # retry do frontend: ignora cooldown
    # Cache inteligente: anota a transição "o que costuma ouvir depois desta" (o play
    # em si é contado só nas saídas bem-sucedidas, pra retry de erro não inflar)
    first_try = not retry
    u = _current_user(request)
    uid = int(u["id"]) if u.get("id") is not None else 1
    if first_try:
        now = time.time()
        with _last_stream_lock:
            prev_entry = _user_last_stream.get(uid)
            _user_last_stream[uid] = (video_id, now)
        if prev_entry:
            prev_id, prev_ts = prev_entry
            diff = now - prev_ts
            # Skip-guard: só grava se a faixa anterior tocou por pelo menos 10s e não mais de 20 min (1200s)
            if 10 <= diff <= 1200 and prev_id != video_id and VID_RE.match(prev_id):
                _bump_next(prev_id, video_id, user_id=uid)
    # 1) música JÁ BAIXADA? toca direto do disco — zero YouTube, zero cache (Koel-style)
    if not retry:
        dl = _serve_from_download(video_id, rng)
        if dl is not None:
            print(f"📥 {video_id} servido do download local (sem tocar no YouTube)")
            if first_try:
                _count_play(video_id)
            _session_serve("cache", int(dl.headers.get("Content-Length") or 0))
            return dl
    # 2) cache local: arquivo completo em disco → resposta instantânea, sem depender do YouTube
    if not retry:
        cached = _serve_from_cache(video_id, rng)
        if cached is not None:
            print(f"💾 {video_id} servido do cache local")
            if first_try:
                _count_play(video_id)
            _session_serve("cache", int(cached.headers.get("Content-Length") or 0))
            return cached
    tried = []
    tried_set = set()
    def _note_fail(d, st):
        # marca o client como ruim pra este vídeo + tira a URL podre do cache
        c = (d or {}).get("client", "")
        if c:
            tried_set.add(c)
            if c not in tried:
                tried.append(c)
        _mark_bad_client(video_id, c)
        if st in (403, 410, 429) and c:
            _client_mark_fail(c)
            with _health_lock:
                _CLIENT_MS.pop(c, None)
                if _last_ok.get("label") == c:
                    _last_ok["label"] = None
        with _cache_lock:
            cur = _stream_cache.get(video_id)
            if cur is not None and cur[1] is d:
                _stream_cache.pop(video_id, None)

    def _try_alts(u, d, max_alts=2):
        for alt in list((d.get("alts") or []))[:max_alts]:
            if u.status_code < 400:
                break
            st = u.status_code
            u.close()
            print(f"⚠️ HTTP {st} em {video_id} (client {d.get('client')}); tentando formato alternativo do mesmo client...")
            u, d = open_upstream(video_id, rng, data=d, alt_url=alt)
        return u, d

    try:
        # No retry (?r= do frontend) evita DE CARA os clients que acabaram de
        # devolver 403 neste vídeo: sem isso o retry repetia o MESMO client
        # queimado (extração inteira de novo + 403 de novo = ~17s perdidos).
        upstream, data = open_upstream(video_id, rng, ignore_cooldown=retry, urgent=True,
                                       avoid=_bad_clients_for(video_id) if retry else ())

        # Escada de contorno pós-403:
        # 1) formato ALTERNATIVO da MESMA extração — o SABR às vezes trava só um itag
        upstream, data = _try_alts(upstream, data, max_alts=2)

        # 2) troca de client percorrendo TODOS os clients disponíveis (não só 1 troca)
        max_swaps = max(1, len(_clients_full()))
        for _ in range(max_swaps):
            if upstream.status_code < 400:
                break
            st = upstream.status_code
            upstream.close()
            _note_fail(data, st)
            avoid_now = tried_set | _bad_clients_for(video_id)
            if len(avoid_now) >= len(_clients_full()):
                break
            print(f"⚠️ HTTP {st} no stream de {video_id} (client {data.get('client')}), trocando de client...")
            upstream, data = open_upstream(video_id, rng, force=True,
                                           avoid=avoid_now, urgent=True)
            upstream, data = _try_alts(upstream, data, max_alts=1)

        # 3) última cartada no servidor: o 403 do googlevideo costuma ser transitório —
        #    pausa curta e extração novinha sem evitar ninguém
        if upstream.status_code >= 400:
            st = upstream.status_code
            upstream.close()
            _note_fail(data, st)
            print(f"⚠️ HTTP {st} de novo em {video_id} (client {data.get('client')}); pausa de 2s e última tentativa...")
            time.sleep(2.0)
            upstream, data = open_upstream(video_id, rng, force=True,
                                           ignore_cooldown=True, urgent=True)
    except Exception as e:
        raise HTTPException(502, _friendly_error(e))

    if upstream.status_code >= 400:
        st = upstream.status_code
        url = (data or {}).get("url") or ""
        upstream.close()
        _note_fail(data, st)
        # 4) último recurso: devolve 302 e deixa o NAVEGADOR buscar direto no
        #    googlevideo — a impressão digital TLS/headers do navegador às vezes
        #    passa onde o requests do Python leva 403. Pior cenário: o <audio>
        #    dispara 'error' e o app pula pra próxima faixa (nunca fica mudo).
        if st in (403, 429) and "googlevideo.com" in url:
            print(f"↪️ {video_id}: 302 direto ao googlevideo como último recurso (navegador tenta)")
            if first_try:
                _count_play(video_id)
                _smart_warm_after(video_id, user_id=uid)
            return RedirectResponse(url, status_code=302)
        extra = ("\n\n💡 O YouTube recusou o áudio nos clients disponíveis.\n"
                 "    Se persistir: USE_COOKIES=1 com um cookies.txt do navegador,"
                 " ou rode `pip install -U yt-dlp`.") if st in (403, 410) else ""
        raise HTTPException(502, f"Upstream HTTP {st} (clients tentados: {', '.join(tried) or (data or {}).get('client')}).{extra}")

    _record_cdn_ok((data or {}).get("client", ""))

    passthrough = {}
    for h in ("Content-Type", "Content-Range", "Accept-Ranges"):
        v = upstream.headers.get(h)
        if v:
            passthrough[h] = v
    # Content-Length só faz sentido se a resposta não vier comprimida
    # (o requests descomprime transparentemente, o que desalinha o tamanho)
    if not upstream.headers.get("Content-Encoding"):
        cl = upstream.headers.get("Content-Length")
        if cl:
            passthrough["Content-Length"] = cl
    passthrough.setdefault("Content-Type", "audio/mp4")
    passthrough.setdefault("Accept-Ranges", "bytes")

    plan = _cache_plan(upstream, rng)   # resposta integral? guarda no cache em paralelo
    if plan:
        expected, ct = plan
        tmp = CACHE_DIR / f"{video_id}.{uuid.uuid4().hex[:8]}.part"

        def iterate():
            n = 0
            try:
                with open(tmp, "wb") as f:
                    for chunk in upstream.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            f.write(chunk)
                            n += len(chunk)
                            yield chunk
            finally:
                upstream.close()
                if n == expected and _cache_promote(video_id, tmp, n, ct,
                                                    client=data.get("client", "")):
                    print(f"💾 {video_id} guardado no cache ({n / 1048576:.1f} MB)")
                else:
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
    else:
        def iterate():
            try:
                for chunk in upstream.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

    if first_try:
        _count_play(video_id)
    _session_serve("net", int(passthrough.get("Content-Length") or 0))
    if first_try:
        _smart_warm_after(video_id, user_id=uid)
    return StreamingResponse(iterate(), status_code=upstream.status_code, headers=passthrough)

# ---------- Configurações (painel Config) ----------
@app.get("/api/settings")
def get_settings(request: Request):
    u = _current_user(request)
    if not u.get("is_admin"):
        return {"settings": {"learn_next": bool(SETTINGS.get("learn_next", True))}}
    pub_settings = dict(SETTINGS)
    pub_settings.pop("pair_key", None)
    return {
        "settings": pub_settings,
        "cache_dir": str(CACHE_DIR),
        "ytdlp_version": getattr(yt_dlp.version, "__version__", "?"),
    }

def _apply_settings(data: dict) -> list:
    errs = []
    def as_bool(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "sim")
        return None
    def as_int(v):
        try:
            return int(v)
        except Exception:
            return None
    for key in ("cache_enabled", "prefetch_next", "force_ipv4", "use_cookies",
                "smart_cache", "learn_next", "cloudflare_tunnel"):   # (fix glm: o zcode tinha deixado os
        # interruptores do cache inteligente SEM caminho de escrita — a UI mudava
        # e o app ignorava, porque _cache_evict/learn_next lêm mas ninguém gravava)
        if key in data:
            b = as_bool(data[key])
            if b is None:
                errs.append(f"{key} não é booleano")
            else:
                SETTINGS[key] = b
    if "cache_limit_mb" in data:
        n = as_int(data["cache_limit_mb"])
        if n is None:
            errs.append("cache_limit_mb não é número")
        else:
            SETTINGS["cache_limit_mb"] = max(50, min(10240, n))
    if "race_wave" in data:
        n = as_int(data["race_wave"])
        if n is None:
            errs.append("race_wave não é número")
        else:
            SETTINGS["race_wave"] = max(1, min(4, n))
    if "extract_timeout" in data:
        n = as_int(data["extract_timeout"])
        if n is None:
            errs.append("extract_timeout não é número")
        else:
            SETTINGS["extract_timeout"] = max(10, min(60, n))
    if "mp3_quality" in data:
        q = str(data["mp3_quality"]).strip()
        if q not in ("128", "192", "320"):
            errs.append("mp3_quality deve ser 128, 192 ou 320")
        else:
            SETTINGS["mp3_quality"] = q
    _save_settings()
    _cache_evict()   # se o limite caiu, os mais antigos saem agora
    return errs

@app.post("/api/settings")
def update_settings(data: dict, request: Request):
    _require_admin(request)
    if not isinstance(data, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    errs = _apply_settings(data)
    if errs:
        raise HTTPException(400, "Valores inválidos: " + "; ".join(errs))
    pub_settings = dict(SETTINGS)
    pub_settings.pop("pair_key", None)
    return {"ok": True, "settings": pub_settings}

# ---------- Cache local (painel Config) ----------
@app.get("/api/cache/stats")
def cache_stats(request: Request):
    _require_admin(request)
    items, used = [], 0
    if _use_cache_db():
        with db() as con:
            rows = con.execute(
                "SELECT video_id, title, artist, size, at FROM cache_entries ORDER BY at DESC"
            ).fetchall()
        if not rows and any(CACHE_DIR.glob("*.meta.json")):
            _cache_sync_db()
            with db() as con:
                rows = con.execute(
                    "SELECT video_id, title, artist, size, at FROM cache_entries ORDER BY at DESC"
                ).fetchall()
        stale = []
        for r in rows:
            vid = r["video_id"]
            audio, _ = _cache_paths(vid)
            if not audio.is_file():
                stale.append(vid)
                continue
            size = int(r["size"] or 0) or audio.stat().st_size
            used += size
            at_val = float(r["at"] or 0)
            items.append({
                "video_id": vid,
                "title": r["title"] or "",
                "artist": r["artist"] or "",
                "size_bytes": size,
                "size_mb": round(size / 1048576, 1),
                "at": datetime.datetime.fromtimestamp(at_val).isoformat(timespec="seconds") if at_val else "",
            })
        if stale:
            try:
                with db() as con:
                    con.executemany("DELETE FROM cache_entries WHERE video_id=?", [(v,) for v in stale])
            except Exception:
                pass
    else:
        for meta_p in CACHE_DIR.glob("*.meta.json"):
            vid = meta_p.name[:-len(".meta.json")]
            audio, _ = _cache_paths(vid)
            if not audio.is_file():
                continue
            try:
                m = json.loads(meta_p.read_text(encoding="utf-8"))
                size = audio.stat().st_size
                used += size
                items.append({
                    "video_id": vid,
                    "title": m.get("title") or "",
                    "artist": m.get("artist") or "",
                    "size_bytes": size,
                    "size_mb": round(size / 1048576, 1),
                    "at": (datetime.datetime.fromtimestamp(float(m["at"])).isoformat(timespec="seconds")
                           if m.get("at") else ""),
                })
            except Exception:
                continue
        items.sort(key=lambda x: x["at"], reverse=True)
    # estatísticas de sessão + inteligentes (restauradas — o zcode tinha
    # reconstruído este endpoint sem elas: os dados eram coletados por
    # _session_serve() no stream mas nunca mais expostos pra UI)
    with _stats_lock:
        _sess = dict(_session_stats)
    _tot = _sess.get("cache", 0) + _sess.get("net", 0)
    _hit = round(100 * _sess.get("cache", 0) / _tot, 1) if _tot else 0.0
    try:
        with db() as con:
            _top = [dict(r) for r in con.execute(
                "SELECT video_id, n FROM plays ORDER BY n DESC, last_at DESC LIMIT 8")]
    except Exception:
        _top = []
    return {
        "enabled": bool(SETTINGS.get("cache_enabled")),
        "limit_mb": int(SETTINGS.get("cache_limit_mb") or 512),
        "used_mb": round(used / 1048576, 1),
        "used_bytes": used,
        "count": len(items),
        "items": items[:100],
        "served_cache": _sess.get("cache", 0),
        "served_net": _sess.get("net", 0),
        "saved_mb": round(_sess.get("bytes", 0) / 1048576, 1),
        "hit_rate": _hit,
        "top": _top,
        "smart": bool(SETTINGS.get("smart_cache", True)),
        "learn": bool(SETTINGS.get("learn_next", True)),
    }

def _cache_delete(vid: str) -> bool:
    audio, meta_p = _cache_paths(vid)
    ok = False
    for p in (audio, meta_p):
        if p.is_file():
            try:
                p.unlink()
                ok = True
            except Exception:
                pass
    with _cache_touch_lock:
        _cache_touch_mem.pop(vid, None)
    _cache_db_delete(vid)
    return ok

@app.delete("/api/cache")
def cache_clear(request: Request):
    _require_admin(request)
    n = 0
    with _disk_lock:   # não apagar pela metade enquanto um stream promove o cache
        for p in list(CACHE_DIR.glob("*.meta.json")) + list(CACHE_DIR.glob("*.audio")):
            try:
                if p.is_file():
                    p.unlink()
                    if p.name.endswith(".meta.json"):
                        n += 1
            except Exception:
                pass
        for part in CACHE_DIR.glob("*.part"):
            try:
                part.unlink()
                n += 1
            except Exception:
                pass
    with _cache_touch_lock:
        _cache_touch_mem.clear()
    if _use_cache_db():
        try:
            with db() as con:
                con.execute("DELETE FROM cache_entries")
        except Exception:
            pass
    return {"ok": True, "removed": n}

@app.delete("/api/cache/{video_id}")
def cache_delete_one(video_id: str, request: Request):
    _require_admin(request)
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    return {"ok": _cache_delete(video_id)}

@app.post("/api/cache/warm")
def cache_warm(body: dict):
    """Enfileira faixas para pré-cache EM ORDEM (uma por vez) — leitura sequencial.
    O worker pula o que já está no cache; respeita cache_enabled + prefetch_next."""
    if not SETTINGS.get("cache_enabled") or not SETTINGS.get("prefetch_next", True):
        return {"ok": True, "queued": 0, "reason": "prefetch desligado no painel"}
    if not isinstance(body, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(400, "corpo deve ser {\"ids\": [...]}")
    _ensure_warm_worker()
    n = 0
    with _warm_cond:
        for v in ids[:50]:
            v = str(v or "")
            if VID_RE.match(v) and v not in _warm_set:
                _warm_set.add(v)
                _warm_q.append(v)
                n += 1
        if n:
            _warm_cond.notify_all()
    return {"ok": True, "queued": n}

# ---------- Saúde do motor de extração (apontamento do Argon) ----------
# O _client_health só aparecia no terminal; agora o painel VÊ e PODE resetar.
@app.get("/api/clients/health")
def clients_health(request: Request):
    _require_admin(request)
    now = time.time()
    with _health_lock:
        health_snap = {k: dict(v) for k, v in _client_health.items()}
        ms_snap = dict(_CLIENT_MS)
        ejs_needed = _EJS.get("needed")
        probe_at = int(_EJS.get("at") or 0) or None
        probe_ms = _EJS.get("last_ms")

    def _state(c):
        lbl = _label(c)
        h = health_snap.get(lbl)
        ms = ms_snap.get(lbl)
        if not h:
            return {"label": lbl, "state": "novo", "fails": 0, "resting_s": 0,
                    "last_try_s_ago": None, "ms": ms}
        fails = int(h.get("fails") or 0)
        resting = fails >= CLIENT_FAIL_STREAK and now < float(h.get("until") or 0)
        lt = h.get("last_try") or 0
        return {"label": lbl,
                "state": "folga" if resting else "de pé",
                "fails": fails,
                "resting_s": max(0, int(float(h.get("until") or 0) - now)) if resting else 0,
                "last_try_s_ago": int(now - lt) if lt else None,
                "ms": ms}

    return {"clients": [_state(c) for c in _clients_full()],
            "cookies": bool(SETTINGS.get("use_cookies") and COOKIES_FILE.exists()),
            "preferido": _last_ok["label"],
            "race_wave": max(1, min(4, int(SETTINGS.get("race_wave") or RACE_WAVE))),
            "extract_timeout": max(5, min(90, int(SETTINGS.get("extract_timeout") or EXTRACT_TIMEOUT))),
            "po_token": bool(_POT_PLUGIN_OK),
            # ---- resultado dos "pre-tests" (sondagem medida, não chutada) ----
            "ejs": ejs_needed,
            "probe_at": probe_at,
            "probe_ms": probe_ms,
            "probe_running": _probe_lock.locked()}

@app.post("/api/clients/probe")
def clients_probe(request: Request):
    """Roda a sondagem AGORA (mede os clients com o vídeo-canário). Roda em
    segundo plano: a resposta volta na hora e o painel consulta /health."""
    _require_admin(request)
    if _probe_lock.locked():
        return {"ok": True, "started": False, "motivo": "já está medindo"}
    threading.Thread(target=_probe_engine, kwargs={"full": True}, daemon=True).start()
    return {"ok": True, "started": True}

@app.post("/api/clients/reset")
def clients_reset(request: Request):
    """Força TODOS os clients de volta à disputa na próxima faixa (folga zerada,
    'client preferido' esquecido, marcações 403 por vídeo limpas)."""
    _require_admin(request)
    with _health_lock:
        n = len(_client_health)
        _client_health.clear()
    with _cache_lock:
        b = len(_bad_clients)
        _bad_clients.clear()
    _last_ok["label"] = None
    print("🩺 saúde dos clients RESETADA pelo painel — todos voltam à disputa")
    return {"ok": True, "cleared": n, "bad_clients_cleared": b}

# ---------- Perfil & Sessão ----------
@app.get("/api/me")
def api_me(request: Request):
    u = _current_user(request)
    adm = bool(u.get("is_admin"))
    return {
        "ok": True,
        "id": u["id"],
        "name": u["name"],
        "isAdmin": adm,
        "is_admin": adm,
        "user": {"id": u["id"], "name": u["name"], "is_admin": adm}
    }

@app.post("/api/logout")
def api_logout():
    resp = Response(json.dumps({"ok": True}), media_type="application/json")
    resp.delete_cookie("sk", path="/", samesite="lax")
    return resp

# ---------- Favoritos (isolados por usuário) ----------
@app.get("/api/favorites")
def list_favorites(request: Request):
    u = _current_user(request)
    with db() as con:
        return [track_row(r) for r in con.execute(
            "SELECT video_id, title, artist, thumbnail, added_at FROM favorites WHERE user_id=? ORDER BY added_at DESC",
            (u["id"],))]

@app.post("/api/favorites/{video_id}")
def add_favorite(video_id: str, request: Request, title: str = "", artist: str = "", thumbnail: str = ""):
    video_id = _valid_track_id(video_id)
    u = _current_user(request)
    with db() as con:
        con.execute("INSERT OR REPLACE INTO favorites (user_id, video_id, title, artist, thumbnail, added_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (u["id"], video_id, title, artist, thumbnail))
    return {"ok": True}

@app.delete("/api/favorites/{video_id}")
def del_favorite(video_id: str, request: Request):
    video_id = _valid_track_id(video_id)
    u = _current_user(request)
    with db() as con:
        con.execute("DELETE FROM favorites WHERE user_id=? AND video_id=?", (u["id"], video_id))
    return {"ok": True}

# ---------- Histórico (isolado por usuário) ----------
@app.get("/api/history")
def get_history(request: Request):
    u = _current_user(request)
    with db() as con:
        return [track_row(r) for r in con.execute(
            "SELECT video_id, title, artist, thumbnail, played_at FROM history WHERE user_id=? ORDER BY played_at DESC LIMIT 100",
            (u["id"],))]

@app.post("/api/history/{video_id}")
def add_history(video_id: str, request: Request, title: str = "", artist: str = "", thumbnail: str = ""):
    video_id = _valid_track_id(video_id)
    u = _current_user(request)
    with db() as con:
        con.execute("INSERT OR REPLACE INTO history (user_id, video_id, title, artist, thumbnail, played_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (u["id"], video_id, title, artist, thumbnail))
        if title:
            con.execute("UPDATE next_map SET title=?, artist=?, thumbnail=? WHERE user_id=? AND next_id=? AND (title IS NULL OR title='')",
                        (title, artist, thumbnail, u["id"], video_id))
    return {"ok": True}

@app.delete("/api/history")
def clear_history(request: Request):
    u = _current_user(request)
    with db() as con:
        n = con.execute("SELECT COUNT(*) c FROM history WHERE user_id=?", (u["id"],)).fetchone()["c"]
        con.execute("DELETE FROM history WHERE user_id=?", (u["id"],))
    return {"ok": True, "removed": int(n or 0)}

@app.delete("/api/history/{video_id}")
def delete_history_item(video_id: str, request: Request):
    video_id = _valid_track_id(video_id)
    u = _current_user(request)
    with db() as con:
        cur = con.execute("DELETE FROM history WHERE user_id=? AND video_id=?", (u["id"], video_id))
    return {"ok": True, "removed": max(0, cur.rowcount or 0)}

# ---------- Continuar ouvindo (o que o cache inteligente APRENDEU) ----------
@app.get("/api/smart/next/{video_id}")
def smart_next(video_id: str, request: Request, limit: int = Query(8, ge=1, le=20)):
    """O que o app aprendeu que você costuma ouvir DEPOIS desta faixa (next_map,
    alimentado pelas trocas de faixa reais isoladas por perfil). Retorna metadados duradouros."""
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    u = _current_user(request)
    try:
        with db() as con:
            # 1. Busca primeiro no perfil do usuário atual
            rows = con.execute(
                "SELECT next_id, title, artist, thumbnail, n FROM next_map WHERE user_id=? AND prev_id=? ORDER BY n DESC LIMIT ?",
                (u["id"], video_id, limit)).fetchall()
            # 2. Fallback para outros perfis locais caso o usuário atual ainda não tenha histórico dessa faixa
            if not rows:
                rows = con.execute(
                    "SELECT next_id, title, artist, thumbnail, MAX(n) as n FROM next_map WHERE prev_id=? GROUP BY next_id ORDER BY n DESC LIMIT ?",
                    (video_id, limit)).fetchall()

            out = []
            for r in rows:
                nid = r["next_id"]
                title = r["title"] or ""
                artist = r["artist"] or ""
                thumb = r["thumbnail"] or ""
                if not title:
                    m = con.execute("SELECT title, artist, thumbnail FROM history WHERE user_id=? AND video_id=? LIMIT 1",
                                    (u["id"], nid)).fetchone()
                    if not m:
                        m = con.execute("SELECT title, artist, thumbnail FROM history WHERE video_id=? LIMIT 1",
                                        (nid,)).fetchone()
                    if not m:
                        m = con.execute("SELECT title, artist, thumbnail FROM favorites WHERE user_id=? AND video_id=? LIMIT 1",
                                        (u["id"], nid)).fetchone()
                    if m:
                        title = m["title"] or ""
                        artist = m["artist"] or ""
                        thumb = m["thumbnail"] or ""
                out.append({
                    "videoId": nid,
                    "n": int(r["n"] or 0),
                    "title": title,
                    "artist": artist,
                    "thumbnail": thumb or f"/api/thumb/{nid}",
                    "known": bool(title)
                })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _friendly_error(e))
    return {"seed": video_id, "tracks": out}

# ---------- Playlists locais (isoladas por usuário) ----------
@app.get("/api/playlists")
def list_playlists(request: Request):
    u = _current_user(request)
    with db() as con:
        playlists = [row(r) for r in con.execute("SELECT id, name FROM local_playlists WHERE user_id=? ORDER BY name", (u["id"],))]
        for p in playlists:
            p["items"] = [track_row(r) for r in con.execute(
                "SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (p["id"],))]
        return playlists

@app.post("/api/playlists")
def create_playlist(name: str, request: Request):
    u = _current_user(request)
    name = (name or "").strip()[:80]
    if not name:
        raise HTTPException(400, "o nome da playlist não pode ser vazio")
    with db() as con:
        con.execute("INSERT OR IGNORE INTO local_playlists(user_id, name) VALUES (?, ?)", (u["id"], name))
        r = con.execute("SELECT id FROM local_playlists WHERE user_id=? AND name=?", (u["id"], name)).fetchone()
    return {"ok": True, "id": r["id"] if r else None}

@app.delete("/api/playlists/{pid}")
def delete_playlist(pid: int, request: Request):
    u = _current_user(request)
    with db() as con:
        _require_playlist_owner(con, pid, u, allow_admin=True)
        con.execute("DELETE FROM local_playlists WHERE id=?", (pid,))
        con.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
    return {"ok": True}

@app.post("/api/playlists/{pid}/items/{video_id}")
def add_to_playlist(pid: int, video_id: str, request: Request, title: str = "", artist: str = "", thumbnail: str = ""):
    video_id = _valid_track_id(video_id)
    u = _current_user(request)
    with db() as con:
        _require_playlist_owner(con, pid, u)
        pos = con.execute("SELECT COALESCE(MAX(position),0)+1 FROM playlist_items WHERE playlist_id=?",
                          (pid,)).fetchone()[0]
        con.execute("INSERT OR REPLACE INTO playlist_items VALUES (?,?,?,?,?,?)",
                    (pid, video_id, title, artist, thumbnail, pos))
    return {"ok": True}

@app.delete("/api/playlists/{pid}/items/{video_id}")
def remove_from_playlist(pid: int, video_id: str, request: Request):
    video_id = _valid_track_id(video_id)
    u = _current_user(request)
    with db() as con:
        _require_playlist_owner(con, pid, u)
        con.execute("DELETE FROM playlist_items WHERE playlist_id=? AND video_id=?", (pid, video_id))
    return {"ok": True}

# ---------- Estações (rádios nomeadas que ficam salvas, isoladas por usuário) ----------
def _station_row(r):
    d = row(r)
    if d:
        d["seedId"] = d.pop("seed_id", None)
        d["seedTitle"] = d.pop("seed_title", None)
        d["seedArtist"] = d.pop("seed_artist", None)
        d["seedThumb"] = d.pop("seed_thumb", None)
    return d

@app.get("/api/stations")
def list_stations(request: Request):
    u = _current_user(request)
    with db() as con:
        return [_station_row(r) for r in con.execute(
            "SELECT * FROM stations WHERE user_id=? ORDER BY name", (u["id"],))]

@app.post("/api/stations")
def create_station(data: dict, request: Request):
    """Cria uma estação a partir de uma faixa-semente do YouTube. Nome é
    obrigatório; criar a mesma semente 2x não duplica."""
    if not isinstance(data, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    name = str(data.get("name") or "").strip()[:80]
    vid = str(data.get("videoId") or "")
    if not name:
        raise HTTPException(400, "a estação precisa de um nome")
    if not VID_RE.match(vid):
        raise HTTPException(400, "videoId inválido — a estação nasce de uma faixa do YouTube")
    u = _current_user(request)
    with db() as con:
        dup = con.execute("SELECT id FROM stations WHERE user_id=? AND name=? AND seed_id=?",
                          (u["id"], name, vid)).fetchone()
        if dup:
            return {"ok": True, "id": dup["id"], "duplicate": True}
        cur = con.execute(
            "INSERT INTO stations(user_id, name, seed_id, seed_title, seed_artist, seed_thumb) VALUES (?,?,?,?,?,?)",
            (u["id"], name, vid, str(data.get("title") or ""), str(data.get("artist") or ""),
             str(data.get("thumbnail") or "")))
        sid = cur.lastrowid
    return {"ok": True, "id": sid}

@app.delete("/api/stations/{sid}")
def delete_station(sid: int, request: Request):
    u = _current_user(request)
    with db() as con:
        cur = con.execute("DELETE FROM stations WHERE id=? AND (user_id=? OR ?=1)",
                          (sid, u["id"], u.get("is_admin", 0)))
    if not cur.rowcount:
        raise HTTPException(404, "Estação não encontrada")
    return {"ok": True}

@app.post("/api/playlists/{pid}/reorder")
def reorder_playlist(pid: int, body: dict, request: Request):
    """Reordena as faixas de uma playlist: corpo {"order": [videoId, ...]}.
    IDs que não estiverem no corpo mantêm a posição relativa que já tinham."""
    if not isinstance(body, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    order = body.get("order")
    if not isinstance(order, list):
        raise HTTPException(400, "corpo deve ser {\"order\": [videoId, ...]}")
    u = _current_user(request)
    with db() as con:
        _require_playlist_owner(con, pid, u)
        for i, vid in enumerate(order):
            vid = str(vid or "")
            if VID_RE.match(vid) or UP_RE.match(vid):
                con.execute("UPDATE playlist_items SET position=? WHERE playlist_id=? AND video_id=?",
                            (i + 1, pid, vid))
    return {"ok": True}

@app.post("/api/playlists/{pid}/items_bulk")
def add_items_bulk(pid: int, body: dict, request: Request):
    """Importação em lote (playlist inteira do YouTube): {"tracks": [{videoId, ...}]}.
    IDs inválidos são ignorados; a ordem enviada é preservada."""
    if not isinstance(body, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    tracks = body.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise HTTPException(400, "corpo deve ser {\"tracks\": [...]}")
    tracks = tracks[:500]
    u = _current_user(request)
    n = 0
    with db() as con:
        _require_playlist_owner(con, pid, u)
        pos = con.execute("SELECT COALESCE(MAX(position),0) FROM playlist_items WHERE playlist_id=?",
                          (pid,)).fetchone()[0]
        for t in tracks:
            if not isinstance(t, dict):
                continue
            vid = str(t.get("videoId") or "")
            # aceita faixas do YouTube E uploads locais (up_…): playlists mistas
            if not (VID_RE.match(vid) or UP_RE.match(vid)):
                continue
            pos += 1
            con.execute("INSERT OR REPLACE INTO playlist_items VALUES (?,?,?,?,?,?)",
                        (pid, vid, str(t.get("title") or ""), str(t.get("artist") or ""),
                         str(t.get("thumbnail") or ""), pos))
            n += 1
    return {"ok": True, "added": n}

# ---------- Playlists do YouTube via link (importar e/ou baixar) ----------
_PL_PREFIX_RE = re.compile(r"^(VL)?(PL|OLAK5uy_|RD|UU|LL|FL|TL)[A-Za-z0-9_-]{8,}$")

def _clean_playlist_id(pid: str) -> str:
    """Limpa e normaliza o ID da playlist, removendo pontuações e prefixo VL."""
    if not pid:
        return ""
    pid = str(pid).strip().rstrip(".,;/")
    # YouTube Music frequentemente embute VL antes do ID real da lista (ex: VLPL... ou VLOLAK...)
    if pid.startswith("VL") and (pid.startswith("VLPL") or pid.startswith("VLOLAK") or pid.startswith("VLRD") or len(pid) > 16):
        pid = pid[2:]
    return pid

def _playlist_id_from_url(s: str) -> str | None:
    """ID de playlist de um link do YouTube/YT Music (?list=…) ou o ID cru (PL…/OLAK5uy_…)."""
    if not s:
        return None
    s = str(s).strip().strip("'\"<>.,;")
    m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", s)
    if m:
        return _clean_playlist_id(m.group(1))
    m_browse = re.search(r"/(?:browse|playlist)/(?:VL)?([A-Za-z0-9_-]{8,})", s)
    if m_browse:
        return _clean_playlist_id(m_browse.group(1))
    if _PL_PREFIX_RE.match(s):
        return _clean_playlist_id(s)
    return None

def _extract_video_id_from_url(s: str) -> str | None:
    """Tenta extrair um videoId de 11 caracteres de URLs do YouTube se houver."""
    if not s:
        return None
    m = re.search(r"(?:[?&]v=|/v/|youtu\.be/|/embed/|/watch\?v=)([A-Za-z0-9_-]{11})", str(s))
    return m.group(1) if m else None

def _pl_fetch_flat(pid: str, vid_fallback: str | None = None, cap: int = 500) -> dict:
    """Metadados + faixas em modo FLAT (rápido: uma requisição, sem extrair cada vídeo)."""
    clean_pid = _clean_playlist_id(pid)

    # 1. Se for rádio dinâmica / mix (RD...) do YouTube Music:
    if clean_pid.startswith("RD"):
        vid = vid_fallback
        if not vid and clean_pid.startswith("RDAMVM") and len(clean_pid) >= 17:
            vid = clean_pid[6:17]
        if vid and VID_RE.match(vid):
            try:
                res = yt.get_watch_playlist(vid)
                tracks = []
                for t in (res.get("tracks") or []):
                    if not isinstance(t, dict):
                        continue
                    v = t.get("videoId")
                    if not v:
                        continue
                    artists = t.get("artists")
                    art = ", ".join(a.get("name", "") for a in artists if isinstance(a, dict)) if isinstance(artists, list) else (t.get("byline") or "YouTube")
                    dur = t.get("length") or t.get("duration") or ""
                    thumbs = t.get("thumbnails") or []
                    thumb = (thumbs[-1].get("url") if thumbs else "") or f"/api/thumb/{v}"
                    tracks.append({
                        "videoId": v,
                        "title": t.get("title") or "Sem título",
                        "artist": art or "YouTube",
                        "thumbnail": thumb,
                        "duration": str(dur)
                    })
                if tracks:
                    return {
                        "id": pid,
                        "title": res.get("title") or f"Mix Rádio ({vid})",
                        "uploader": "YouTube Music",
                        "total": len(tracks),
                        "tracks": tracks[:cap]
                    }
            except Exception as ytm_err:
                logger.debug(f"ytmusicapi get_watch_playlist falhou para mix {clean_pid}: {ytm_err}")

    # 2. Extração via yt-dlp (rápido e compatível com todas as playlists públicas/não-listadas)
    opts = _base_ydl_opts()
    opts.update({
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "playlistend": cap,          # playlist gigante não derruba o app
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/playlist?list={clean_pid}", download=False)
    tracks = []
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        vid = e.get("id")
        if not vid:
            continue
        dur = e.get("duration")
        dur_s = f"{int(dur // 60)}:{int(dur % 60):02d}" if isinstance(dur, (int, float)) and dur else ""
        thumbs = e.get("thumbnails") or []
        thumb = (thumbs[-1].get("url") if thumbs else "") or e.get("thumbnail") \
                or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
        tracks.append({"videoId": vid, "title": e.get("title") or "Sem título",
                       "artist": e.get("uploader") or e.get("channel") or "YouTube",
                       "thumbnail": f"/api/thumb/{vid}", "duration": dur_s})
    return {"id": pid, "title": (info or {}).get("title") or f"Playlist {pid}",
            "uploader": (info or {}).get("uploader") or (info or {}).get("channel") or "",
            "total": (info or {}).get("playlist_count") or len(tracks), "tracks": tracks}

_pl_pool = cf.ThreadPoolExecutor(max_workers=2)   # separado: playlist grande não trava busca/extração

@app.get("/api/ytplaylist")
def get_yt_playlist(request: Request, url: str = Query(..., min_length=3)):
    _check_rate_limit(request, "ytplaylist")
    pid = _playlist_id_from_url(url)
    vid = _extract_video_id_from_url(url)
    if not pid:
        if vid:
            raise HTTPException(400, f"O link informado é de uma música/vídeo individual ({vid}), não de uma playlist. "
                                     f"Para ouvi-la ou adicioná-la a uma playlist local, use a barra de busca acima!")
        raise HTTPException(400, "Não reconheci uma playlist nesse link — use a URL contendo ?list=… "
                                 "(do YouTube ou YouTube Music) ou o ID cru (ex: PL… ou OLAK5uy_…).")

    try:
        data = _pl_pool.submit(_pl_fetch_flat, pid, vid).result(timeout=90)
    except cf.TimeoutError:
        raise HTTPException(504, "O YouTube demorou mais de 90 segundos para carregar esta playlist. Tente novamente em instantes.")
    except Exception as e:
        err_msg = str(e)
        low = err_msg.lower()
        if "does not exist" in low or "not found" in low or "unavailable" in low:
            if len(pid) < 18 and pid.startswith("PL"):
                detail = (f"A playlist '{pid}' não foi encontrada no YouTube. "
                          f"O ID parece estar cortado/incompleto (apenas {len(pid)} caracteres, "
                          f"enquanto playlists normais do YouTube costumam ter ~34 caracteres). "
                          f"Verifique se você copiou o link inteiro.")
            else:
                detail = f"A playlist '{pid}' não foi encontrada no YouTube (pode ter sido excluída ou o link está incorreto)."
            raise HTTPException(404, detail)
        elif "private" in low or "privada" in low:
            raise HTTPException(403, f"A playlist '{pid}' está configurada como PRIVADA no YouTube. "
                                     f"Altere a privacidade para 'Não listada' ou 'Pública' no YouTube para poder importá-la.")
        elif "unviewable" in low:
            raise HTTPException(400, "Este tipo de lista dinâmica/mix do YouTube Music não pode ser exportado diretamente. "
                                     "Dica: experimente salvar as faixas em uma playlist comum no YouTube antes de importar.")
        elif "sign in to confirm" in low or "bot" in low:
            raise HTTPException(429, "O YouTube limitou temporariamente as requisições deste servidor (verificação anti-bot). "
                                     "Aguarde alguns minutos ou adicione cookies.txt no painel.")
        else:
            raise HTTPException(400, _friendly_error(e))

    if not data.get("tracks"):
        raise HTTPException(404, "A playlist foi encontrada, mas não contém faixas públicas disponíveis.")

    data["capped"] = (data.get("total") or 0) > len(data["tracks"])
    return data

# --- baixar a playlist inteira em subpasta (job em segundo plano) ---
_pl_jobs: dict[str, dict] = {}
_pl_jobs_lock = threading.Lock()
_pl_dl_pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pl_dl")

def _pl_safe_folder(name: str) -> str:
    safe = "".join(c for c in (name or "").strip() if c.isalnum() or c in " -_()").strip()[:60]
    return safe or "playlist"

def _prune_pl_jobs():
    now = time.time()
    with _pl_jobs_lock:
        old = [k for k, j in _pl_jobs.items() if now - j.get("created", now) > 2 * 3600]
        for k in old:
            _pl_jobs.pop(k, None)
        while len(_pl_jobs) > 12:
            _pl_jobs.pop(next(iter(_pl_jobs)), None)

def _run_pl_download(job: dict, folder: Path, tracks: list):
    """Baixa as faixas UMA POR VEZ na subpasta (gentil com o YouTube, status no job)."""
    folder.mkdir(parents=True, exist_ok=True)
    job["folder"] = str(folder.relative_to(DATA))
    for i, t in enumerate(tracks):
        if job.get("cancel"):
            job["status"] = "cancelada"
            return
        vid = str(t.get("videoId") or "")
        name = t.get("title") or vid
        job["current"] = f"{i + 1}/{len(tracks)} · {name}"
        if not VID_RE.match(vid):
            job["failed"] += 1
            job["errors"].append(f"{name}: id inválido")
        else:
            try:
                _download_to_dir(vid, folder, name)
            except Exception as e:
                job["failed"] += 1
                job["errors"].append(f"{name}: {str(getattr(e, 'detail', None) or e)[:140]}")
        job["done"] += 1
        if len(job["errors"]) > 40:
            del job["errors"][:-40]
    job["status"] = "cancelada" if job.get("cancel") else ("concluída" if not job["failed"] else "concluída com erros")

def _pl_download_thread(job: dict, pid: str, tracks, folder_name: str):
    if job.get("cancel"):
        job["status"] = "cancelada"
        return
    try:
        if not tracks:   # frontend não mandou a lista → busca flat agora
            job["status"] = "buscando faixas…"
            data = _pl_fetch_flat(pid)
            tracks = data["tracks"]
            folder_name = folder_name or data["title"]
            job["total"] = len(tracks)
        if not tracks:
            job["status"] = "erro"
            job["errors"].append("playlist veio vazia")
            return
        folder = DL_DIR / "playlists" / _pl_safe_folder(folder_name or f"Playlist {pid}")
        _run_pl_download(job, folder, tracks)
    except Exception as e:
        job["status"] = "erro"
        job["errors"].append(str(e)[:200])

@app.post("/api/ytplaylist/download")
def ytplaylist_download(body: dict, request: Request):
    u = _require_admin(request)
    _check_rate_limit(request, "download")
    if not isinstance(body, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    pid = _playlist_id_from_url(str(body.get("url") or ""))
    if not pid:
        raise HTTPException(400, "Não reconheci uma playlist nesse link (?list=… ou ID cru).")
    tracks = body.get("tracks") if isinstance(body, dict) else None
    if isinstance(tracks, list) and tracks:
        tracks = [t for t in tracks if isinstance(t, dict) and t.get("videoId")][:500]
    else:
        tracks = None
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "user_id": u["id"], "status": "na fila…", "total": len(tracks or []), "done": 0, "failed": 0,
           "current": "", "folder": "", "cancel": False, "errors": [], "created": time.time()}
    _prune_pl_jobs()
    with _pl_jobs_lock:
        _pl_jobs[jid] = job
    _pl_dl_pool.submit(_pl_download_thread, job, pid, tracks, str(body.get("folder") or ""))
    return {"ok": True, "jobId": jid}

@app.get("/api/ytplaylist/job/{jid}")
def ytplaylist_job(jid: str, request: Request):
    u = _require_admin(request)
    with _pl_jobs_lock:
        job = _pl_jobs.get(jid)
        if not job:
            raise HTTPException(404, "job não encontrado")
        if job.get("user_id") not in (None, u["id"]) and not u.get("is_admin"):
            raise HTTPException(403, "Acesso negado a este job")
        snap = {k: job.get(k) for k in ("id", "status", "total", "done", "failed", "current", "folder")}
        snap["errors"] = list(job.get("errors") or [])[-10:]
    return snap

@app.post("/api/ytplaylist/job/{jid}/cancel")
def ytplaylist_job_cancel(jid: str, request: Request):
    u = _require_admin(request)
    with _pl_jobs_lock:
        job = _pl_jobs.get(jid)
        if not job:
            raise HTTPException(404, "job não encontrado")
        if job.get("user_id") not in (None, u["id"]) and not u.get("is_admin"):
            raise HTTPException(403, "Acesso negado a este job")
        job["cancel"] = True
    return {"ok": True}

@app.post("/api/playlists/{pid}/download")
def download_saved_playlist(pid: int, request: Request):
    """Baixa uma playlist SALVA da aba Playlists direto pra subpasta
    downloads/playlists/<nome>/ — mesmo sistema de jobs do link do YouTube.
    Assim a playlist continua baixável depois de importada/adicionada."""
    u = _require_admin(request)
    _check_rate_limit(request, "download")
    with db() as con:
        pl = row(_require_playlist_owner(con, pid, u))
        tracks = [track_row(r) for r in con.execute(
            "SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (pid,))]
    if not tracks:
        raise HTTPException(400, "Playlist vazia — não tem o que baixar")
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "user_id": u["id"], "status": "na fila…", "total": len(tracks), "done": 0, "failed": 0,
           "current": "", "folder": "", "cancel": False, "errors": [], "created": time.time()}
    _prune_pl_jobs()
    with _pl_jobs_lock:
        _pl_jobs[jid] = job
    _pl_dl_pool.submit(_pl_download_thread, job, "", tracks, pl["name"])
    return {"ok": True, "jobId": jid, "total": len(tracks)}

# ---------- Downloads ----------
_dl_active: set = set()   # vídeo_ids em download agora (evita baixar o mesmo 2x)
_dl_lock = threading.Lock()

def _existing_download(safe: str, d: Path = None):
    d = d or DL_DIR
    for ext in (".mp3", ".m4a", ".webm", ".opus"):
        p = d / (safe + ext)
        if p.is_file() and p.stat().st_size > 0:
            return p.name
    return None

def _download_from_cache(video_id: str, meta: dict, key: str, out_dir: Path):
    """Promove cache/{vid}.audio → downloads/ (apontamento do Argon): se a faixa
    já foi OUVIDA inteira, o áudio está no disco — converter localmente evita
    re-baixar do YouTube (mais rápido, imune a anti-bot, zero rede).
    Com FFmpeg: MP3 na qualidade do painel COM TAGS ID3 (título/artista saem do
    meta do cache — apontamento do gemini 3.8: o player do carro/celular mostra
    o nome certo). Sem FFmpeg: cópia crua (m4a/opus).
    TRANSAÇÃO (gemini 3.8): o _disk_lock cobre SÓ a cópia rápida do .audio pra
    uma fonte privada — o _cache_evict nunca mais apaga o arquivo no meio do
    promote (antes, se o eviction corresse entre o lookup e o copyfile, caía no
    FileNotFoundError e o fluxo fugia pro yt-dlp à toa). O FFmpeg (lento) roda
    DEPOIS, sem segurar trava — senão congelava cache/eviction/streaming por
    até 600s. Retorna None em qualquer pane → o fluxo cai pro yt-dlp de sempre."""
    audio, _ = _cache_paths(video_id)
    src = out_dir / f"{key}.{uuid.uuid4().hex[:6]}.csrc"
    try:
        ct = (meta.get("content_type") or "").lower()
        ext = ".m4a" if "mp4" in ct else (".webm" if "webm" in ct else ".m4a")
        q = str(SETTINGS.get("mp3_quality") or "192")
        title = str(meta.get("title") or "")[:200]
        artist = str(meta.get("artist") or "")[:200]
        # fonte privada: trava o _disk_lock só na cópia (rápida), solta, e o
        # resto roda sobre a cópia — intocável pelo eviction
        with _disk_lock:
            shutil.copyfile(audio, src)
        try:
            if shutil.which("ffmpeg"):
                final = out_dir / f"{key}.mp3"
                tmp = out_dir / f"{key}.{uuid.uuid4().hex[:6]}.part"
                r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                                    "-i", str(src), "-vn", "-codec:a", "libmp3lame",
                                    "-b:a", f"{q}k",
                                    "-id3v2_version", "3",   # v2.3 = compat com player de carro/rádio
                                    "-metadata", f"title={title}",
                                    "-metadata", f"artist={artist}",
                                    # -f mp3 OBRIGATÓRIO: o ffmpeg escolhe o muxer pela
                                    # EXTENSÃO do destino — e o temporário termina em
                                    # ".part" → "Unable to choose an output format" →
                                    # era ESTE o motivo do promote falhar no SEU log
                                    # (KWUUUglrgho, diagnosticado pelo gemini 3.8)
                                    "-f", "mp3", str(tmp)],
                                   capture_output=True, text=True, timeout=600)
                if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
                    os.replace(tmp, final)
                    _dl_index_add(video_id, final)
                    print(f"⬇ {video_id}: salvo direto do CACHE (mp3 {q}k + tags ID3, zero YouTube)")
                    return {"file": final.name, "cached": True, "source": "cache"}
                tmp.unlink(missing_ok=True)
                # por que o ffmpeg falhou? sem o stderr no log a gente nunca soube
                # (caso real do seu log: KWUUUglrgho — gemini 3.8 pediu o diagnóstico)
                err = (r.stderr or "").strip()[:200] or f"código de saída {r.returncode}"
                print(f"⚠️ ffmpeg falhou no promote de {video_id}: {err} — sigo com cópia crua")
            final = out_dir / f"{key}{ext}"
            tmp = out_dir / f"{key}.{uuid.uuid4().hex[:6]}.part"
            shutil.copyfile(src, tmp)
            os.replace(tmp, final)
            _dl_index_add(video_id, final)
            print(f"⬇ {video_id}: salvo direto do CACHE ({ext} cru, zero YouTube)")
            return {"file": final.name, "cached": True, "source": "cache"}
        finally:
            src.unlink(missing_ok=True)   # a fonte privada nunca vira lixo
    except Exception as e:
        print(f"⚠️ promote cache→download falhou ({video_id}): {str(e)[:120]} — sigo pro yt-dlp")
        try:
            for p in out_dir.glob(f"{key}.*.part"):
                p.unlink(missing_ok=True)
            src.unlink(missing_ok=True)
        except Exception:
            pass
        return None

def _download_to_dir(video_id: str, out_dir: Path, title_hint: str = "") -> dict:
    """Baixa um vídeo (MP3 via FFmpeg, ou m4a cru) em out_dir — usado pelo download
    individual e pelo download de playlist inteira (subpasta por playlist).
    Novos arquivos ganham sufixo ' [videoId]': dois vídeos com o MESMO título
    não se sobrescrevem mais (bug da colisão de nomes). Downloads antigos, sem
    o sufixo, continuam sendo encontrados pela 2ª verificação."""
    if not VID_RE.match(video_id or ""):
        raise HTTPException(400, "video_id inválido")
    safe = "".join(c for c in title_hint if c.isalnum() or c in " -_()")[:80].strip() or video_id
    key = f"{safe} [{video_id}]"
    # já baixado antes? responde na hora sem passar pelo yt-dlp
    # (o 2º clique no mesmo vídeo dava 500 por "has already been downloaded")
    cached = _existing_download(key, out_dir) or _existing_download(safe, out_dir)
    if cached:
        _dl_index_add(video_id, out_dir / cached)
        return {"file": cached, "cached": True}
    with _dl_lock:
        if video_id in _dl_active:
            raise HTTPException(409, "este vídeo já está baixando — aguarda uns segundos")
        _dl_active.add(video_id)
    try:
        # Já ouviu essa música? O áudio completo pode estar no cache — promove
        # DIRETO do disco (MP3 via FFmpeg local ou cópia cru): nem fala com o
        # YouTube. Vale pra download individual E pra playlist inteira.
        hit = _cache_lookup(video_id)
        if hit:
            made = _download_from_cache(video_id, hit, key, out_dir)
            if made:
                return made
        out = str(out_dir / f"{key}.%(ext)s")
        has_ffmpeg = shutil.which("ffmpeg") is not None
        post = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                 "preferredquality": str(SETTINGS.get("mp3_quality") or "192")}] if has_ffmpeg else []
        opts = _base_ydl_opts()
        opts.update({
            "skip_download": False,
            "format": "bestaudio/best" if has_ffmpeg else "bestaudio[ext=m4a]/best",
            "outtmpl": out,
            "postprocessors": post,
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                name = ydl.prepare_filename(info)
            if has_ffmpeg:
                name = os.path.splitext(name)[0] + ".mp3"
            _dl_index_add(video_id, Path(name))
            return {"file": os.path.basename(name), "cached": False}
        except Exception as e:
            # yt-dlp reclamou mas o arquivo existe (nome/extensão divergente): devolve ok
            cached = _existing_download(safe, out_dir)
            if cached:
                _dl_index_add(video_id, out_dir / cached)
                return {"file": cached, "cached": True}
            raise
    finally:
        with _dl_lock:
            _dl_active.discard(video_id)

_DL_EXTS = (".mp3", ".m4a", ".webm", ".opus")

@app.post("/api/download/{video_id}")
def download(video_id: str, request: Request, title: str = Query("")):
    _require_admin(request)
    _check_rate_limit(request, "download")
    try:
        return {"ok": True, **_download_to_dir(video_id, DL_DIR, title)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _friendly_error(e))

@app.get("/api/downloads")
def list_downloads():
    """Arquivos soltos em downloads/ — [{name, videoId}] (videoId vem do índice;
    downloads antigos, anteriores ao índice, voltam com videoId null)."""
    with _dl_index_lock:
        _dl_index_load()
        rev = {str(rec.get("file") or "").replace("\\", "/").lower(): v for v, rec in _dl_index.items()}
    data_res = DATA.resolve()
    out = []
    for f in sorted(DL_DIR.iterdir(), key=lambda x: x.name.lower()):
        if not f.is_file() or f.name.startswith("."):
            continue
        rel = str(f.resolve().relative_to(data_res)).replace("\\", "/").lower()
        out.append({"name": f.name, "videoId": rev.get(rel)})
    return out

@app.get("/downloads/{name}")
def get_download(name: str):
    p = (DL_DIR / name).resolve()
    if not p.is_file() or p.parent != DL_DIR.resolve() or p.name.startswith(".") or p.suffix.lower() not in _DL_EXTS:
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)

@app.get("/api/downloads/folders")
def downloads_folders():
    """Subpastas de playlists baixadas (downloads/playlists/<nome>/).
    Cada arquivo vem com videoId quando conhecido (índice de downloads)."""
    root = DL_DIR / "playlists"
    out = []
    with _dl_index_lock:
        _dl_index_load()
        rev = {str(rec.get("file") or "").replace("\\", "/").lower(): v for v, rec in _dl_index.items()}
    data_res = DATA.resolve()
    if root.is_dir():
        for d in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if not d.is_dir():
                continue
            files = []
            for f in sorted(d.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file() and f.suffix.lower() in (".mp3", ".m4a", ".webm", ".opus"):
                    rel = str(f.resolve().relative_to(data_res)).replace("\\", "/").lower()
                    files.append({"name": f.name, "videoId": rev.get(rel)})
            out.append({"name": d.name, "count": len(files), "files": files})
    return out

@app.delete("/api/downloads/folders/{folder}")
def delete_download_folder(folder: str, request: Request):
    """Exclui a subpasta inteira de uma playlist baixada (com trava de travessia:
    só vale pasta FILHA DIRETA de downloads/playlists/)."""
    _require_admin(request)
    base = (DL_DIR / "playlists").resolve()
    fdir = (DL_DIR / "playlists" / folder).resolve()
    if fdir.parent != base or not fdir.is_dir():
        raise HTTPException(404, "Pasta não encontrada")
    n = sum(1 for f in fdir.iterdir() if f.is_file())
    shutil.rmtree(fdir)
    # tira do índice as faixas que apontavam para essa pasta (nada de "fantasmas")
    with _dl_index_lock:
        _dl_index_load()
        dead = [v for v, rec in _dl_index.items()
                if str(rec.get("file", "")).replace("\\", "/").startswith("downloads/playlists/")
                and not (DATA / rec["file"]).is_file()]
        for v in dead:
            _dl_index.pop(v, None)
        if dead:
            _dl_index_save()
    return {"ok": True, "removed": n}

@app.get("/downloads/playlists/{folder}/{name}")
def get_pl_download(folder: str, name: str):
    base = (DL_DIR / "playlists").resolve()
    fdir = (base / folder).resolve()
    if fdir.parent != base:          # bloqueia ../ e trapaça de caminho
        raise HTTPException(404)
    p = (fdir / name).resolve()
    if not p.is_file() or p.parent != fdir or p.name.startswith(".") or p.suffix.lower() not in _DL_EXTS:
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)

def _dl_index_remove_file(rel_path: str) -> int:
    """Tira do índice todo vídeo cujo arquivo baixado era rel_path (nada de fantasma).
    Comparação tolerante: barra invertida OU normal e case-insensitive — no Windows
    o _dl_index_add grava "downloads\\x.mp3" e a rota de delete manda
    "downloads/x.mp3"; antes disso a entrada não era achada e virava fantasma
    (apontamento do gemini 3.8)."""
    with _dl_index_lock:
        _dl_index_load()
        alvo = str(rel_path or "").replace("\\", "/").strip().lower()
        dead = [v for v, rec in _dl_index.items()
                if str(rec.get("file", "")).replace("\\", "/").lower() == alvo]
        for v in dead:
            _dl_index.pop(v, None)
        if dead:
            _dl_index_save()
        return len(dead)

@app.delete("/api/downloads/{name}")
def delete_download_file(name: str, request: Request):
    """Exclui UMA música avulsa de downloads/ (apontamento do Argon: só existia
    DELETE de pasta inteira e de cache — não dava pra apagar um arquivo só)."""
    _require_admin(request)
    p = (DL_DIR / name).resolve()
    if p.parent != DL_DIR.resolve() or not p.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    if p.suffix.lower() not in _DL_EXTS:
        raise HTTPException(400, "Isso não é um arquivo de música")
    p.unlink()
    # BASE.resolve() dos dois lados: no Windows o case do caminho resolvido pode
    # divergir do BASE "cru" e o relative_to levantava ValueError (500 no delete)
    n = _dl_index_remove_file(str(p.relative_to(DATA.resolve())).replace("\\", "/"))
    return {"ok": True, "indexed": n}

@app.delete("/api/downloads/folders/{folder}/{name}")
def delete_download_folder_file(folder: str, name: str, request: Request):
    """Idem, para um arquivo DENTRO de downloads/playlists/<pasta>/."""
    _require_admin(request)
    base = (DL_DIR / "playlists").resolve()
    fdir = (base / folder).resolve()
    if fdir.parent != base or not fdir.is_dir():
        raise HTTPException(404, "Pasta não encontrada")
    p = (fdir / name).resolve()
    if p.parent != fdir or not p.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    if p.suffix.lower() not in _DL_EXTS:
        raise HTTPException(400, "Isso não é um arquivo de música")
    p.unlink()
    n = _dl_index_remove_file(str(p.relative_to(DATA.resolve())).replace("\\", "/"))
    return {"ok": True, "indexed": n}

# ---------- Suas músicas (upload de arquivos de FORA do YouTube) ----------
# UP_RE e UP_DIR já definidos na seção de cache/id acima (evita duplicação).
UP_EXT = {".mp3", ".m4a", ".mp4", ".opus", ".ogg", ".oga", ".flac", ".wav", ".webm"}
UP_MAX_MB = 300

# ---------- Metadados online (Deezer + iTunes — grátis, sem chave) ----------
def _deezer_guess(title: str, artist: str = "", limit: int = 5) -> list:
    """Deezer Search API: ótima cobertura de música BR e manda a DURAÇÃO,
    que usamos pra conferir se o resultado é a mesma faixa do arquivo."""
    q = " ".join(x for x in [(artist or "").split(",")[0].strip(), (title or "").strip()] if x)
    if len(q) < 2:
        return []
    try:
        r = requests.get("https://api.deezer.com/search",
                         params={"q": q, "limit": max(1, limit)}, timeout=8)
        out = []
        for it in (r.json() or {}).get("data") or []:
            al = it.get("album") or {}
            out.append({"title": it.get("title") or "",
                        "artist": (it.get("artist") or {}).get("name") or "",
                        "album": al.get("title") or "",
                        "art_url": al.get("cover_big") or al.get("cover_medium") or "",
                        "year": 0, "dur": float(it.get("duration") or 0), "src": "deezer"})
        return out[:limit]
    except Exception:
        return []

def _itunes_guess(title: str, artist: str = "", limit: int = 5) -> list:
    """Procura capa/artista/álbum/ano oficiais pelo título (e artista se houver).
    Falha de rede ou nada encontrado → lista vazia (nunca derruba o upload)."""
    q = " ".join(x for x in [(artist or "").split(",")[0].strip(), (title or "").strip()] if x)
    if len(q) < 2:
        return []
    try:
        r = requests.get("https://itunes.apple.com/search",
                         params={"term": q, "entity": "song", "media": "music",
                                 "country": "BR", "limit": max(1, limit)},
                         timeout=8)
        out = []
        for it in (r.json() or {}).get("results") or []:
            art = (it.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
            ym = re.search(r"(\d{4})", it.get("releaseDate") or "")
            out.append({"title": it.get("trackName") or "",
                        "artist": it.get("artistName") or "",
                        "album": it.get("collectionName") or "",
                        "art_url": art,
                        "year": int(ym.group(1)) if ym else 0,
                        "dur": round((it.get("trackTimeMillis") or 0) / 1000, 1),
                        "src": "itunes"})
        return out[:limit]
    except Exception:
        return []

def _meta_candidates(title: str, artist: str = "", dur: float = 0.0) -> list:
    """Deezer + iTunes juntos, dedup por título+artista. Com a duração do
    arquivo, o candidato mais parecido (±8s) vai pra frente — evita adotar
    a música errada com o mesmo nome."""
    cands = []
    for src in (_deezer_guess, _itunes_guess):
        try:
            cands.extend(src(title, artist))
        except Exception:
            pass
    if dur:
        cands.sort(key=lambda c: abs((c.get("dur") or 0) - dur))
    seen, out = set(), []
    for c in cands:
        k = (c["title"].lower(), c["artist"].lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out

def _is_safe_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("https", "http"):
            return False
        hostname = p.hostname
        if not hostname or hostname.lower() in ("localhost", "127.0.0.1", "::1"):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or not ip.is_global:
                return False
        except ValueError:
            addr_info = socket.getaddrinfo(hostname, None)
            for item in addr_info:
                ip_str = item[4][0]
                ip = ipaddress.ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or not ip.is_global:
                    return False
        return True
    except Exception:
        return False

def _dl_art(url: str):
    """Baixa a capa (JPEG). Protegido contra SSRF (inclusive redirects) e com limite de 5MB."""
    cur = str(url or "").strip()
    if not cur:
        return None
    try:
        for _ in range(4):
            if not _is_safe_url(cur):
                return None
            with requests.get(cur, timeout=8, stream=True, allow_redirects=False) as r:
                if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        return None
                    cur = urllib.parse.urljoin(cur, loc)
                    continue
                if not r.ok:
                    return None
                content = b""
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    content += chunk
                    if len(content) > 5 * 1024 * 1024:
                        return None
                if content[:3] == b"\xff\xd8\xff":
                    return content
                return None
    except Exception:
        pass
    return None

def _up_write_tags(p: Path, title: str = "", artist: str = "", album: str = "",
                   art: bytes | None = None, year: int = 0, genre: str = "") -> bool:
    """Grava título/artista/álbum/ano/gênero/capa DENTRO do arquivo (sobrevive
    fora do app). Cada formato do seu jeito; falha de tag não é erro — o banco fica certo."""
    ok = False
    try:
        from mutagen import File as MFile
        m = MFile(str(p), easy=True)
        if m is not None:
            if title:
                m["title"] = [title]
            if artist:
                m["artist"] = [artist]
            if album:
                m["album"] = [album]
            if year:
                m["date"] = [str(year)]
            if genre:
                m["genre"] = [genre]
            m.save()
            ok = True
    except Exception:
        pass
    if not art:
        return ok
    ext = p.suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, APIC
            tags = ID3(str(p))
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, data=art))
            tags.save()
            ok = True
        elif ext in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4, MP4Cover
            mp4 = MP4(str(p))
            mp4.tags["covr"] = [MP4Cover(art, imageformat=MP4Cover.FORMAT_JPEG)]
            mp4.save()
            ok = True
        elif ext == ".flac":
            from mutagen.flac import Picture
            fl = MFile(str(p))
            if fl is not None and hasattr(fl, "pictures"):
                pic = Picture()
                pic.data, pic.mime, pic.type = art, "image/jpeg", 3
                fl.clear_pictures()
                fl.add_picture(pic)
                fl.save()
                ok = True
    except Exception:
        pass
    return ok

def _clean_file_stem(stem: str) -> str:
    """Limpa o nome do arquivo pra virar nome de música: tira numeração
    ("01 - ", "07."), sufixo de ripador e a sujeira de site de download —
    "(Official Video)", "[HQ]", "(Lyrics)", "www.site.com" etc.
    Preserva o que é informação musical: feat., remix, ao vivo."""
    s = stem.strip()
    s = re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", s)              # "01 - ", "07.", "03_"
    s = re.sub(r"^\s*track\s*\d+\s*[-._)]?\s*", "", s, flags=re.I)
    s = s.replace("_", " ")
    def _seg(m):
        inner = m.group(0).lower()
        if any(k in inner for k in ("feat", "ft.", "part", "remix", "ao vivo", "live", "acústic", "acoustic")):
            return m.group(0)
        if any(k in inner for k in ("official", "oficial", "video", "vídeo", "clipe", "lyric", "audio",
                                    "áudio", "hd", "hq", "4k", "remaster", "download", "descarga",
                                    "www", "http", ".com", ".br", "mp3", "full", "completa", "extended")):
            return ""
        return m.group(0)
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", _seg, s)
    s = re.sub(r"(?:www\.|https?://)\S+", "", s, flags=re.I)   # links soltos
    s = re.sub(r"\S*\.(?:com|net|org|br)\b", "", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–_.~")
    return s

def _up_meta(p: Path, fallback_name: str = "") -> dict:
    """Lê TUDO que as tags do arquivo têm: título/artista/álbum/duração/capa
    + ano, gênero, nº da faixa e bitrate. Sem tags: cai pro nome do arquivo
    limpo ('Artista - Título') — o resto a busca online completa."""
    title = artist = album = genre = ""
    year = 0
    dur = 0.0
    bitrate = 0
    track_no = 0
    art = None
    m = None
    try:
        from mutagen import File as MFile
        try:
            m = MFile(str(p), easy=True)
            if m is not None:
                dur = float(getattr(m.info, "length", 0) or 0)
                bitrate = int(getattr(m.info, "bitrate", 0) or 0)
                def _g(k):
                    v = (m.get(k) or [""])[0]
                    return str(v) if v else ""
                title, artist, album, genre = _g("title"), _g("artist"), _g("album"), _g("genre")
                dt = _g("date") or _g("originaldate")
                y = re.search(r"(\d{4})", dt)
                if y:
                    year = int(y.group(1))
                tn = _g("tracknumber")
                t2 = re.match(r"(\d+)", tn)
                if t2:
                    track_no = int(t2.group(1))
        except Exception:
            m = None            # stream de áudio ilegível — as tags podem salvar
        if m is not None:
            try:
                pics = getattr(m, "pictures") or []   # FLAC/ogg
                if pics:
                    art = bytes(pics[0].data)
            except Exception:
                pass
        if not title or not artist or art is None:
            try:
                from mutagen.id3 import ID3
                tags = ID3(str(p))
                _tg = lambda k: (str(tags.get(k)) if tags.get(k) else "")
                if not title:
                    title = _tg("TIT2")
                if not artist:
                    artist = _tg("TPE1")
                if not album:
                    album = _tg("TALB")
                if not genre:
                    genre = _tg("TCON")
                if not year:
                    y = re.search(r"(\d{4})", _tg("TDRC") or _tg("TYER"))
                    if y:
                        year = int(y.group(1))
                if not track_no:
                    t2 = re.match(r"(\d+)", _tg("TRCK"))
                    if t2:
                        track_no = int(t2.group(1))
                if art is None:
                    apic = tags.getall("APIC")
                    if apic:
                        art = bytes(apic[0].data)
            except Exception:
                pass
        if art is None:
            try:
                from mutagen.mp4 import MP4
                mp4 = MP4(str(p))
                tags = mp4.tags or {}
                covr = tags.get("covr")
                if covr:
                    art = bytes(covr[0])
                if not genre:
                    genre = (tags.get("©gen") or [""])[0]
                if not year:
                    y = re.search(r"(\d{4})", str((tags.get("©day") or [""])[0] or ""))
                    if y:
                        year = int(y.group(1))
            except Exception:
                pass
    except Exception:
        pass
    stem = _clean_file_stem(Path(fallback_name).stem if fallback_name else p.stem)
    from_tags = bool(title)
    if not title:
        if " - " in stem:
            a, _, t = stem.partition(" - ")
            artist = artist or a.strip()
            title = t.strip() or stem
        else:
            title = stem
    return {"title": title[:200], "artist": (artist or "Minhas músicas")[:200],
            "album": album[:200], "dur": round(dur, 1), "art": art, "from_tags": from_tags,
            "year": year, "genre": genre[:80], "track_no": track_no, "bitrate": bitrate}

UP_DIR_MAX_MB = 10000   # teto de 10 GB para biblioteca própria

@app.post("/api/uploads")
def upload_tracks(request: Request, files: list[UploadFile] = File(...)):
    """Importa arquivos de áudio do computador para a biblioteca (dedupe por
    conteúdo: o mesmo arquivo enviado 2x não duplica)."""
    _require_admin(request)
    import hashlib
    added, dup, errs = 0, 0, []
    imported = []   # faixas novas no formato do frontend (pra jogar na playlist aberta)
    for uf in files[:20]:
        name = uf.filename or "audio"
        ext = Path(name).suffix.lower()
        if ext not in UP_EXT:
            errs.append(f"{name}: formato não suportado")
            continue
        try:
            used_mb = sum(f.stat().st_size for f in UP_DIR.iterdir() if f.is_file()) / (1024 * 1024)
            if used_mb > UP_DIR_MAX_MB:
                errs.append("Cota de armazenamento de uploads esgotada (limite 10 GB)")
                break
        except Exception:
            pass
        # grava em streaming (1MB por vez): não segura 300MB na RAM durante o upload
        tmp = UP_DIR / f".{uuid.uuid4().hex[:10]}.part"
        h = hashlib.sha1()
        size = 0
        too_big = False
        try:
            with tmp.open("wb") as out:
                while True:
                    chunk = uf.file.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > UP_MAX_MB * 1048576:
                        too_big = True
                        break
                    h.update(chunk)
                    out.write(chunk)
        except Exception as e:
            errs.append(f"{name}: {str(e)[:80]}")
            tmp.unlink(missing_ok=True)
            continue
        finally:
            try:
                uf.file.close()
            except Exception:
                pass
        if too_big:
            tmp.unlink(missing_ok=True)
            errs.append(f"{name}: passa de {UP_MAX_MB} MB")
            continue
        uid = "up_" + h.hexdigest()[:10]
        p = UP_DIR / f"{uid}{ext}"
        with db() as con:
            if con.execute("SELECT 1 FROM uploads WHERE id=?", (uid,)).fetchone():
                dup += 1
                tmp.unlink(missing_ok=True)
                continue
        try:
            os.replace(tmp, p)
            meta = _up_meta(p, fallback_name=name)
            # tags incompletas → busca Deezer + iTunes e adota o melhor candidato
            guess_used = False
            ar = "" if meta["artist"] == "Minhas músicas" else meta["artist"]
            if meta["title"] and (not meta["album"] or not ar or not meta["art"]
                                  or not meta["from_tags"] or not meta["year"]):
                cands = _meta_candidates(meta["title"], ar, meta["dur"])
                g = cands[0] if cands else None
                if g:
                    if not meta["from_tags"]:
                        # nome veio do ARQUIVO (sem tags): adota o oficial da busca
                        if g["title"]:
                            meta["title"] = g["title"]
                        if g["artist"]:
                            meta["artist"] = g["artist"]
                        if g["album"]:
                            meta["album"] = g["album"]
                        if g.get("year"):
                            meta["year"] = g["year"]
                        if not meta["art"]:
                            meta["art"] = _dl_art(g["art_url"])
                        # grava as tags oficiais no arquivo (fica certo pra sempre)
                        _up_write_tags(p, title=meta["title"], artist=meta["artist"],
                                       album=meta["album"], year=meta["year"],
                                       genre=meta["genre"])
                    else:
                        # tags existem: só preenche o que falta
                        if not meta["album"] and g["album"]:
                            meta["album"] = g["album"]
                        if not ar and g["artist"]:
                            meta["artist"] = g["artist"]
                        if not meta["year"] and g.get("year"):
                            meta["year"] = g["year"]
                        if not meta["art"]:
                            meta["art"] = _dl_art(g["art_url"])
                    guess_used = True
            art = None
            if meta["art"]:
                try:
                    ap = UP_DIR / f"{uid}.art.jpg"
                    ap.write_bytes(meta["art"])
                    art = str(ap.relative_to(DATA))
                except Exception:
                    art = None
            with db() as con:
                con.execute("""INSERT INTO uploads (id, file, title, artist, album, dur, art, added, year, genre)
                               VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?,?)""",
                            (uid, str(p.relative_to(DATA)), meta["title"], meta["artist"],
                             meta["album"], meta["dur"], art, meta["year"], meta["genre"]))
            added += 1
            imported.append({"videoId": uid, "title": meta["title"], "artist": meta["artist"],
                             "album": meta["album"], "thumbnail": f"/api/local/{uid}/art" if art else ""})
            print(f"⬆️ upload: {meta['title']} — {meta['artist']} ({size / 1048576:.1f} MB)"
                  + (" · metadados via iTunes" if guess_used else ""))
        except Exception as e:
            try:
                p.unlink(missing_ok=True)
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            errs.append(f"{name}: {str(e)[:80]}")
    if not added and not dup:
        raise HTTPException(400, "; ".join(errs) or "nada pra importar")
    return {"ok": True, "added": added, "duplicates": dup, "errors": errs, "tracks": imported}

@app.get("/api/uploads")
def list_uploads():
    with db() as con:
        rows = con.execute(
            "SELECT id,title,artist,album,dur,art,year,genre FROM uploads ORDER BY added DESC, id").fetchall()
    return [{"id": r["id"], "title": r["title"], "artist": r["artist"],
             "album": r["album"], "dur": r["dur"], "art": bool(r["art"]),
             "year": r["year"] or 0, "genre": r["genre"] or ""} for r in rows]

@app.get("/api/uploads/guess")
def upload_guess(title: str = "", artist: str = ""):
    """Sugestão de metadados online (Deezer + iTunes): capa, artista, álbum, ano."""
    if len((title or "").strip()) < 2:
        raise HTTPException(400, "título curto demais pra buscar")
    out = _meta_candidates(title, artist)
    if not out:
        return {"ok": True, "results": []}
    return {"ok": True, "results": out}

@app.post("/api/uploads/{uid}/edit")
def edit_upload(uid: str, body: dict, request: Request):
    """Edita título/artista/álbum do upload: grava no banco E nas tags do
    arquivo (MP3/MP4/FLAC), baixa e embute a capa se vier art_url."""
    _require_admin(request)
    if not UP_RE.match(uid):
        raise HTTPException(400, "id inválido")
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON inválido")
    title = str(body.get("title") or "").strip()[:200]
    artist = str(body.get("artist") or "").strip()[:200]
    album = str(body.get("album") or "").strip()[:200]
    genre = str(body.get("genre") or "").strip()[:80]
    try:
        year = int(str(body.get("year") or "0")[:4] or 0)
    except Exception:
        year = 0
    if year and not 1000 <= year <= 2100:
        year = 0
    art_url = str(body.get("art_url") or "").strip()
    if not title:
        raise HTTPException(400, "título não pode ficar vazio")
    with db() as con:
        rec = con.execute("SELECT file, art FROM uploads WHERE id=?", (uid,)).fetchone()
        if not rec:
            raise HTTPException(404, "upload não encontrado")
    p = DATA / rec["file"]
    if not p.is_file():
        raise HTTPException(404, "arquivo sumiu do disco")
    art_bytes = _dl_art(art_url) if art_url else None
    art_col = rec["art"]
    if art_bytes:
        try:
            ap = UP_DIR / f"{uid}.art.jpg"
            ap.write_bytes(art_bytes)
            art_col = str(ap.relative_to(DATA))
        except Exception:
            art_bytes = None
    _up_write_tags(p, title=title, artist=artist, album=album, art=art_bytes,
                   year=year, genre=genre)
    with db() as con:
        con.execute("UPDATE uploads SET title=?, artist=?, album=?, art=?, year=?, genre=? WHERE id=?",
                    (title, artist, album, art_col, year, genre, uid))
    return {"ok": True, "title": title, "artist": artist, "album": album,
            "year": year, "genre": genre,
            "thumbnail": f"/api/local/{uid}/art" if art_col else ""}

@app.delete("/api/uploads/{uid}")
def delete_upload(uid: str, request: Request):
    _require_admin(request)
    if not UP_RE.match(uid):
        raise HTTPException(400, "id inválido")
    with db() as con:
        rec = con.execute("SELECT file, art FROM uploads WHERE id=?", (uid,)).fetchone()
        if not rec:
            raise HTTPException(404, "upload não encontrado")
        con.execute("DELETE FROM favorites WHERE video_id=?", (uid,))
        con.execute("DELETE FROM history WHERE video_id=?", (uid,))
        con.execute("DELETE FROM playlist_items WHERE video_id=?", (uid,))
        con.execute("DELETE FROM uploads WHERE id=?", (uid,))
    for fn in {rec["file"], rec["art"]}:
        if fn:
            try:
                (DATA / fn).unlink(missing_ok=True)
            except Exception:
                pass
    return {"ok": True}

@app.get("/api/local/{uid}")
def stream_local(uid: str, request: Request):
    """Toca um upload direto do disco, com Range — mesma qualidade de resposta
    do /api/stream, só que sem encostar na internet."""
    if not UP_RE.match(uid):
        raise HTTPException(400, "id inválido")
    with db() as con:
        rec = con.execute("SELECT file FROM uploads WHERE id=?", (uid,)).fetchone()
    if not rec:
        raise HTTPException(404, "upload não encontrado")
    p = (DATA / rec["file"]).resolve()
    if UP_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return _serve_file(p, request.headers.get("range"))

@app.get("/api/local/{uid}/art")
def local_art(uid: str):
    if not UP_RE.match(uid):
        raise HTTPException(400, "id inválido")
    with db() as con:
        rec = con.execute("SELECT art FROM uploads WHERE id=?", (uid,)).fetchone()
    if not rec or not rec["art"]:
        raise HTTPException(404, "sem capa embutida")
    p = (DATA / rec["art"]).resolve()
    if p.parent != UP_DIR.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})

# ---------- Manutenção do yt-dlp ----------
def _print_version_note():
    """Avisa se o yt-dlp estiver velho demais (o YouTube quebra versões antigas o tempo todo)."""
    try:
        v = yt_dlp.version.__version__
        parts = [int(x) for x in v.split(".")[:3]]
        idade = (datetime.date.today() - datetime.date(*parts)).days
        if idade > 90:
            print(f"\n⚠️ Seu yt-dlp está DESATUALIZADO ({v}, {idade} dias).\n"
                  f"   O YouTube muda a API toda semana — atualize: pip install -U yt-dlp\n")
        else:
            print(f"  yt-dlp {v} ✓")
    except Exception:
        pass

def _auto_update_ytdlp():
    def run():
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                               capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                print("🔄 yt-dlp atualizado. Reinicie o app para usar a nova versão.")
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()

def _probe_search():
    """Testa a busca do YT Music no boot: se está quebrada, o log diz POR QUÊ
    (o 'falhou ()' vazio do meio da sessão não ajuda ninguém a diagnosticar)."""
    def run():
        try:
            yt.search("teste", filter="songs", limit=1, ignore_spelling=False)
            print("  🔎 busca YT Music ✓")
        except Exception as e:
            print(f"\n  ⚠️ Busca via YT Music FALHOU no boot ({type(e).__name__}: {str(e) or 'sem mensagem'})")
            print("     As buscas vão usar o plano B (yt-dlp, um pouco mais lento). Causas comuns:")
            print("       - firewall/antivírus bloqueando music.youtube.com")
            print("       - rede/DNS com problema para alcançar o Google")
            print("       - ytmusicapi desatualizado → pip install -U ytmusicapi\n")
    threading.Thread(target=run, daemon=True).start()

# ---- "Pre-tests" de arranque ----------------------------------------------
# Mede os clients com um vídeo-canário ~6s depois de subir (e para se alguém já
# estiver tocando). O resultado decide a ORDEM dos clients e se o solucionador
# JS remoto é necessário — nas medições ele custava ~3s por faixa.
# Desligue com PROBE_ON_BOOT=0 (útil em testes automatizados).
if os.environ.get("PROBE_ON_BOOT", "1") != "0":
    threading.Thread(target=_probe_boot, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    HOST = os.environ.get("HOST", "0.0.0.0")   # 0.0.0.0 = abre pra rede local (celular, TV etc.)
    PORT = int(os.environ.get("PORT", "8787"))
    _print_version_note()
    _probe_search()
    if AUTO_UPDATE_YTDLP:
        print("🔄 AUTO_UPDATE_YTDLP=1 → atualizando o yt-dlp em segundo plano...")
        _auto_update_ytdlp()
    if SETTINGS.get("cloudflare_tunnel") or os.environ.get("ENABLE_TUNNEL") == "1" or os.environ.get("CLOUDFLARE_TUNNEL") == "1" or "--tunnel" in sys.argv:
        print("  ☁️  Iniciando Túnel Cloudflare em segundo plano...")
        _start_cloudflared_tunnel(PORT)
    print("\n  🎵 StremMusic rodando — build 2026-10-25.2")
    print("     (2026-10-25.2: PRE-TESTS no arranque — mede os clients e decide o solucionador JS ·")
    print("      EJS virou plano B: -4,1s medidos por faixa · 1º client sozinho na frente, paralelo só no resgate ·")
    print("      layout automático, modo noturno e modo leve na interface)")
    print("     (2026-10-25.1: cache não guarda recorte de Range · servidor PO Token em :4416 · arranque sem import-string)")
    print(f"  💾 cache local: {'ativo' if SETTINGS.get('cache_enabled') else 'desativado'}"
          f" — {SETTINGS.get('cache_limit_mb')} MB em {CACHE_DIR}")
    print(f"  🔐 fechadura: {'LIGADA — só quem pareou pelo QR entra' if SETTINGS.get('pairing_required') else 'desligada (padrão)'}"
          f" · painel de pareamento (QR pro celular): http://127.0.0.1:{PORT}/painel")
    print(f"  🔑 PO Token (bgutil): {'ativo — 403 do YouTube mitigado' if _POT_PLUGIN_OK else 'INDISPONÍVEL (pip install bgutil-ytdlp-pot-provider)'}")
    if HOST in ("0.0.0.0", "::"):
        try:
            _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _s.connect(("8.8.8.8", 80))          # não envia nada: só descobre o IP local
            _ip = _s.getsockname()[0]
            _s.close()
        except Exception:
            _ip = "127.0.0.1"
        print(f"  📶 no PC:      http://127.0.0.1:{PORT}")
        print(f"  📶 na rede:    http://{_ip}:{PORT}   ← use esse no celular/outros aparelhos")
        print("     (se não abrir, autorize o Python/Node no Firewall do Windows)")
    else:
        print(f"  🌐 http://127.0.0.1:{PORT}")
    print()
    # reload só com RELOAD=1 (desenvolvimento): em servidor ligado 24/7 o watcher
    # é um risco a mais (reinicia no meio de um stream) e consome memória à toa
    if os.environ.get("RELOAD") == "1":
        # reload exige o import-string (o watcher reimporta o módulo pelo nome)
        uvicorn.run("app:app", host=HOST, port=PORT, reload=True)
    else:
        # Objeto em vez do import-string: `uvicorn.run("app:app")` reimporta o
        # módulo PELO NOME e depende de a pasta do app estar no sys.path. Em
        # Python portátil/embutido (._pth, `python -P`, PYTHONSAFEPATH=1) ela não
        # está e o app morre com "Error loading ASGI app. Could not import
        # module app" antes de abrir a porta. Passando o objeto, já importado
        # aqui em cima, isso não pode mais acontecer.
        uvicorn.run(app, host=HOST, port=PORT)
