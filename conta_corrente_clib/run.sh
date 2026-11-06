#!/usr/bin/with-contenv bashio
export APP_DB=/data/clib.sqlite
export APP_PDFS=/data/pdfs
export APP_VERSAO="$(bashio::addon.version)"
export PYTHONPATH=/app

bashio::log.info "A iniciar Conta Corrente CLIB ${APP_VERSAO} na porta 8099 (só acessível pelo ingress)."
cd /app
exec python3 -m waitress --host=0.0.0.0 --port=8099 --threads=4 web.app:app
