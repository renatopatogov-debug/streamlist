# StremMusic em servidor — guia de instalação

Resposta curta: **sim, dá para hospedar.** O backend é Python puro (FastAPI +
uvicorn), não tem nenhuma amarra de Windows, e já traz duas camadas de acesso
(`APP_TOKEN` e a fechadura por QR do `/painel`).

O problema prático de hospedar **fora de casa** não é o app: é o YouTube
tratando IP de datacenter como robô. Leia a seção [Onde hospedar](#1-onde-hospedar)
antes de escolher.

---

## 1. Onde hospedar

| Onde | YouTube | Custo | Veredito |
|---|---|---|---|
| **PC / mini PC / NAS em casa** + Tailscale | ✅ IP residencial, raramente bloqueia | R$ 0 (energia) | **melhor opção** |
| **Raspberry Pi 4/5** em casa | ✅ idem | ~R$ 400 uma vez | ótimo, baixo consumo |
| **VPS** (Oracle Free, Contabo, Hetzner…) | ⚠️ muitos IPs já marcados: "Sign in to confirm you're not a bot" | R$ 20–40/mês | só com cookies ou proxy residencial |
| **PaaS** (Render, Railway, Fly) | ❌ efêmero/alto bloqueio; sem disco persistente de verdade | variável | não recomendo |

Regra de bolso: **o app gosta de IP residencial**. Se for VPS, planeje
`cookies.txt` (exportado do navegador logado) ou `YT_PROXY` com proxy
residencial — sem isso a busca funciona e o áudio falha com 403/429.

---

## 2. Linux direto (systemd) — sem container

```bash
# 1) dependências de sistema
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg curl git

# 2) usuário e pastas
sudo useradd -r -m -d /opt/stremmusic stremmusic || true
sudo mkdir -p /opt/stremmusic /var/lib/stremmusic
sudo chown -R stremmusic:stremmusic /opt/stremmusic /var/lib/stremmusic

# 3) código (copie a pasta do projeto para /opt/stremmusic)
#    precisam existir: app.py, static/, requirements-server.txt
cd /opt/stremmusic
sudo -u stremmusic python3 -m venv .venv
sudo -u stremmusic .venv/bin/pip install -U pip
sudo -u stremmusic .venv/bin/pip install -r requirements-server.txt

# 4) Deno (opcional, mas melhora muito o áudio: PO Token)
curl -fsSL https://deno.land/install.sh | sudo -u stremmusic sh
sudo -u stremmusic git clone --depth 1 \
  https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
  /home/stremmusic/bgutil-ytdlp-pot-provider

# 5) senha de acesso
python3 -c "import secrets;print(secrets.token_urlsafe(24))"

# 6) serviço
sudo cp deploy/stremmusic.service /etc/systemd/system/
sudo nano /etc/systemd/system/stremmusic.service      # cole a senha em APP_TOKEN
sudo systemctl daemon-reload
sudo systemctl enable --now stremmusic
sudo systemctl status stremmusic
journalctl -u stremmusic -f                            # log ao vivo
```

Teste: `curl -H "Authorization: Bearer SUA_SENHA" http://127.0.0.1:8787/api/settings`

### Atualizar depois

```bash
sudo systemctl stop stremmusic
# copie os arquivos novos para /opt/stremmusic
sudo -u stremmusic .venv/bin/pip install -r requirements-server.txt
sudo systemctl start stremmusic
```

---

## 3. Docker / docker compose

```bash
cp deploy/docker-compose.yml .            # opcional: rodar da raiz
echo "APP_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" > .env
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f
```

Dados (banco, configurações, cache, downloads, uploads) ficam no volume
`stremmusic-data`, montado em `/data` via `DATA_DIR`. Recriar o container não
perde nada.

> ⚠️ A imagem em `deploy/Dockerfile` **não pôde ser construída na máquina onde
> este guia foi escrito** (não havia Docker disponível). O que está validado é o
> que ela usa: `requirements-server.txt` instala e o app importa sem `pywebview`,
> `DATA_DIR` funciona, `APP_TOKEN` bloqueia API **e** downloads, e o backend não
> tem dependência de Windows. Se o build falhar, o passo mais provável é o
> `deno install` do provedor de PO Token — ele é best-effort: sem ele o app sobe
> e só perde o PO Token.

---

## 4. Variáveis de ambiente

| Variável | Padrão | Para que serve |
|---|---|---|
| `APP_TOKEN` | vazio (sem senha) | **defina em servidor**. Exige token em `/api/*` e `/downloads/*` (cookie, `?token=` ou `Bearer`) |
| `DATA_DIR` | pasta do app | banco, `settings.json`, cache, downloads e uploads |
| `HOST` / `PORT` | `0.0.0.0` / `8787` | escuta. Atrás de proxy use `HOST=127.0.0.1` |
| `AUTO_UPDATE_YTDLP` | `0` | `1` atualiza o yt-dlp no arranque (o YouTube quebra versões velhas) |
| `PROBE_ON_BOOT` | `1` | mede os clients no arranque (os "pre-tests"); `0` desliga |
| `YT_PROXY` | — | `http://user:pass@host:porta` para sair por outro IP |
| `USE_COOKIES` | `0` | `1` usa `cookies.txt` do `DATA_DIR` |

---

## 5. Segurança (leia antes de abrir para a internet)

1. **Sempre com `APP_TOKEN`.** Sem ele, quem achar a URL usa sua banda, enche
   seu disco e vê suas playlists.
2. **A fechadura por QR** (`/painel`, só no próprio PC) é o modo "família":
   liga/desliga em um clique e o celular pareado guarda o cookie por 1 ano.
3. **`/painel` só responde a localhost.** Atrás de proxy isso continua valendo
   porque o uvicorn confia no `X-Forwarded-For` vindo de `127.0.0.1`. **Não**
   rode com `--forwarded-allow-ips=*`: isso faria todo mundo parecer local.
4. **HTTPS na frente.** Use Caddy (2 linhas de config, certificado automático)
   ou Cloudflare Tunnel. Sem TLS, sua senha viaja em texto claro.
5. **Firewall**: só 22/80/443 abertos. O 8787 escuta em `127.0.0.1`.
6. **Backup**: `metrolist.db` + `settings.json` (poucos KB) bastam. Cache e
   downloads podem ser refeitos.

Exemplo de `Caddyfile`:

```
musica.seudominio.com {
    reverse_proxy 127.0.0.1:8787
}
```

---

## 6. O que foi verificado (e o que não foi)

Verificado nesta máquina:

- `pip install -r requirements-server.txt` lista tudo que o `app.py` importa —
  nenhum import de Windows (`winreg`, `msvcrt`), nenhum caminho `C:\`.
- `pywebview` **não** é necessário para o servidor (o app roda sem ele).
- `DATA_DIR` cria e usa banco, cache, downloads e uploads no diretório indicado.
- `APP_TOKEN`: `/api/settings` sem token → 401; com `?token=`, `Bearer` ou cookie
  → 200; `/downloads/arquivo.mp3` sem token → **401** (antes vazava).
- `/painel` responde em localhost; `/` carrega para o frontend pedir a senha.
- `python app.py` sobe com `HOST=0.0.0.0` e imprime o IP da rede.

Não verificado (não havia como nesta máquina): build da imagem Docker,
`systemd` de verdade e instalação num Linux real. Os arquivos seguem o padrão
usual dessas ferramentas, mas trate-os como rascunho até o primeiro deploy.
