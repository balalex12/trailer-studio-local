FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# Dependencias de sistema:
#  - git   -> clonar ComfyUI y custom nodes
#  - ffmpeg-> requerido por VideoHelperSuite para exportar mp4
#  - build-essential -> compilar wheels ocasionales
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget ffmpeg build-essential \
    && rm -rf /var/lib/apt/lists/*

# ComfyUI (shallow clone)
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI /app/ComfyUI
WORKDIR /app/ComfyUI

# PyTorch CUDA 12.1 + requirements de ComfyUI + soporte GGUF
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/cu121 && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gguf sentencepiece

# El entrypoint instala las dependencias de los custom_nodes montados por volumen
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8188
# Invocamos bash explicitamente (robusto ante bit de ejecucion / finales de linea)
ENTRYPOINT ["bash", "/entrypoint.sh"]
