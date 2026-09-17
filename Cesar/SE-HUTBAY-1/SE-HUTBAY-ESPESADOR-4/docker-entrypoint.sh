#!/bin/sh
# Entrypoint del contenedor SE-Espesador.
#
# Siembra los tags PCS7 de planta antes de levantar Gunicorn.
# El seed es idempotente: en reinicios no duplica ni pisa lo que hayas
# editado en la UI (config/ es un volumen, asi que persiste).
#
# Para saltarlo:  SEED_TAGS_PLANTA=0 docker compose up
set -e

if [ "${SEED_TAGS_PLANTA:-1}" = "1" ]; then
    echo "[entrypoint] Sembrando tags PCS7 de planta..."
    python /app/scripts/seed_tags_planta.py || \
        echo "[entrypoint] AVISO: el seed fallo, se arranca igual con la config existente."
else
    echo "[entrypoint] Seed desactivado (SEED_TAGS_PLANTA=0)."
fi

echo "[entrypoint] Arrancando Gunicorn..."
exec "$@"
