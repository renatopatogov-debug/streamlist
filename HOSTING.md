# StremMusic — Guia de Hospedagem (acessar o app de fora de casa)

O StremMusic é um **servidor Python que roda no seu PC**. O celular acessa o PC.
Hoje ele funciona no WiFi porque o PC e o celular estão na mesma rede. Para ouvir
música **fora de casa (4G/5G, trabalho, rua)** você precisa de um "túnel" que leve
a internet até o seu PC. Este guia cobre as opções, do mais recomendado ao menos.

> ⚠️ O coração do app é o **yt-dlp falando com o YouTube a partir do SEU IP de casa**.
> Isso é um ponto forte: YouTube bloqueia muito menos IP residencial do que IP de
> datacenter. Por isso, **hospedar em casa + túnel costuma ser MAIS estável do que
> colocar num servidor na nuvem** (VPS) — lá o YouTube responde "sign in to confirm
> you're not a bot" com frequência e o app deixa de achar música.

---

## Opção A — Tailscale (RECOMENDADA) ✅

Rede privada entre o seu PC e o seu celular, grátis (plano individual), sem abrir
portas no roteador e sem expor nada pra internet. Funciona em 4G/5G e atravessa
CGNAT (a maioria das operadoras brasileiras usa CGNAT, o que **quebra** a opção de
abrir porta no roteador — com Tailscale isso é irrelevante).

1. No PC: instale de https://tailscale.com/download e faça login.
2. No celular: instale o app Tailscale (Android/iOS) e logue **na mesma conta**.
3. Descubra o IP do PC na rede Tailscale: no app Tailscale do PC aparece algo como
   `100.x.y.z` — anote (dá pra fixar esse IP no painel admin, aba Machines).
4. No PC, inicie o servidor: `run.bat` (fica em `http://100.x.y.z:8787`).
5. No celular, **com o Tailscale ativado**, abra `http://100.x.y.z:8787` no
   navegador — funciona no WiFi, no 4G, em qualquer lugar.

**Vantagens:** zero exposição pública (ninguém além dos seus aparelhos acessa),
sem senha pra implementar, grátis, criptografado ponta a ponta.
**Limitação:** o app Tailscale precisa estar ativado no celular quando for usar.

### Deixar sempre disponível
- **PC não pode dormir**: Configurações → Sistema → Energia → "Ao ligar na tomada:
  nunca suspender" (ou `powercfg /change standby-timeout-ac 0`).
- **Iniciar com o Windows**: `Win+R` → `shell:startup` → cole um atalho para o
  `run.bat`. Ou use o Agendador de Tarefas com gatilho "Ao iniciar" e "Executar
  esteja o usuário logado ou não".
- **Firewall**: na primeira execução, autorize o Python para redes privadas
  (com Tailscale basta a rede privada; o tráfego externo nem chega ao Windows).

---

## Opção B — Cloudflare Tunnel (URL pública, sem abrir porta) 🌐

Se quiser um endereço público (ex.: `musica.suadominio.com`) que funciona sem o
app Tailscale no celular:

1. Crie uma conta grátis no Cloudflare e adicione um domínio (seu ou gratuito).
2. No PC: `cloudflared tunnel login` → `cloudflared tunnel create stremmusic` →
   `cloudflared tunnel route dns stremmusic musica.suadominio.com` →
   `cloudflared tunnel run stremmusic` apontando para `http://localhost:8787`.
3. Teste rápido sem domínio: `cloudflared tunnel --url http://localhost:8787`
   gera uma URL temporária `https://algo-aleatorio.trycloudflare.com`.

⚠️ **ATENÇÃO**: com URL pública você **precisa** ligar a proteção. O app tem duas
camadas (veja `deploy/README.md`):

- **`APP_TOKEN`** (variável de ambiente) — exige senha em `/api/*` **e** em
  `/downloads/*`; o frontend pede a senha na primeira visita e guarda no cookie.
- **Fechadura por QR** — no PC, abra `http://127.0.0.1:8787/painel` e ligue; o
  celular pareado entra pelo QR e fica com cookie de 1 ano. Quem não pareou vê
  a página de bloqueio.

Sem uma delas, **qualquer pessoa** que descobrir o endereço usa sua banda, baixa
músicas no seu disco e lê suas playlists. Para uso só seu, a Opção A continua
mais simples — não expõe nada.

---

## Opção C — Abrir porta no roteador + DDNS ❌ (não recomendo)

Expor o PC direto na internet exige IP público (a maioria das operadoras BR usa
**CGNAT** — não existe IP público e isso simplesmente não funciona), senha ligada
no app e deixa o servidor visível pra scanners. Só faça se souber o que está
fazendo.

## Opção D — VPS na nuvem (Oracle Free Tier, Contabo, etc.) ⚠️

Funciona tecnicamente, mas leia o aviso no topo: **o YouTube bloqueia IP de
datacenter** e a busca/stream fica instável — você teria que usar cookies do
navegador (`USE_COOKIES=1`) ou proxy residencial, e ainda assim quebra com
frequência.

Se for por esse caminho, o passo a passo completo (systemd, Docker, `DATA_DIR`,
`APP_TOKEN`, HTTPS com Caddy) está em **[`deploy/README.md`](deploy/README.md)**.
O backend é Python puro e não depende de Windows — o que decide a experiência é
o IP, não o sistema operacional.

---

## Checklist final (qualquer opção)

- [ ] `run.bat` inicia o servidor (sem auto-reload — correção aplicada nesta versão).
- [ ] PC configurado pra não dormir.
- [ ] Servidor inicia junto com o Windows (atalho em `shell:startup`).
- [ ] Primeira música testada pelo celular **fora do WiFi**.
- [ ] Se for expor na internet: `APP_TOKEN` definido **e** fechadura do `/painel` ligada.
- [ ] `AUTO_UPDATE_YTDLP=1` (opcional): o yt-dlp se atualiza sozinho — o YouTube
      quebra versões velhas a toda hora, e sem isso o app "morre" de repente um dia.

## Nota legal

O app extrai áudio do YouTube via yt-dlp, o que vai contra os Termos de Serviço
do YouTube. Para um player pessoal seu, de uso privado, é o mesmo risco de usar
qualquer front-end alternativo — mas **evite publicar uma instância aberta**:
além do risco de bloqueio do IP, não é o caso de oferecer isso a terceiros.
