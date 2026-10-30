# StremMusic — APK Android (com.stremmusic.app v2.1)

O `StremMusic.apk` desta pasta é o **app Android nativo** do StremMusic, gerado
pelo assistente do site (GLM). Ele NÃO contém a interface: é uma casca leve
(~30 KB) que abre o servidor do seu PC dentro de um WebView — ou seja, o app
sempre roda a versão mais nova da interface, sem precisar reinstalar APK.

## Como funciona

- Abre `http://<endereço salvo>:8787` (padrão: `http://192.168.0.10:8787`).
- O endereço fica salvo NO APARELHO. Para trocar: desligue o servidor (ou saia
  do WiFi) → abre o app → tela "Servidor não encontrado" → **"Mudar endereço"**.
- Se o servidor não responder, mostra uma página de erro própria (mesmo visual
  do app) com botões **"Tentar de novo"** e **"Mudar endereço"**.
- **StremKeep** (serviço de mídia em primeiro plano): quando uma música começa a
  tocar, o frontend chama `StremApp.keepAlive(true)` — isso mantém o app vivo
  em segundo plano com notificação de mídia (play/pause na notificação, áudio
  continua com a tela desligada).
- Músicas salvas "No celular" (aba 📱) ficam no armazenamento próprio do app —
  sobrevivem a reinícios do aparelho e funcionam sem internet (com o app aberto).

## Instalação no celular

1. Copie o `StremMusic.apk` para o celular (cabos, nuvem, o que preferir).
2. Toque no arquivo → permita "instalar apps desconhecidos" para o gerenciador
   de arquivos → confirme a instalação.
3. Com o `run.bat` rodando no PC, abra o app. Se o IP do PC mudou, use
   "Mudar endereço" (o IP do PC aparece no console do `run.bat`).

> O arquivo `.idsig` é só a assinatura do empacotador — não precisa instalar.

## Permissões que ele pede (e por quê)

| Permissão | Motivo |
|---|---|
| INTERNET | acessar o servidor no PC |
| FOREGROUND_SERVICE + MEDIA_PLAYBACK | StremKeep: música em segundo plano |
| POST_NOTIFICATIONS | notificação de mídia (Android 13+) |

## Requisitos do lado do servidor

Nenhum endpoint especial: o APK só carrega a interface do StremMusic normal
(`run.bat`, porta 8787). Se um dia você ativar `APP_TOKEN` no servidor, o app
pede o token na primeira abertura e guarda no aparelho.

## Manutenção

- **Interface**: atualizar = só atualizar o projeto no PC. O APK pega tudo ao vivo.
- **O APK em si**: para mudar ícone/nome/funcionalidades nativas, peça ao
  assistente do site para regerar (é um WebView custom, não um projeto Gradle/
  Capacitor) e substitua o arquivo aqui.
