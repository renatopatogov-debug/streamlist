# StremMusic — imagem de servidor (sem janela, só web)
#
# Build (a partir da RAIZ do projeto):
#   docker build -f deploy/Dockerfile -t stremmusic .
#
# Rode com o compose (recomendado) ou:
#   docker run -d --name stremmusic -p 127.0.0.1:8787:8787 \
#     -e APP_TOKEN=uma-senha-longa -v stremmusic-data:/data stremmusic
#
# ATENÇÃO: imagem NÃO testada pelo autor deste patch (não havia Docker na
# máquina onde ele foi escrito). O que está validado é o app em si: linux sem
# dependência de Windows, DATA_DIR, APP_TOKEN e requirements-server.txt.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8787 \
    DATA_DIR=/data

# ffmpeg = MP3 de verdade nos downloads · git = provedor de PO Token
# ca-certificates/curl = TLS e o healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# Deno: usado pelo provedor de PO Token do YouTube (bgutil).
# Vem pronto da imagem oficial, sem script de instalação (mais previsível).
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements-server.txt ./
RUN pip install -r requirements-server.txt

# Provedor de PO Token (servidor HTTP em 127.0.0.1:4416). O app sobe o Deno
# sozinho no arranque e cai no modo script se isto não existir. Best-effort:
# sem rede no build, a imagem ainda funciona (só sem PO Token).
RUN git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
        /root/bgutil-ytdlp-pot-provider \
    && cd /root/bgutil-ytdlp-pot-provider/server \
    && (deno install >/dev/null 2>&1 || true) \
    && echo "provedor de PO Token baixado"

# Código (dados ficam no volume /data, não na imagem)
COPY app.py ./
COPY static ./static

# Pasta de dados: banco, configurações, cache, downloads e uploads.
RUN mkdir -p /data && chmod 777 /data
VOLUME ["/data"]

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:8787/api/auth/check" >/dev/null || exit 1

CMD ["python", "app.py"]
