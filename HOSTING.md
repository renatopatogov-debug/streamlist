# StremMusic — Guia de Conexão e Hospedagem (Acessar fora de casa)

O StremMusic é um **servidor Python leve** que roda no seu computador ou servidor caseiro.
Ele funciona na sua rede Wi-Fi local e também pode ser acessado de qualquer lugar pelo celular (4G/5G).

O app utiliza o motor do **yt-dlp** com suporte a PO Tokens persistentes a partir do seu IP residencial, garantindo altíssima estabilidade contra bloqueios do YouTube.

---

## Opção 1 — Cloudflare Quick Tunnel Embutido (Mais Fácil / Recomendada) ☁️

O StremMusic possui integração nativa com o **Cloudflare Quick Tunnel**. Não é necessário criar conta na Cloudflare, registrar domínio nem abrir portas no roteador.

### Como usar:
1. No seu PC, inicie o servidor com suporte a túnel:
   ```powershell
   python app.py --tunnel
   # ou clique duas vezes em: run_tunnel.bat
   ```
2. O StremMusic baixará o binário seguro do `cloudflared` caso ainda não esteja presente, conectará à rede global da Cloudflare e exibirá:
   - A URL pública segura HTTPS (`https://...trycloudflare.com`)
   - O QR Code direto no terminal para escanear com a câmera do celular.
3. Você também pode ligar ou desligar o túnel em tempo real pelo painel administrativo em:
   [http://127.0.0.1:8787/painel](http://127.0.0.1:8787/painel)

---

## Opção 2 — Tailscale (Rede Privada P2P) 🔒

Para quem prefere uma VPN privada sem expor URLs públicas:

1. No PC: instale o [Tailscale](https://tailscale.com/download) e faça login.
2. No celular: instale o app Tailscale (Android/iOS) e conecte na mesma conta.
3. Anote o IP do PC na rede Tailscale (ex: `100.x.y.z`).
4. No PC, inicie o servidor normalmente com `run.bat` (escutando em `0.0.0.0:8787`).
5. No celular com o Tailscale ativado, abra `http://100.x.y.z:8787` no navegador.

---

## 👥 Sistema Multi-usuário & Painel do Host

Acesse **[http://127.0.0.1:8787/painel](http://127.0.0.1:8787/painel)** no PC do dono para:
- Cadastrar perfis para seus amigos ou cômodos (cada um com seu próprio QR Code e link exclusivo).
- Isolar favoritos, histórico e playlists por usuário.
- Ativar a **Fechadura de Acesso** (quando ativada, apenas aparelhos que escanearam o QR Code conseguem acessar o player).
- Renovar QR Codes deslogando aparelhos antigos em 1 clique.

---

## 📲 Instalando como Aplicativo (PWA) no Celular

O StremMusic é uma **Progressive Web App (PWA)** completa com suporte a áudio em segundo plano com a tela bloqueada:

- **No Android (Chrome):** Toque nos três pontinhos e selecione **"Adicionar à tela inicial"** ou **"Instalar aplicativo"**.
- **No iPhone / iPad (Safari):** Toque no botão **Compartilhar (quadrado com seta ⬆)** e selecione **"Adicionar à Tela de Início"**.
- O app rodará em tela cheia, sem barra de endereços, com controles de reprodução na tela de bloqueio e na central de controle (MediaSession API).
