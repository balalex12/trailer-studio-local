#!/usr/bin/env bash
set -e
cd /app/ComfyUI

# Los custom_nodes viven en un volumen del host, por lo que sus dependencias
# no se instalan en build. Se instalan aqui en cada arranque (idempotente).
echo "[entrypoint] Instalando dependencias de custom_nodes..."
for req in custom_nodes/*/requirements.txt; do
  [ -f "$req" ] || continue
  echo "[entrypoint] -> $(dirname "$req")"
  pip install --no-cache-dir -r "$req" || echo "[entrypoint] AVISO: fallo en $req (continuo)"
done

echo "[entrypoint] Lanzando ComfyUI (args: $*)"
exec python main.py --listen 0.0.0.0 --port 8188 "$@"
