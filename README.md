# 🎵 StremMusic

<p align="center">
  <b>Player de streaming de música moderno, auto-hospedado (self-hosted), com suporte a PWA mobile, túnel reverso 4G/5G e gerenciamento multi-usuário via QR Code.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/PWA-Ready-5A0FC8?style=flat&logo=pwa&logoColor=white" alt="PWA">
  <img src="https://img.shields.io/badge/Cloudflare-Quick%20Tunnel-F38020?style=flat&logo=cloudflare&logoColor=white" alt="Cloudflare">
  <img src="https://img.shields.io/badge/yt--dlp-2026.10+-red?style=flat&logo=youtube&logoColor=white" alt="yt-dlp">
</p>

---

## ✨ Destaques & Funcionalidades

* 🎧 **Streaming Real via YouTube Music:** Reprodução progressiva com cabeçalhos HTTP `Range` (HTTP 206), permitindo avançar e retroceder instantaneamente sem travar a reprodução.
* ☁️ **Túnel Cloudflare Integrado (4G/5G na Rua):** Ouça suas músicas fora de casa sem precisar abrir portas no roteador, assinar VPS ou configurar DNS. Gera links HTTPS seguros (`trycloudflare.com`) e QR Code automaticamente.
* 👥 **Multi-usuário & Perfis com QR Code Exclusivo:** Crie perfis para amigos ou membros da família no painel do host (`/painel`). Cada perfil possui QR Code próprio, mantendo favoritos, histórico e playlists 100% isolados.
* 📲 **Aplicativo Celular (PWA) no iOS e Android:** Instale na tela inicial com um toque. Roda em tela cheia com reprodução em segundo plano e controles na tela de bloqueio (Media Session API).
* 💾 **Cache Inteligente & Downloads MP3:** Armazena faixas em cache local em disco, aprende suas sequências favoritas e permite baixar faixas convertidas em MP3 (128, 192 ou 320 kbps) via FFmpeg.
* 🛡️ **Motor Resiliente yt-dlp + PO Token:** Servidor interno de PO Token persistente (`bgutil-ytdlp-pot-provider`) e disputa paralela de clients para contornar restrições de bots do YouTube.
* 📁 **Uploads de Áudio Próprios:** Adicione seus arquivos locais de áudio (.mp3, .flac, .m4a, .ogg) arrastando para o navegador, com extração de tags ID3 e capas de álbum.
* 🖥️ **Modo Desktop Windows:** Janela nativa leve via WebView2 (`desktop.bat`) sem barra de ferramentas do navegador.

---

## 🚀 Como Usar no Windows (1-Click)

Basta baixar o repositório (ou a pasta da Release) e dar dois cliques no launcher unificado:
```cmd
Iniciar.bat
```
O **`Iniciar.bat`** cuida de tudo sozinho:
1. Verifica se o Python está presente (e auxilia a instalar caso falte).
2. Instala automaticamente as bibliotecas na primeira vez que for aberto.
3. Exibe um menu simples para você escolher como quer rodar:
   - **Túnel Cloudflare (Rua 4G/5G + QR Code)**
   - **Aplicativo Desktop (Janela nativa)**
   - **Servidor Local (Wi-Fi de casa)**
   - **Atualização rápida de componentes do YouTube**

*(Se preferir atalhos diretos, você também pode usar `run_tunnel.bat`, `run.bat` ou `desktop.bat`)*

---

## 👥 Conectando Celulares & Amigos (Painel do Host)

No computador onde o servidor está rodando, acesse:
👉 **[http://127.0.0.1:8787/painel](http://127.0.0.1:8787/painel)**

1. Digite o nome do amigo e clique em **"＋ Criar Novo Login & QR Code"**.
2. Aponte a câmera do celular do amigo para o QR Code gerado na tela.
3. No navegador do smartphone, toque em **"Adicionar à Tela Inicial"** para instalar o PWA.
4. O celular estará conectado diretamente ao perfil daquela pessoa!

---

## 🐧 Instalação em Linux / Servidor / Docker

Consulte o diretório [`deploy/`](deploy/) para documentação de servidor e arquivos prontos:

* **Docker Compose:**
  ```bash
  docker compose -f deploy/docker-compose.yml up -d
  ```
* **Instalação direta no servidor Linux:**
  ```bash
  pip install -r requirements-server.txt
  python app.py --host 0.0.0.0 --port 8787
  ```
* **Serviço systemd:** Arquivo [`deploy/stremmusic.service`](deploy/stremmusic.service) incluído para inicialização automática no boot.

---

## ⌨️ Atalhos de Teclado

| Atalho | Ação |
|---|---|
| `Espaço` | Reproduzir / Pausar |
| `Ctrl + Seta Direita` | Próxima música |
| `Ctrl + Seta Esquerda` | Música anterior |
| `Seta Cima / Baixo` | Ajustar volume |
| `M` | Silenciar áudio |
| `Alt + 1` a `Alt + 4` | Alternar layouts de tela (Automático, Clássico, Estúdio, Compacto) |
| `/` | Focar na barra de busca |

---

## 🧪 Testes de Confiabilidade

Para executar a suíte completa de testes automatizados:
```bash
python tests/test_smoke.py
```
*(Cobre validação de cache HTTP 206, extração de playlists, isolamento multi-usuário, túnel Cloudflare e bypass de service worker)*

---

## 📄 Licença

Distribuído sob a licença MIT. Consulte o arquivo de licença para mais detalhes.
