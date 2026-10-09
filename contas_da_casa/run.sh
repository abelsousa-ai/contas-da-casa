#!/usr/bin/with-contenv bashio
# Contas da casa: dados em /data (persistente e incluído nos backups do Home Assistant)
export FINANCAS_DB=/data/financas.sqlite
export FINANCAS_SEGREDO_FICHEIRO=/data/segredo
export PYTHONPATH=/app

if [ ! -f "${FINANCAS_DB}" ]; then
  bashio::log.info "Ainda não há base de dados: abre a app no painel para carregar a base inicial."
fi

bashio::log.info "A iniciar Contas da casa na porta 8099 (só acessível pelo ingress)."
cd /app
exec python3 -m waitress --host=0.0.0.0 --port=8099 --threads=4 web.app:app
