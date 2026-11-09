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
import os, sys, json, re, uuid, shutil, socket, sqlite3, time, threading, subprocess, datetime, random, secrets, ipaddress

# Garante que o console Windows nunca quebre em cp1252 com emojis ou caracteres especiais
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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
BGUTIL_SERVER_DIR = Path.home() / "bgutil-ytdlp-pot-provider" / "server"
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
    p1 = DATA / "bin" / bin_name
    if p1.is_file():
        return p1
    p2 = BASE / bin_name
    if p2.is_file():
        return p2
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

            for line in iter(proc.stdout.readline, ""):
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if m:
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
                    break

            proc.wait()
        except Exception as e:
            print(f"  ⚠️ Erro no processo do túnel Cloudflare: {e}")
        finally:
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

_LOCKED_HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>StremMusic · Acesso Protegido</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#020617;color:#f1f5f9;font-family:'Outfit',system-ui,-apple-system,sans-serif;display:flex;
  flex-direction:column;align-items:center;justify-content:center;min-height:100vh;min-height:100dvh;
  text-align:center;padding:24px;-webkit-font-smoothing:antialiased}
  .box{background:rgba(15,23,42,.65);border:1px solid rgba(255,255,255,.08);border-radius:24px;padding:36px 28px;max-width:420px;width:100%;box-shadow:0 24px 48px rgba(0,0,0,.5)}
  .icon{width:56px;height:56px;border-radius:18px;background:rgba(56,189,248,.1);border:1px solid rgba(56,189,248,.25);color:#38bdf8;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:24px}
  h1{font-size:22px;font-weight:700;letter-spacing:-.4px;margin-bottom:10px;color:#fff}
  p{color:#94a3b8;font-size:14px;line-height:1.6;margin-bottom:20px}
  code{background:rgba(255,255,255,.06);color:#e2e8f0;padding:2px 8px;border-radius:6px;font-size:12.5px}
  form{display:flex;gap:8px;margin-top:8px}
  input{flex:1;background:rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.12);color:#fff;border-radius:12px;padding:12px 14px;font-size:14px;outline:none}
  input:focus{border-color:#38bdf8}
  button{background:linear-gradient(135deg,#38bdf8,#818cf8);color:#fff;border:0;border-radius:12px;padding:12px 18px;font-size:14px;font-weight:700;cursor:pointer}
</style></head>
<body><div class="box">
  <div class="icon">🔒</div>
  <h1>Acesso restrito a pareados</h1>
  <p>Escaneie o QR Code no painel do computador (<code>http://localhost:8787/painel</code>) ou digite a chave de acesso abaixo:</p>
  <form method="GET" action="/">
    <input name="sk" placeholder="Chave de acesso…" autocomplete="off" required>
    <button type="submit">Entrar</button>
  </form>
</div></body></html>"""

_PUBLIC_PWA_PATHS = {
    "/manifest.webmanifest", "/sw.js", "/favicon.ico",
    "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"
}

@app.middleware("http")
async def _pair_guard(request: Request, call_next):
    path = request.url.path
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        client = forwarded.split(",")[0].strip()
    else:
        client = request.client.host if request.client else ""
    local = client in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost") \
        or (client == "testclient" and path.startswith("/painel"))
    if path == "/painel" or path.startswith("/painel/"):
        if not local:  # painel é coisa do dono: nunca atravessa a rede
            return Response("Painel disponível só no PC do dono (localhost).",
                            status_code=403, media_type="text/plain; charset=utf-8")
        return await call_next(request)
    if path in _PUBLIC_PWA_PATHS or not SETTINGS.get("pairing_required") or local:
        return await call_next(request)
    key = _pair_key()
    sk = request.query_params.get("sk")
    if sk and _safe_eq(sk, key):  # 1º acesso pareado: grava o cookie e segue
        resp = await call_next(request)
        try:
            resp.set_cookie("sk", key, max_age=60 * 60 * 24 * 365, samesite="lax", httponly=True)
        except Exception:
            pass
        return resp
    if _safe_eq(request.cookies.get("sk") or "", key):
        return await call_next(request)
    return HTMLResponse(_LOCKED_HTML, status_code=403)

_PAINEL_CSS = """
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#020617;background-image:radial-gradient(ellipse at 50% 0%,rgba(56,189,248,.11),transparent 65%);color:#f1f5f9;font-family:'Outfit',system-ui,-apple-system,sans-serif;padding:32px 20px 60px;min-height:100vh;-webkit-font-smoothing:antialiased}
  .wrap{max-width:940px;margin:0 auto}
  .top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid rgba(255,255,255,.06)}
  .brand{display:flex;align-items:center;gap:14px}
  .logo{width:42px;height:42px;border-radius:13px;background:linear-gradient(135deg,#38bdf8,#818cf8);display:flex;align-items:center;justify-content:center;box-shadow:0 8px 24px rgba(56,189,248,.3);font-size:20px;color:#fff;font-weight:800}
  h1{font-size:22px;font-weight:800;letter-spacing:-.5px;color:#fff}
  h1 span{background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .sub{color:#94a3b8;font-size:13px;margin-top:2px}
  .btn-open{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:#f1f5f9;text-decoration:none;padding:10px 18px;border-radius:99px;font-size:13.5px;font-weight:600;transition:.2s}
  .btn-open:hover{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.2)}
  .controls-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-bottom:24px}
  .ctrl-card{background:rgba(15,23,42,.55);border:1px solid rgba(255,255,255,.07);border-radius:18px;padding:18px 20px;display:flex;flex-direction:column;justify-content:space-between;gap:14px}
  .ctrl-head{display:flex;align-items:center;justify-content:space-between;gap:10px}
  .ctrl-title{font-size:14px;font-weight:700;color:#fff}
  .ctrl-desc{font-size:12.5px;color:#94a3b8;line-height:1.45;margin-top:4px}
  .ctrl-foot{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .badge{padding:5px 12px;border-radius:999px;font-size:11.5px;font-weight:700;letter-spacing:.02em;white-space:nowrap}
  .badge.on{background:rgba(251,113,133,.12);color:#fb7185;border:1px solid rgba(251,113,133,.3)}
  .badge.off{background:rgba(74,222,128,.1);color:#4ade80;border:1px solid rgba(74,222,128,.25)}
  .badge.cf-on{background:rgba(56,189,248,.14);color:#38bdf8;border:1px solid rgba(56,189,248,.35)}
  .badge.cf-off{background:rgba(255,255,255,.04);color:#94a3b8;border:1px solid rgba(255,255,255,.08)}
  button{background:linear-gradient(135deg,#38bdf8,#818cf8);color:#fff;border:0;border-radius:11px;padding:9px 16px;font-size:13px;font-weight:700;cursor:pointer;transition:all .2s;font-family:inherit}
  button:hover{filter:brightness(1.08);transform:translateY(-1px)}
  button.ghost{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.09);color:#e2e8f0}
  button.ghost:hover{background:rgba(255,255,255,.11);border-color:rgba(255,255,255,.18)}
  .key-pill{font-family:ui-monospace,monospace;font-size:12px;color:#94a3b8;background:rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.07);padding:7px 12px;border-radius:10px;cursor:pointer;user-select:all}
  .key-pill b{color:#f8fafc}
  .sect-label{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin:8px 0 12px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}
  .card{background:rgba(15,23,42,.45);border:1px solid rgba(255,255,255,.07);border-radius:20px;padding:22px;display:flex;flex-direction:column;transition:border-color .2s}
  .card:hover{border-color:rgba(255,255,255,.14)}
  .card.cf{background:linear-gradient(180deg,rgba(14,58,84,.35),rgba(15,23,42,.6));border-color:rgba(56,189,248,.35)}
  .tag{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b}
  .card.cf .tag{color:#38bdf8}
  .ip{font-size:19px;font-weight:800;margin:4px 0 10px;color:#fff;letter-spacing:-.3px}
  .url-row{display:flex;align-items:center;gap:6px}
  .url{flex:1;font-family:ui-monospace,monospace;font-size:11.5px;background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:9px 11px;word-break:break-all;cursor:pointer;color:#cbd5e1;transition:.15s}
  .url:hover{border-color:rgba(56,189,248,.4);color:#fff}
  .url[data-c="1"]::after{content:" · copiado ✓";color:#4ade80;font-weight:700}
  .qr{background:#fff;border-radius:16px;padding:12px;margin:16px auto 12px;width:min(196px,100%);box-shadow:0 12px 30px rgba(0,0,0,.35)}
  .qr svg{width:100%;height:auto;display:block}
  .hint{font-size:12.5px;color:#94a3b8;line-height:1.5;text-align:center}
  .spin{display:inline-block;width:16px;height:16px;border:2px solid rgba(56,189,248,.25);border-top-color:#38bdf8;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-3px;margin-right:8px}
  @keyframes sp{to{transform:rotate(360deg)}}
  .foot{margin-top:28px;padding-top:18px;border-top:1px solid rgba(255,255,255,.05);font-size:12.5px;color:#64748b;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px}
  a{color:#38bdf8;text-decoration:none}
"""

@app.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    port = request.url.port or 80
    key = _pair_key()
    on = bool(SETTINGS.get("pairing_required"))
    cards = []
    cf_on = bool(_CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None)
    cf_connecting = cf_on and not _CF_TUNNEL_URL
    if _CF_TUNNEL_URL:
        cf_url = f"{_CF_TUNNEL_URL}/?sk={key}"
        cf_qr = _qr_svg(cf_url)
        cf_qr_html = (f"<div class='qr'>{cf_qr}</div>"
                      "<div class='hint'>📷 Aponte a câmera do celular — abre direto no navegador com HTTPS e opção de instalar o app (PWA)</div>"
                      if cf_qr else "")
        cards.append(
            "<div class='card cf'>"
            "<div class='tag'>☁️ Cloudflare Quick Tunnel · 4G/5G sem VPN</div>"
            "<div class='ip'>Acesso na Rua (HTTPS)</div>"
            f"<div class='url' title='Clique para copiar' onclick=\"this.dataset.c='1';navigator.clipboard.writeText(this.textContent)\">{cf_url}</div>"
            + cf_qr_html +
            "</div>"
        )
    elif cf_on:
        cards.append(
            "<div class='card cf'>"
            "<div class='tag'>☁️ Cloudflare Quick Tunnel</div>"
            "<div class='ip'><span class='spin'></span>Conectando…</div>"
            "<div class='hint' style='text-align:left;margin-top:6px'>Negociando link HTTPS com a Cloudflare. Esta página atualizará sozinha em instantes…</div>"
            "</div>"
        )
    for ip in _candidate_ips():
        tailscale = ip.startswith("100.")
        tag = "Tailscale · VPN P2P" if tailscale else "Rede Local · Mesmo Wi-Fi"
        hint = ("Requer Tailscale ativo no PC e no celular" if tailscale
                else "📷 Aponte a câmera do celular conectado no mesmo Wi-Fi")
        url = f"http://{ip}:{port}/?sk={key}"
        qr = _qr_svg(url)
        qr_html = ("<div class='qr'>" + qr + "</div><div class='hint'>" + hint + "</div>"
                   if qr else
                   "<div class='hint'>QR indisponível: rode <b>pip install qrcode</b></div>")
        cards.append(
            "<div class='card'><div class='tag'>" + tag + "</div>"
            "<div class='ip'>" + ip + ":" + str(port) + "</div>"
            "<div class='url' title='Clique para copiar' onclick=\"this.dataset.c='1';navigator.clipboard.writeText(this.textContent)\">" + url + "</div>"
            + qr_html + "</div>")

    btn = ("<button class='ghost' onclick=\"fetch('/painel/toggle',{method:'POST'}).then(r=>r.json()).then(()=>location.reload())\">"
           + ("Destrancar servidor" if on else "Trancar (só pareados)")
           + "</button>")
    badge = ("<span class='badge on'>🔒 Trancado · só pareados</span>" if on
             else "<span class='badge off'>🔓 Aberto na rede local</span>")
    cf_btn = ("<button class='" + ("ghost" if cf_on else "") + "' onclick=\"this.disabled=true;this.textContent='Aguarde…';fetch('/painel/tunnel/toggle',{method:'POST'}).then(r=>r.json()).then(()=>location.reload())\">"
              + ("Desligar Túnel" if cf_on else "☁️ Ligar Túnel Cloudflare (4G/5G)")
              + "</button>")
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
        "<p class='sub'>Painel local de conexão e pareamento de dispositivos</p></div></div>"
        f"<a class='btn-open' href='http://127.0.0.1:{port}/'>↗ Abrir Player no PC</a>"
        "</div>"
        "<div class='controls-grid'>"
        "<div class='ctrl-card'>"
        "<div><div class='ctrl-head'><span class='ctrl-title'>Acesso na Rua (Cloudflare 4G/5G)</span>" + cf_badge + "</div>"
        "<p class='ctrl-desc'>Gera um link HTTPS gratuito (trycloudflare.com) sem abrir portas no roteador e sem precisar de aplicativos extras.</p></div>"
        "<div class='ctrl-foot'>" + cf_btn + "</div>"
        "</div>"
        "<div class='ctrl-card'>"
        "<div><div class='ctrl-head'><span class='ctrl-title'>Segurança & Pareamento</span>" + badge + "</div>"
        "<p class='ctrl-desc'>Quando trancado, apenas celulares que leram o QR Code (ou possuem a chave) entram no servidor.</p></div>"
        "<div class='ctrl-foot'>" + btn
        + "<button class='ghost' onclick=\"fetch('/painel/rekey',{method:'POST'}).then(r=>r.json()).then(()=>location.reload())\">Trocar chave</button>"
        + f"<span class='key-pill' title='Clique para copiar a chave' onclick=\"navigator.clipboard.writeText('{key}')\">chave: <b>{key}</b></span>"
        "</div></div>"
        "</div>"
        "<div class='sect-label'>Cartões de Conexão (Escaneie com a câmera do celular)</div>"
        "<div class='grid'>" + "".join(cards) + "</div>"
        "<div class='foot'>"
        f"<span>Player local: <a href='http://127.0.0.1:{port}/'>http://127.0.0.1:{port}/</a></span>"
        "<span>No celular: abra pelo QR Code e escolha <b>Adicionar à tela inicial</b> para instalar o PWA</span>"
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
        if parsed.netloc and parsed.netloc != host:
            raise HTTPException(403, "Origem inválida")
    elif referer:
        parsed = urllib.parse.urlparse(referer)
        if parsed.netloc and parsed.netloc != host:
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
    SETTINGS["pair_key"] = secrets.token_urlsafe(9)
    _save_settings()
    return {"pair_key": SETTINGS["pair_key"]}

@app.post("/painel/tunnel/toggle")
def painel_tunnel_toggle(request: Request):
    _check_csrf(request)
    cf_on = bool(_CF_TUNNEL_PROC and _CF_TUNNEL_PROC.poll() is None)
    if cf_on:
        _stop_cloudflared_tunnel()
        SETTINGS["cloudflare_tunnel"] = False
    else:
        port = int(os.environ.get("PORT", "8787"))
        _start_cloudflared_tunnel(port)
        SETTINGS["cloudflare_tunnel"] = True
    _save_settings()
    return {"ok": True, "active": not cf_on}

@app.get("/api/tunnel/status")
def tunnel_status():
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
        CREATE TABLE IF NOT EXISTS favorites (video_id TEXT PRIMARY KEY, title TEXT, artist TEXT, thumbnail TEXT, added_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS history (video_id TEXT PRIMARY KEY, title TEXT, artist TEXT, thumbnail TEXT, played_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS local_playlists (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS playlist_items (playlist_id INTEGER, video_id TEXT, title TEXT, artist TEXT, thumbnail TEXT, position INTEGER, PRIMARY KEY (playlist_id, video_id));
        CREATE TABLE IF NOT EXISTS stations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, seed_id TEXT, seed_title TEXT, seed_artist TEXT, seed_thumb TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS plays (video_id TEXT PRIMARY KEY, n INTEGER DEFAULT 0, last_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS next_map (prev_id TEXT, next_id TEXT, n INTEGER DEFAULT 0, PRIMARY KEY (prev_id, next_id));
        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY, file TEXT UNIQUE, title TEXT, artist TEXT,
            album TEXT, dur REAL, art TEXT, added TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        # migração suave: ano e gênero das faixas importadas (colunas novas)
        for col, typ in (("year", "INTEGER DEFAULT 0"), ("genre", "TEXT DEFAULT ''")):
            try:
                con.execute(f"ALTER TABLE uploads ADD COLUMN {col} {typ}")
            except Exception:
                pass   # coluna já existe
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
PLAYER_CLIENTS = [["android_vr"], ["mweb"], None]
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
    if not _CLIENT_MS:
        return clients_list
    def chave(item):
        i, c = item
        ms = _CLIENT_MS.get(_label(c))
        return (0 if ms is not None else 1, ms or 10 ** 9, i)
    return [c for _, c in sorted(enumerate(clients_list), key=chave)]


def _extract_one_adaptive(video_id: str, clients) -> dict:
    """Caminho enxuto primeiro (~4s mais rápido nas medições); o solucionador JS
    remoto só entra se ele falhar — e só fica ligado depois de falhar seguido."""
    need = _EJS.get("needed")
    if need and (time.time() - float(_EJS.get("at") or 0) < EJS_TTL):
        return _extract_one(video_id, clients, ejs=True)
    t0 = time.time()
    try:
        data = _extract_one(video_id, clients, ejs=False)
        _mark_client_ms(data.get("client"), time.time() - t0)
        with _health_lock:
            _EJS["streak_ok"] = int(_EJS.get("streak_ok") or 0) + 1
            _EJS["streak_fail"] = 0
        if need and _EJS["streak_ok"] >= EJS_ACERTOS_PARA_DESLIGAR:
            _set_ejs(False, "")
        return data
    except Exception as first:
        with _health_lock:
            _EJS["streak_fail"] = int(_EJS.get("streak_fail") or 0) + 1
            _EJS["streak_ok"] = 0
        try:
            data = _extract_one(video_id, clients, ejs=True)
            if _EJS["streak_fail"] >= EJS_FALHAS_PARA_LIGAR:
                _set_ejs(True, f"{_label(clients)} só entregou a URL com o EJS")
            return data
        except Exception:
            raise first


def _probe_engine(full: bool = True) -> dict:
    """Sondagem: mede os clients de verdade (vídeo-canário) e calibra o motor.
    Roda em segundo plano e PARA se alguém começar a tocar — sondagem nunca
    disputa banda com o play do usuário."""
    if not _probe_lock.acquire(blocking=False):
        return {"ok": False, "motivo": "sondagem já em andamento"}
    try:
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
                    _extract_one(canario, clients, ejs=False)
                    dt = time.time() - t0
                    _mark_client_ms(lbl, dt)
                    resultado[lbl] = {"ms": int(dt * 1000), "ok": True}
                except Exception as e:
                    resultado[lbl] = {"ms": None, "ok": False, "erro": str(e)[:90]}
                    print(f"🧪 {lbl}: enxuto falhou na sondagem ({str(e)[:70]})")
                time.sleep(0.8)                  # não vira rajada no YouTube
        finally:
            _EJS["last_ms"] = {k: v.get("ms") for k, v in resultado.items()}
            _EJS["at"] = time.time()
            if resultado and not any(v["ok"] for v in resultado.values()):
                _set_ejs(True, "nenhum client entregou URL no caminho enxuto")
            elif any(v["ok"] for v in resultado.values()):
                _set_ejs(False, "")
            try:
                SETTINGS["client_ms"] = dict(_CLIENT_MS)
                _save_settings()
            except Exception:
                pass
        print(f"🧪 sondagem concluída: {resultado}")
        return {"ok": True, "resultado": resultado, "ejs": _EJS.get("needed")}
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

def _save_settings():
    try:
        SETTINGS_FILE.write_text(json.dumps(SETTINGS, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception as e:
        print(f"⚠️ não consegui salvar settings.json: {e}")

_load_settings()

# Vídeos com pedido de stream ATIVO. Suporta vários aparelhos na rede ao mesmo
# tempo: a troca de música num aparelho NÃO cancela a extração do outro — uma
# extração "urgent" só é cancelada quando NINGUÉM está mais pedindo aquele vídeo.
_wanted = set()
_wanted_lock = threading.Lock()

def _want(video_id: str):
    with _wanted_lock:
        _wanted.add(video_id)

def _unwant(video_id: str):
    with _wanted_lock:
        _wanted.discard(video_id)

_last_ok = {"label": None}    # último client que deu certo → tenta primeiro na próxima

def _prefer_last_ok(clients):
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
    opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    if clients:
        opts["extractor_args"]["youtube"]["player_client"] = clients
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if not info:
        raise RuntimeError(f"sem dados ({_label(clients)})")
    hdrs = info.get("http_headers") or {}
    url = info.get("url") or ""
    if _is_direct_media(url, info.get("protocol") or ""):
        return {"url": url, "headers": hdrs, "client": _label(clients),
                "alts": _audio_alts(info.get("formats"), url)}
    # O formato escolhido veio como manifest (HLS/DASH): procura um formato de
    # áudio direto na lista; sem nenhum, falha para a onda tentar o próximo client.
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
        raise RuntimeError(f"só manifest HLS/DASH disponível ({_label(clients)})")
    cands.sort(key=lambda f: (f.get("ext") == "m4a", f.get("abr") or f.get("tbr") or 0), reverse=True)
    f = cands[0]
    return {"url": f["url"], "headers": f.get("http_headers") or hdrs, "client": _label(clients),
            "alts": _audio_alts(cands[1:], f["url"])}

def _extract(video_id: str, avoid=(), urgent: bool = False) -> dict:
    avoid = set(avoid)
    # evita clients marcados como ruins para este vídeo (403 recente no stream)
    _full = _clients_full()   # web_safari/tv só entram com cookies (gemini 3.8, log real)
    clients_list = [c for c in _full if _label(c) not in avoid] or list(_full)
    clients_list = _prefer_last_ok(clients_list)   # o client que funcionou por último vai na frente
    clients_list, de_folga = _client_healthy(clients_list)
    if de_folga:
        print(f"ℹ️ {video_id}: clients de folga nesta rodada: {', '.join(de_folga)}")
    if not clients_list:            # teoricamente impossível (sondagem libera 1x); por segurança
        clients_list = list(_full)
    # quem a sondagem mediu mais rápido vai na frente (empate = ordem original)
    clients_list = _ordered_clients(clients_list)
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
        # com 1 client na frente, o teto curto devolve o controle rápido se ele
        # morrer; com EJS ligado a extração é sabidamente lenta, então usa o teto
        teto = wave_to if (wi or _EJS.get("needed")) else min(wave_to, 12)
        futs = {_extract_pool.submit(_extract_one_adaptive, video_id, c): c for c in wave}
        try:
            for f in cf.as_completed(futs, timeout=min(teto, remaining)):
                try:
                    data = f.result()
                    _last_ok["label"] = data["client"]
                    _client_mark_ok(data["client"])
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
    with _warm_lock:
        for v in ids[:50]:
            v = str(v or "")
            if VID_RE.match(v) and v not in _warm_set:
                _warm_set.add(v)
                _warm_q.append(v)

def prewarm(video_ids: list[str]):
    # Apenas a 1ª sugestão corre em paralelo (é a que o usuário mais clica — e
    # thread própria: o pré-carregamento espera a extração, não pode ocupar
    # worker do pool). As demais vão pra fila sequencial de pré-cache: 3+
    # extrações simultâneas no mesmo segundo esbarravam na barreira anti-bot
    # do YouTube e derrubavam TODAS as resoluções (os 502 em cascata de out/2026).
    if not video_ids:
        return
    threading.Thread(target=_safe_prewarm, args=(video_ids[0],), daemon=True).start()
    if len(video_ids) > 1:
        _enqueue_warm(video_ids[1:])

# Sessão HTTP reaproveitada para os trechos de áudio
session = _mount_ipv4(requests.Session())

# ---------- Cache de áudio em disco (LRU, limite ajustável no painel) ----------
CACHE_DIR = DATA / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")     # formato de videoId do YouTube

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

def _cache_paths(vid: str):
    return CACHE_DIR / f"{vid}.audio", CACHE_DIR / f"{vid}.meta.json"

def _cache_write_meta(vid: str, m: dict):
    _, meta_p = _cache_paths(vid)
    tmp = CACHE_DIR / f"{vid}.{uuid.uuid4().hex[:6]}.mtmp"
    tmp.write_text(json.dumps(m), encoding="utf-8")
    os.replace(tmp, meta_p)

def _cache_lookup(vid: str):
    """Meta válido (áudio existe e o tamanho bate) ou None — se corrompido, apaga."""
    if not VID_RE.match(vid or ""):
        return None
    audio, meta_p = _cache_paths(vid)
    if not audio.is_file() or not meta_p.is_file():
        return None
    try:
        m = json.loads(meta_p.read_text(encoding="utf-8"))
        if int(m.get("size") or 0) != audio.stat().st_size or audio.stat().st_size <= 0:
            raise ValueError("tamanho divergente")
        return m
    except Exception:
        try:
            meta_p.unlink(missing_ok=True)
        except Exception:
            pass
        return None

def _cache_limit_bytes() -> int:
    return max(50, int(SETTINGS.get("cache_limit_mb") or 512)) * 1024 * 1024

def _cache_evict():
    """Apaga itens até caber no limite. Com o cache INTELIGENTE ligado, a ordem de
    saída não é só "o mais antigo": cada repetição prorroga a permanência (música
    que você volta a ouvir fica) e favoritos só saem em último caso. Limpa órfãos."""
    # leitura de banco + varredura do disco FORA do lock (não travar streams durante o scan)
    limit = _cache_limit_bytes()
    smart = bool(SETTINGS.get("smart_cache", True))
    favs, plays_map = set(), {}
    if smart:
        try:
            with db() as con:
                favs = {r[0] for r in con.execute("SELECT video_id FROM favorites")}
                plays_map = {r[0]: int(r[1] or 0) for r in con.execute("SELECT video_id, n FROM plays")}
        except Exception:
            favs, plays_map = set(), {}
    entries = []   # (at_ajustado, at_bruto, vid, audio, meta_p, size)
    for meta_p in CACHE_DIR.glob("*.meta.json"):
        vid = meta_p.name[:-len(".meta.json")]
        audio, _ = _cache_paths(vid)
        if not audio.is_file():
            meta_p.unlink(missing_ok=True)
            continue
        try:
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            at = float(m.get("at") or 0)
            # cada repetição "rejuvenesce" o arquivo em 3h (teto 48 plays → 6 dias)
            adj = at + (min(int(plays_map.get(vid, 0)), 48) * 3 * 3600 if smart else 0)
            entries.append((adj, at, vid, audio, meta_p, audio.stat().st_size))
        except Exception:
            meta_p.unlink(missing_ok=True)
    total = sum(e[5] for e in entries)

    with _disk_lock:
        dead = set()

        def _kill(e):
            nonlocal total
            try:
                e[3].unlink()
                e[4].unlink()
            except Exception:
                pass
            total -= e[5]
            dead.add(e[2])

        # passada 1: não-favoritos (mais antigos primeiro, já com o bônus de replay)
        pool = [e for e in entries if not (smart and e[2] in favs)]
        for e in sorted(pool, key=lambda x: x[0]):
            if total <= limit:
                break
            _kill(e)
        # passada 2: mesmo favorito sai se NADA mais resolver (mais antigo primeiro)
        if total > limit:
            for e in sorted([e for e in entries if e[2] not in dead], key=lambda x: x[1]):
                if total <= limit:
                    break
                _kill(e)
        now = time.time()
        for junk in CACHE_DIR.glob("*.part"):
            try:
                if now - junk.stat().st_mtime > 3600:
                    junk.unlink()
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
                return None
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

    def _touch():
        m2 = dict(m)
        m2["at"] = time.time()
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
        try:
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
_warm_worker_on = False

def _ensure_warm_worker():
    global _warm_worker_on
    with _warm_lock:
        if _warm_worker_on:
            return
        _warm_worker_on = True
    threading.Thread(target=_warm_worker, daemon=True).start()

def _warm_worker():
    while True:
        with _warm_lock:
            vid = _warm_q.pop(0) if _warm_q else None
        if vid is None:
            with _warm_lock:
                if not _warm_q:
                    _warm_set.clear()
            time.sleep(1.0)
            continue
        try:
            _prefetch_audio(vid)   # pula sozinho os que já estão no cache
        except Exception:
            pass

# ---------- Cache inteligente (aprende o que você ouve e protege o que você ama) ----------
# plays     → quantas vezes cada faixa tocou (base da eviction esperta)
# next_map  → transições "quem costuma vir depois de quem" (aprendido com suas trocas de faixa)
# sessão    → quanto o cache te poupou de YouTube agora
_last_stream_lock = threading.Lock()
_last_stream_id = None
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

def _bump_next(prev_id: str, next_id: str):
    """Anota a transição prev→next (o que você costuma ouvir em seguida)."""
    try:
        with db() as con:
            con.execute("""INSERT INTO next_map(prev_id, next_id, n) VALUES (?,?,1)
                           ON CONFLICT(prev_id, next_id) DO UPDATE SET n = n + 1""",
                        (prev_id, next_id))
    except Exception:
        pass

def _smart_warm_after(video_id: str):
    """Pré-guarda no cache o que costuma vir DEPOIS desta faixa — mesmo fora da fila."""
    if not SETTINGS.get("learn_next", True) or not SETTINGS.get("cache_enabled") \
            or not SETTINGS.get("prefetch_next", True):
        return
    try:
        with db() as con:
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
    proxies = {"https": YT_PROXY} if YT_PROXY else None
    resp = session.get(alt_url or data["url"], headers=headers, stream=True,
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

def _known_blend(tracks: list, seed_id: str, k: int = 4) -> list:
    """Mistura até k faixas que você JÁ CONHECE (favoritos; se faltar, histórico)
    no meio do mix do YouTube — é o que faz a estação ficar com conhecidas + descobertas."""
    try:
        with db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT video_id, title, artist, thumbnail FROM favorites ORDER BY RANDOM() LIMIT ?", (k,))]
            if len(rows) < k:
                got = {r["video_id"] for r in rows} | {seed_id}
                for r in con.execute("SELECT video_id, title, artist, thumbnail FROM history ORDER BY RANDOM() LIMIT ?",
                                     (k * 3,)):
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
def radio(video_id: str, known: int = 0):
    """Mix RD da semente. Com ?known=1 (usado pelas ESTAÇÕES) entra uma pitada
    de faixas que você já conhece no meio das descobertas."""
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    known = bool(known)
    key = (video_id, known)
    hit = _radio_cache.get(key)
    if hit and time.time() - hit[0] < 6 * 3600:
        return {"id": f"RD{video_id}", "title": "Rádio", "tracks": hit[1], "cached": True}
    try:
        tracks = _pl_pool.submit(_radio_fetch, video_id).result(timeout=75)
    except Exception as e:
        raise HTTPException(502, _friendly_error(e))
    if known:
        tracks = _known_blend(tracks, video_id)
    _radio_cache[key] = (time.time(), tracks)
    if len(_radio_cache) > 100:
        for k_old, _ in sorted(_radio_cache.items(), key=lambda kv: kv[1][0])[:50]:
            _radio_cache.pop(k_old, None)
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
_EMBEDDED_ASSETS: dict[str, str] = {
    "manifest.webmanifest": "ewogICJuYW1lIjogIlN0cmVtTXVzaWMiLAogICJzaG9ydF9uYW1lIjogIlN0cmVtTXVzaWMiLAogICJkZXNjcmlwdGlvbiI6ICJQbGF5ZXIgZGUgbcO6c2ljYSBkbyBZb3VUdWJlIGNvbSBjYWNoZSBsb2NhbCwgZG93bmxvYWRzIGUgcGxheWxpc3RzLiIsCiAgImxhbmciOiAicHQtQlIiLAogICJzdGFydF91cmwiOiAiLyIsCiAgInNjb3BlIjogIi8iLAogICJkaXNwbGF5IjogInN0YW5kYWxvbmUiLAogICJvcmllbnRhdGlvbiI6ICJhbnkiLAogICJiYWNrZ3JvdW5kX2NvbG9yIjogIiMwMjA2MTciLAogICJ0aGVtZV9jb2xvciI6ICIjMDIwNjE3IiwKICAiaWNvbnMiOiBbCiAgICB7ICJzcmMiOiAiL2ljb24tMTkyLnBuZyIsICJzaXplcyI6ICIxOTJ4MTkyIiwgInR5cGUiOiAiaW1hZ2UvcG5nIiwgInB1cnBvc2UiOiAiYW55IiB9LAogICAgeyAic3JjIjogIi9pY29uLTUxMi5wbmciLCAic2l6ZXMiOiAiNTEyeDUxMiIsICJ0eXBlIjogImltYWdlL3BuZyIsICJwdXJwb3NlIjogImFueSIgfSwKICAgIHsgInNyYyI6ICIvaWNvbi1tYXNrYWJsZS01MTIucG5nIiwgInNpemVzIjogIjUxMng1MTIiLCAidHlwZSI6ICJpbWFnZS9wbmciLCAicHVycG9zZSI6ICJtYXNrYWJsZSIgfQogIF0KfQo=",
    "icon-192.png": "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",
    "icon-512.png": "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",
    "icon-maskable-512.png": "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAIAAAB7GkOtAAAK2klEQVR42u3d23IUZRSG4RmKqr4l70CB7EhCNIq416uyRIxAgIRNcHfgfZkjPIhFibRhUpnu6X99z1McQ9E9WS+r+Wdm/t6ff80AyHPFJQAQAAAEAIDqrs5dAwAbAABBG8DslYsAYAMAQAAAEAAABACAKhwDBbABACAAAAgAAAIAQCHeCQxgAwAgagNwDBTABgCAAAAgAAAIAAACAIAAANAix0ABbAAARG0APgoCwAYAgAAAIAAACAAAdTgGCmADAEAAABAAAAQAgEK8ExjABgBA1AbgGCiADQAAAQBAAAAQAAAEAAABAKBFjoECpAbAO4EBMnkEBCAAAAgAAAIAgAAAUIhjoAA2AAAEAAABAEAAACjER0EA2AAAiNoAHAMFsAEAIAAACAAAAgCAAAAgAAC0yDFQgNQAeCcwQCaPgAAEAAABAEAAABAAAApxDBTABgCAAAAgAAAIAACF+CgIABsAAFEbgGOgADYAAAQAAAEAQAAAEAAABACAFjkGCpAaAO8EBsjkERCAAAAgAAAIAAACAEAhjoEC2ACgZX/c6FwEEAA0ABAANAAQADQA+DcfBUHZBrz/8tSlABsAkRlYswrAuRuAY6DUbsAH9gCwAZDpd3sACAAaAAgAGgAIABoAAgAaAAIAlRsgA3DGMVBCM3DN8VAEwDuByfTbWnftRAOI5hEQwQ1Y9ywIAQANAAEADQABAA0AAQANgHIcA4U3GnDd0SBsAJDpV6sAAgAaAAIAGgAFeScw/G8Drr/w/wHYACCzARv2AGpvAFQfOv4Ze8nb4QJSNgCOgU7HL8P8e/OsKDdMMRcQ3uQRUPHpP9rv7waBADDd4WKEuYAgALljxQhzAUEAQAMQAMKmifnlGoIAgAYQzTHQRG76shqw5mwoTQfAR0EkctOX5OVGN5vN1p7LAE3yCAgunYFNj4MQANAAEADQABAA0AAQANAAmAzHQBO56UM3YN25IGwAkOlkszuxCiAAkJwBFwEBAA2AyfFO4Ehu+rgNWH/mvwSwAUBmA7bsAQgAaABMhmOgidz0VTVgw7MgbACQ6YU9AAGA5AbIAAIAVgEQANAAEADQABAA0AAYkGOgidz0qTVg0/FQVhIAnwqQyE2fmOdb3eZTDWBsHgHBNBpw07MgBACCGyADCABYBUAAQANAAEADYIkcA03kprfSgC1Hg7ABQKZn9gAEADQABAASGyADDME7gSO56W1mYOvYfwlgA4DMBmzbAxAA0AC4NMdAE7nprTfgpmdB2AAg01N7AAIAGgACABoAAgBJDZABBACsAiAAoAGwAMdAE7npVRuw7XgoFwqATwVI5KYXdbzdbR9pAIvyCAhqNWDHsyAW3wDiX9z+xUS9BnhVUyoAw/275ux39gNDvT3Aq5rzXWno1WxxBq9qsgIw2ovYTwsaQFYA5rPZlH+N/PI93ulG/guuxLziL6bzqvarlV9OAUF9R/YAmnsEtJJXrR8VNAABADSAyrwTuM8rf0HKNmDnibOh2AAgswG73dGuVQABgOAMuAj4NNAec39BMhqw63GQDQDI9MQeIACABiAAgAYgAIAGIACABiAAQOUGyECUq3NvCn1L+WvipnN+Bm49djzUBgBEenzLHiAAgAYgAIAGIACABiAAgAYgAEDhBshAST4NtIdPA4XeDOw5HmoDADI9sgcIAKABCACgAbTKl8L38aXw8K4G7D3y/wE2ACCzAXv2AAEANIA2OQbawzFQuFADPvQ4yAYAZDq0CggAoAEIAKABCACQwf8ECABg+iMAgOnPhPlS+B6+FB4W9NHh6czBYhsAkDn9EQDA9EcAANMfAQBMfwQAMP0RAMD0ZwJ8GmgPnwYKb9s/PPXKqRYAlwB45+h3EUryCAgw/QUAwPRP4kvh+/hSeJjN9h+a/jYAwPRHAADTnzIcA+3hGCjJPn7ouGdMAFwC4PXodxGieAQEmP4CAJj+CABg+iMAgOmPAACmP4X4UvgevhSeBJ88cNzTBgBETn8XAe8DAKMfGwBg+iMAyS9WPyGY/ggAYPojADGvWj8kmP7kaODTQG8/OD3Y78b5g0JOxTn8F+W24540ugG8fgUX+COg5M8ODW8Abb2Oh1gF/IRg+iMAXtBg9BMVAJ8KEMhNrz3975v+LMQxUDD9EQDA9CeJL4VP5KaX9Ol9xz2xAUDk9HcREAAw/WEhPg4ajH5sAIDpjwAApj8CAJj+1ORL4RO56U2787PjntgAIHL6uwgIAJj+IABg+sPFeR8AGP3YAADTHwEATH/K82mgidz0JnzmuCc2AMic/i4CAgCmPwzCdwJHctOnPP0PTH9GCwBg9BPJIyAw/REAwPQniWOgidz0Sfn8wHFPbAAQOf1dBAQATH8QADD9QQDA9IeBeB8AGP3YAADTn6gNwPeDB3LTV+KLnxz3xAYAkdPfRUAAwPQHAQDTHwQATH8QADD9YQzeBwBGP6kBcC4tkJs+qC8d96QRHgHBkqe/i4AAgOkPk+ZL4SO56UNM/3umPzYAMP1BAMD0h8lyDBSMflID4LxaIDd9Kb6657gnbfMIaPVDpPyf6MaBAIDpDwLAiqaJyeUaggAkzhSTyzUEAUicLCaXawj/4Rjo5ObL93c6Y8v0hzEC4PvBp+brH5c8aBxVXModcRmpxyMgGLXHIABg+oMAgOkPAgCmPwgAmP4gAGD6wwB8Gij84xvHPbEBQOz0BwEA0x/q86XwxE//u6Y/NgAw/UEAwPQHAQDTH2pyDJRE3971fe7g+wDIG/0uApzxCAjTHwQATH8QADD9QQDA9AcBANMfCvGl8FT23Q+Oe4INgMjp7yLAeRuAS4DRDzYAMP1BAMD0BwEA0x8EAEx/qMOngQLYAAAQAADK86XwADYAAAQAAAEAoCbHQAFsAAAIAAACAIAAACAAALTOO4EBUgPgGChAJo+AAAQAAAEAQAAAEAAABACA1jkGCmADAEAAACjPR0EA2AAAEAAABACAmhwDBbABACAAAAgAAAIAgAAA0DrvBAZIDYBjoACZPAICEAAABAAAAQBAAAAQAABa5xgogA0AAAEAoDwfBQFgAwBAAAAQAABqcgwUwAYAgAAAIAAACAAAAgBA67wTGCA1AI6BAmTyCAhAAAAQAAAEAAABAEAAAGidY6AANgAABACA8nwUBIANAAABAEAAAKjJMVAAGwAAAgCAAAAgAAAIAACt805ggNQAOAYKkMkjIAABAEAAABAAAAQAAAEAoHWOgQLYAACI2gC8ExjABgCAAAAgAAAIAAB1OAYKYAMAQAAAEAAABAAAAQCgdT4KAiA1AI6BAmTyCAhAAAAQAAAEAAABAEAAAGidY6AANgAAojYA7wQGsAEAIAAACAAAAgBAHY6BAtgAABAAAAQAAAEAQAAAaJ2PggBIDYBjoACZPAICEAAABAAAAQBAAAAQAABa5xgogA0AgKgNwDuBAWwAAAgAAAIAgAAAUIdjoAA2AAAEAAABAEAAABAAAFrnoyAAUgPgGChAJo+AAAQAAAEAQAAAEAAABACA1jkGCmADACBqA/BOYAAbAAACAIAAACAAANThGCiADQAAAQBAAAAQAAAK8U5gABsAAFEbgGOgADYAAAQAAAEAQAAAEAAABACAFjkGCmADACBqA/BREAA2AAAEAAABAEAAAKjDMVAAGwAAAgCAAAAgAAAU4p3AADYAAKI2AMdAAWwAAAgAAAIAQEl/A8lPOMJCnSU1AAAAAElFTkSuQmCC",
}

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
    f = BASE / "icon-192.png"
    if f.is_file():
        return FileResponse(str(f), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
    return Response(content=b"", media_type="image/x-icon")

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
    if not f.is_file():
        raise HTTPException(404, "manifest não encontrado")
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        sk_cookie = request.cookies.get("sk") or request.query_params.get("sk") or ""
        key = _pair_key()
        if sk_cookie and _safe_eq(sk_cookie, key):
            data["start_url"] = f"/?sk={key}"
        return Response(content=json.dumps(data, ensure_ascii=False),
                        media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})
    except Exception:
        return Response(content=f.read_bytes(), media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=3600"})

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    f = BASE / "static" / "sw.js"
    if not f.is_file():
        raise HTTPException(404, "sw.js não encontrado")
    return FileResponse(str(f), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

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
def search(q: str = Query(..., min_length=1), limit: int = Query(SEARCH_LIMIT, ge=1, le=SEARCH_LIMIT)):
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
def artist_tracks(id: str = "", name: str = ""):
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
def resolve(video_id: str, title: str = "", artist: str = ""):
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
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    rng = request.headers.get("range")
    retry = bool(request.query_params.get("r"))   # retry do frontend: ignora cooldown
    # Cache inteligente: anota a transição "o que costuma ouvir depois desta" (o play
    # em si é contado só nas saídas bem-sucedidas, pra retry de erro não inflar)
    first_try = not retry
    if first_try:
        global _last_stream_id
        with _last_stream_lock:
            prev_id, _last_stream_id = _last_stream_id, video_id
        if prev_id and prev_id != video_id and VID_RE.match(prev_id):
            _bump_next(prev_id, video_id)
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
    def _note_fail(d, st):
        # marca o client como ruim pra este vídeo + tira a URL podre do cache
        c = (d or {}).get("client", "")
        if c and c not in tried:
            tried.append(c)
        _mark_bad_client(video_id, c)
        with _cache_lock:
            cur = _stream_cache.get(video_id)
            if cur is not None and cur[1] is d:
                _stream_cache.pop(video_id, None)
    try:
        # No retry (?r= do frontend) evita DE CARA os clients que acabaram de
        # devolver 403 neste vídeo: sem isso o retry repetia o MESMO client
        # queimado (extração inteira de novo + 403 de novo = ~17s perdidos).
        # Só no retry: com 'avoid' o cache de URL é ignorado, e no caminho
        # normal nós QUEREMOS aproveitar a URL pré-aquecida.
        upstream, data = open_upstream(video_id, rng, ignore_cooldown=retry, urgent=True,
                                       avoid=_bad_clients_for(video_id) if retry else ())

        # Escada de contorno pós-403 (cada degrau só roda se o anterior falhou):
        # 1) formato ALTERNATIVO da MESMA extração — o SABR às vezes trava só um itag
        for alt in list((data.get("alts") or []))[:2]:
            if upstream.status_code < 400:
                break
            st = upstream.status_code
            upstream.close()
            _note_fail(data, st)
            print(f"⚠️ HTTP {st} em {video_id} (client {data.get('client')}); tentando formato alternativo do mesmo client...")
            upstream, data = open_upstream(video_id, rng, data=data, alt_url=alt)
        # 2) troca de client (evitando os que já devolveram 403 para este vídeo)
        if upstream.status_code >= 400:
            st = upstream.status_code
            upstream.close()
            _note_fail(data, st)
            print(f"⚠️ HTTP {st} no stream de {video_id} (client {data.get('client')}), trocando de client...")
            upstream, data = open_upstream(video_id, rng, force=True,
                                           avoid=_bad_clients_for(video_id), urgent=True)
        # 3) um formato alternativo do novo client
        for alt in list((data.get("alts") or []))[:1]:
            if upstream.status_code < 400:
                break
            st = upstream.status_code
            upstream.close()
            _note_fail(data, st)
            print(f"⚠️ HTTP {st} em {video_id} (client {data.get('client')}); formato alternativo do novo client...")
            upstream, data = open_upstream(video_id, rng, data=data, alt_url=alt)
        # 4) última cartada no servidor: o 403 do googlevideo costuma ser transitório —
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
        # 5) último recurso: devolve 302 e deixa o NAVEGADOR buscar direto no
        #    googlevideo — a impressão digital TLS/headers do navegador às vezes
        #    passa onde o requests do Python leva 403. Pior cenário: o <audio>
        #    dispara 'error' e o app pula pra próxima faixa (nunca fica mudo).
        if st in (403, 429) and "googlevideo.com" in url:
            print(f"↪️ {video_id}: 302 direto ao googlevideo como último recurso (navegador tenta)")
            if first_try:
                _count_play(video_id)
                _smart_warm_after(video_id)
            return RedirectResponse(url, status_code=302)
        extra = ("\n\n💡 O YouTube recusou o áudio nos clients disponíveis.\n"
                 "    Se persistir: USE_COOKIES=1 com um cookies.txt do navegador,"
                 " ou rode `pip install -U yt-dlp`.") if st in (403, 410) else ""
        raise HTTPException(502, f"Upstream HTTP {st} (clients tentados: {', '.join(tried) or (data or {}).get('client')}).{extra}")

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
        _smart_warm_after(video_id)
    return StreamingResponse(iterate(), status_code=upstream.status_code, headers=passthrough)

# ---------- Configurações (painel Config) ----------
@app.get("/api/settings")
def get_settings():
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
            SETTINGS["cache_limit_mb"] = max(50, min(1024, n))
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
def update_settings(data: dict):
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
def cache_stats():
    items, used = [], 0
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
    return ok

@app.delete("/api/cache")
def cache_clear():
    n = 0
    with _disk_lock:   # não apagar pela metade enquanto um stream promove o cache
        for meta_p in CACHE_DIR.glob("*.meta.json"):
            if _cache_delete(meta_p.name[:-len(".meta.json")]):
                n += 1
        for part in CACHE_DIR.glob("*.part"):
            try:
                part.unlink()
                n += 1
            except Exception:
                pass
    return {"ok": True, "removed": n}

@app.delete("/api/cache/{video_id}")
def cache_delete_one(video_id: str):
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
    with _warm_lock:
        for v in ids[:50]:
            v = str(v or "")
            if VID_RE.match(v) and v not in _warm_set:
                _warm_set.add(v)
                _warm_q.append(v)
                n += 1
    return {"ok": True, "queued": n}

# ---------- Saúde do motor de extração (apontamento do Argon) ----------
# O _client_health só aparecia no terminal; agora o painel VÊ e PODE resetar.
@app.get("/api/clients/health")
def clients_health():
    now = time.time()

    def _state(c):
        lbl = _label(c)
        h = _client_health.get(lbl)
        ms = _CLIENT_MS.get(lbl)
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
            "ejs": _EJS.get("needed"),
            "probe_at": int(_EJS.get("at") or 0) or None,
            "probe_ms": _EJS.get("last_ms"),
            "probe_running": _probe_lock.locked()}

@app.post("/api/clients/probe")
def clients_probe():
    """Roda a sondagem AGORA (mede os clients com o vídeo-canário). Roda em
    segundo plano: a resposta volta na hora e o painel consulta /health."""
    if _probe_lock.locked():
        return {"ok": True, "started": False, "motivo": "já está medindo"}
    threading.Thread(target=_probe_engine, kwargs={"full": True}, daemon=True).start()
    return {"ok": True, "started": True}

@app.post("/api/clients/reset")
def clients_reset():
    """Força TODOS os clients de volta à disputa na próxima faixa (folga zerada,
    'client preferido' esquecido, marcações 403 por vídeo limpas)."""
    with _health_lock:
        n = len(_client_health)
        _client_health.clear()
    with _cache_lock:
        b = len(_bad_clients)
        _bad_clients.clear()
    _last_ok["label"] = None
    print("🩺 saúde dos clients RESETADA pelo painel — todos voltam à disputa")
    return {"ok": True, "cleared": n, "bad_clients_cleared": b}

# ---------- Favoritos ----------
@app.get("/api/favorites")
def list_favorites():
    with db() as con:
        return [track_row(r) for r in con.execute("SELECT * FROM favorites ORDER BY added_at DESC")]

@app.post("/api/favorites/{video_id}")
def add_favorite(video_id: str, title: str = "", artist: str = "", thumbnail: str = ""):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO favorites VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                    (video_id, title, artist, thumbnail))
    return {"ok": True}

@app.delete("/api/favorites/{video_id}")
def del_favorite(video_id: str):
    with db() as con:
        con.execute("DELETE FROM favorites WHERE video_id=?", (video_id,))
    return {"ok": True}

# ---------- Histórico ----------
@app.get("/api/history")
def get_history():
    with db() as con:
        return [track_row(r) for r in con.execute("SELECT * FROM history ORDER BY played_at DESC LIMIT 100")]

@app.post("/api/history/{video_id}")
def add_history(video_id: str, title: str = "", artist: str = "", thumbnail: str = ""):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO history VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
                    (video_id, title, artist, thumbnail))
    return {"ok": True}

@app.delete("/api/history")
def clear_history():
    """Apaga o histórico inteiro (apontamento do Argon: só existia GET/POST)."""
    with db() as con:
        n = con.execute("SELECT COUNT(*) c FROM history").fetchone()["c"]
        con.execute("DELETE FROM history")
    return {"ok": True, "removed": int(n or 0)}

@app.delete("/api/history/{video_id}")
def delete_history_item(video_id: str):
    """Remove UM item do histórico (o registro, não os dados aprendidos de plays/next_map)."""
    with db() as con:
        cur = con.execute("DELETE FROM history WHERE video_id=?", (video_id,))
    return {"ok": True, "removed": max(0, cur.rowcount or 0)}

# ---------- Continuar ouvindo (o que o cache inteligente APRENDEU) ----------
@app.get("/api/smart/next/{video_id}")
def smart_next(video_id: str, limit: int = Query(8, ge=1, le=20)):
    """O que o app aprendeu que você costuma ouvir DEPOIS desta faixa (next_map,
    alimentado pelas suas trocas de faixa reais). Título/artista vêm do histórico/
    favoritos; sem metadado conhecido, a faixa volta com known=false e thumbnail
    derivada do id (o frontend só enfileira as conhecidas)."""
    if not VID_RE.match(video_id):
        raise HTTPException(400, "video_id inválido")
    try:
        with db() as con:
            rows = con.execute(
                "SELECT next_id, n FROM next_map WHERE prev_id=? ORDER BY n DESC LIMIT ?",
                (video_id, limit)).fetchall()
            out = []
            for r in rows:
                nid = r["next_id"]
                m = con.execute("SELECT title, artist, thumbnail FROM history WHERE video_id=?",
                                (nid,)).fetchone()
                if not m:
                    m = con.execute("SELECT title, artist, thumbnail FROM favorites WHERE video_id=?",
                                    (nid,)).fetchone()
                m = dict(m) if m else {}
                out.append({"videoId": nid, "n": int(r["n"] or 0),
                            "title": m.get("title") or "", "artist": m.get("artist") or "",
                            # capa pelo proxy local (mesmo padrão de search/radio —
                            # gemini 3.8: o link direto i.ytimg.com quebrava offline
                            # e com domínio do Google bloqueado)
                            "thumbnail": m.get("thumbnail") or f"/api/thumb/{nid}",
                            "known": bool(m.get("title"))})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _friendly_error(e))
    return {"seed": video_id, "tracks": out}

# ---------- Playlists locais ----------
@app.get("/api/playlists")
def list_playlists():
    with db() as con:
        playlists = [row(r) for r in con.execute("SELECT * FROM local_playlists ORDER BY name")]
        for p in playlists:
            p["items"] = [track_row(r) for r in con.execute(
                "SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (p["id"],))]
        return playlists

@app.post("/api/playlists")
def create_playlist(name: str):
    with db() as con:
        con.execute("INSERT OR IGNORE INTO local_playlists(name) VALUES (?)", (name,))
        r = con.execute("SELECT id FROM local_playlists WHERE name=?", (name,)).fetchone()
    return {"ok": True, "id": r["id"] if r else None}

@app.delete("/api/playlists/{pid}")
def delete_playlist(pid: int):
    with db() as con:
        con.execute("DELETE FROM local_playlists WHERE id=?", (pid,))
        con.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
    return {"ok": True}

@app.post("/api/playlists/{pid}/items/{video_id}")
def add_to_playlist(pid: int, video_id: str, title: str = "", artist: str = "", thumbnail: str = ""):
    with db() as con:
        if not con.execute("SELECT 1 FROM local_playlists WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Playlist não encontrada")
        pos = con.execute("SELECT COALESCE(MAX(position),0)+1 FROM playlist_items WHERE playlist_id=?",
                          (pid,)).fetchone()[0]
        con.execute("INSERT OR REPLACE INTO playlist_items VALUES (?,?,?,?,?,?)",
                    (pid, video_id, title, artist, thumbnail, pos))
    return {"ok": True}

@app.delete("/api/playlists/{pid}/items/{video_id}")
def remove_from_playlist(pid: int, video_id: str):
    with db() as con:
        con.execute("DELETE FROM playlist_items WHERE playlist_id=? AND video_id=?", (pid, video_id))
    return {"ok": True}

# ---------- Estações (rádios nomeadas que ficam salvas) ----------
def _station_row(r):
    d = row(r)
    if d:
        d["seedId"] = d.pop("seed_id", None)
        d["seedTitle"] = d.pop("seed_title", None)
        d["seedArtist"] = d.pop("seed_artist", None)
        d["seedThumb"] = d.pop("seed_thumb", None)
    return d

@app.get("/api/stations")
def list_stations():
    with db() as con:
        return [_station_row(r) for r in con.execute("SELECT * FROM stations ORDER BY name")]

@app.post("/api/stations")
def create_station(data: dict):
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
    with db() as con:
        dup = con.execute("SELECT id FROM stations WHERE name=? AND seed_id=?", (name, vid)).fetchone()
        if dup:
            return {"ok": True, "id": dup["id"], "duplicate": True}
        cur = con.execute(
            "INSERT INTO stations(name, seed_id, seed_title, seed_artist, seed_thumb) VALUES (?,?,?,?,?)",
            (name, vid, str(data.get("title") or ""), str(data.get("artist") or ""),
             str(data.get("thumbnail") or "")))
        sid = cur.lastrowid
    return {"ok": True, "id": sid}

@app.delete("/api/stations/{sid}")
def delete_station(sid: int):
    with db() as con:
        cur = con.execute("DELETE FROM stations WHERE id=?", (sid,))
    if not cur.rowcount:
        raise HTTPException(404, "Estação não encontrada")
    return {"ok": True}

@app.post("/api/playlists/{pid}/reorder")
def reorder_playlist(pid: int, body: dict):
    """Reordena as faixas de uma playlist: corpo {"order": [videoId, ...]}.
    IDs que não estiverem no corpo mantêm a posição relativa que já tinham."""
    if not isinstance(body, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    order = body.get("order")
    if not isinstance(order, list):
        raise HTTPException(400, "corpo deve ser {\"order\": [videoId, ...]}")
    with db() as con:
        if not con.execute("SELECT 1 FROM local_playlists WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Playlist não encontrada")
        for i, vid in enumerate(order):
            vid = str(vid or "")
            if VID_RE.match(vid) or UP_RE.match(vid):
                con.execute("UPDATE playlist_items SET position=? WHERE playlist_id=? AND video_id=?",
                            (i + 1, pid, vid))
    return {"ok": True}

@app.post("/api/playlists/{pid}/items_bulk")
def add_items_bulk(pid: int, body: dict):
    """Importação em lote (playlist inteira do YouTube): {"tracks": [{videoId, ...}]}.
    IDs inválidos são ignorados; a ordem enviada é preservada."""
    if not isinstance(body, dict):
        raise HTTPException(400, "o corpo deve ser um objeto JSON")
    tracks = body.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        raise HTTPException(400, "corpo deve ser {\"tracks\": [...]}")
    tracks = tracks[:500]
    n = 0
    with db() as con:
        if not con.execute("SELECT 1 FROM local_playlists WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Playlist não encontrada")
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
def get_yt_playlist(url: str = Query(..., min_length=3)):
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
def ytplaylist_download(body: dict):
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
    job = {"id": jid, "status": "iniciando…", "total": len(tracks or []), "done": 0, "failed": 0,
           "current": "", "folder": "", "cancel": False, "errors": [], "created": time.time()}
    _prune_pl_jobs()
    with _pl_jobs_lock:
        _pl_jobs[jid] = job
    threading.Thread(target=_pl_download_thread,
                     args=(job, pid, tracks, str(body.get("folder") or "")),
                     daemon=True).start()
    return {"ok": True, "jobId": jid}

@app.get("/api/ytplaylist/job/{jid}")
def ytplaylist_job(jid: str):
    with _pl_jobs_lock:
        job = _pl_jobs.get(jid)
        if not job:
            raise HTTPException(404, "job não encontrado")
        snap = {k: job.get(k) for k in ("id", "status", "total", "done", "failed", "current", "folder")}
        snap["errors"] = list(job.get("errors") or [])[-10:]
    return snap

@app.post("/api/ytplaylist/job/{jid}/cancel")
def ytplaylist_job_cancel(jid: str):
    with _pl_jobs_lock:
        job = _pl_jobs.get(jid)
        if not job:
            raise HTTPException(404, "job não encontrado")
        job["cancel"] = True
    return {"ok": True}

@app.post("/api/playlists/{pid}/download")
def download_saved_playlist(pid: int):
    """Baixa uma playlist SALVA da aba Playlists direto pra subpasta
    downloads/playlists/<nome>/ — mesmo sistema de jobs do link do YouTube.
    Assim a playlist continua baixável depois de importada/adicionada."""
    with db() as con:
        pl = row(con.execute("SELECT * FROM local_playlists WHERE id=?", (pid,)).fetchone())
        if not pl:
            raise HTTPException(404, "Playlist não encontrada")
        tracks = [track_row(r) for r in con.execute(
            "SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (pid,))]
    if not tracks:
        raise HTTPException(400, "Playlist vazia — não tem o que baixar")
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "status": "iniciando…", "total": len(tracks), "done": 0, "failed": 0,
           "current": "", "folder": "", "cancel": False, "errors": [], "created": time.time()}
    _prune_pl_jobs()
    with _pl_jobs_lock:
        _pl_jobs[jid] = job
    threading.Thread(target=_pl_download_thread,
                     args=(job, "", tracks, pl["name"]), daemon=True).start()
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

@app.post("/api/download/{video_id}")
def download(video_id: str, title: str = Query("")):
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
    _dl_index_load()
    out = []
    for f in sorted(DL_DIR.iterdir(), key=lambda x: x.name.lower()):
        if not f.is_file() or f.name.startswith("."):
            continue
        vid = next((v for v, rec in _dl_index.items() if rec.get("file") == str(f.relative_to(DATA))), None)
        out.append({"name": f.name, "videoId": vid})
    return out

@app.get("/downloads/{name}")
def get_download(name: str):
    p = (DL_DIR / name).resolve()
    if not p.is_file() or p.parent != DL_DIR.resolve():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)

@app.get("/api/downloads/folders")
def downloads_folders():
    """Subpastas de playlists baixadas (downloads/playlists/<nome>/).
    Cada arquivo vem com videoId quando conhecido (índice de downloads)."""
    root = DL_DIR / "playlists"
    out = []
    _dl_index_load()
    rev = {rec.get("file"): v for v, rec in _dl_index.items()}
    if root.is_dir():
        for d in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if not d.is_dir():
                continue
            files = []
            for f in sorted(d.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file() and f.suffix.lower() in (".mp3", ".m4a", ".webm", ".opus"):
                    rel = str(f.relative_to(DATA))
                    files.append({"name": f.name, "videoId": rev.get(rel)})
            out.append({"name": d.name, "count": len(files), "files": files})
    return out

@app.delete("/api/downloads/folders/{folder}")
def delete_download_folder(folder: str):
    """Exclui a subpasta inteira de uma playlist baixada (com trava de travessia:
    só vale pasta FILHA DIRETA de downloads/playlists/)."""
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
    if not p.is_file() or p.parent != fdir:
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)

_DL_EXTS = (".mp3", ".m4a", ".webm", ".opus")

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
def delete_download_file(name: str):
    """Exclui UMA música avulsa de downloads/ (apontamento do Argon: só existia
    DELETE de pasta inteira e de cache — não dava pra apagar um arquivo só)."""
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
def delete_download_folder_file(folder: str, name: str):
    """Idem, para um arquivo DENTRO de downloads/playlists/<pasta>/."""
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
# Estende a biblioteca estilo Koel/Navidrome: o usuário envia os MP3/flac dele
# e toca tudo no mesmo app. O id tem prefixo up_ (13 chars) para NUNCA colidir
# com videoId de 11 chars do YouTube.
UP_RE = re.compile(r"^up_[a-f0-9]{10}$")
UP_DIR = DL_DIR / "upload"
UP_DIR.mkdir(exist_ok=True)
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
    """Baixa a capa (JPEG). Protegido contra SSRF e com limite de 5MB. Retorna None em qualquer falha — capa é bônus."""
    if not url or not _is_safe_url(url):
        return None
    try:
        r = requests.get(url, timeout=8, stream=True)
        if r.ok:
            content = b""
            for chunk in r.iter_content(chunk_size=64 * 1024):
                content += chunk
                if len(content) > 5 * 1024 * 1024:
                    return None
            if content[:3] == b"\xff\xd8\xff":
                return content
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
def upload_tracks(files: list[UploadFile] = File(...)):
    """Importa arquivos de áudio do computador para a biblioteca (dedupe por
    conteúdo: o mesmo arquivo enviado 2x não duplica)."""
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
def edit_upload(uid: str, body: dict):
    """Edita título/artista/álbum do upload: grava no banco E nas tags do
    arquivo (MP3/MP4/FLAC), baixa e embute a capa se vier art_url."""
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
def delete_upload(uid: str):
    if not UP_RE.match(uid):
        raise HTTPException(400, "id inválido")
    with db() as con:
        rec = con.execute("SELECT file, art FROM uploads WHERE id=?", (uid,)).fetchone()
        if not rec:
            raise HTTPException(404, "upload não encontrado")
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
