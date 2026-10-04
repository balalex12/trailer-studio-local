# Setup, modelos y benchmarks (RTX 3000 · 6 GB)

> Documento de referencia detallado: modelos con sus URLs, comparativas medidas y notas de
> infraestructura. Para la visión general y el uso del orquestador, ve al [README](../README.es.md).

Setup de **texto a vídeo** fotorrealista para una NVIDIA Quadro RTX 3000 (6 GB
VRAM), ampliado a una **pipeline completa de producción de tráilers**: vídeo,
música, ambiente y voz narrada — todo local, en contenedores, sin nada instalado
en Windows.

El modelo de vídeo es **Wan 2.1 T2V 1.3B en fp16**, el mayor modelo de vídeo que
**cabe entero en 6 GB de VRAM** sin cuantizar ni hacer streaming desde RAM.

> 📖 **[MANUAL.md](../MANUAL.md)** — manual de uso de ComfyUI: interfaz, nodos
> explicados uno a uno, guía de prompts, ajustes y solución de problemas.
>

## Componentes de la pipeline

| Capa | Herramienta | Dónde corre | Nota |
|---|---|---|---|
| 🎬 Vídeo T2V | Wan 2.1 1.3B (ComfyUI) | contenedor `comfyui` :8188 | GPU, cabe en 6 GB |
| 🎵 Música | ACE-Step 3.5B (ComfyUI nativo) | contenedor `comfyui` | genera música + voces |
| 🍃 Ambiente/efectos | MMAudio (custom node) | contenedor `comfyui` | sonido de campo desde el vídeo |
| 🗣️ Voz narrada | OmniVoice Studio | contenedor `omnivoice` :3900 | TTS español, **solo GPU** |
| ✂️ Montaje/mezcla | ffmpeg (dentro de ComfyUI) | contenedor `comfyui` | crossfades, ducking, loudnorm |

**Los dos servicios comparten la única GPU de 6 GB.** No pueden generar a la vez:
para usar OmniVoice se para ComfyUI (ver sección OmniVoice abajo).

## Puesta en marcha

```powershell
docker compose up -d          # arranca ComfyUI en http://localhost:8188
docker compose down           # lo detiene
```

En la UI: menú **Workflows → Wan21_T2V_1.3B_6GB**. Cambia el prompt en el nodo
verde y pulsa **Queue**.

## Instalación desde cero (tras clonar este repo)

Este repo **no incluye los modelos ni los custom nodes** (pesan ~23 GB y se
descargan de sus fuentes). Para reconstruir el entorno:

```bash
# 1) Custom nodes
git clone https://github.com/city96/ComfyUI-GGUF                 custom_nodes/ComfyUI-GGUF
git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite custom_nodes/ComfyUI-VideoHelperSuite
git clone https://github.com/kijai/ComfyUI-MMAudio               custom_nodes/ComfyUI-MMAudio

# 2) Modelos: descarga los archivos de la seccion "Modelos" a sus carpetas.
# 3) Levanta:
docker compose up -d
```

## Modelos instalados (6.7 GB)

| Archivo | Carpeta | Tamaño | Rol |
|---|---|---|---|
| `wan2.1_t2v_1.3B_fp16.safetensors` | `models/diffusion_models/` | 2.64 GB | Modelo de vídeo (difusión) |
| `umt5-xxl-encoder-Q5_K_M.gguf` | `models/text_encoders/` | 3.86 GB | Text encoder de Wan (corre en CPU) |
| `wan_2.1_vae.safetensors` | `models/vae/` | 0.24 GB | VAE de Wan |
| `Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank32.safetensors` | `models/loras/` | 0.09 GB | LoRA de velocidad (solo preview) |
| `ace_step_v1_3.5b.safetensors` | `models/checkpoints/` | 7.17 GB | **Música** (ACE-Step, todo-en-uno) |
| `mmaudio_*` (4 archivos) | `models/mmaudio/` | 4.88 GB | **Ambiente/efectos** (MMAudio) |

Descarga (si hiciera falta rehacerla):

```
# Vídeo (Wan 2.1)
https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors
https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q5_K_M.gguf
https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors
https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank32.safetensors

# Música (ACE-Step)
https://huggingface.co/Comfy-Org/ACE-Step_ComfyUI_repackaged/resolve/main/all_in_one/ace_step_v1_3.5b.safetensors

# Ambiente (MMAudio) -> models/mmaudio/ , y clonar el nodo en custom_nodes/
https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/mmaudio_large_44k_v2_fp16.safetensors
https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/mmaudio_vae_44k_fp16.safetensors
https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/mmaudio_synchformer_fp16.safetensors
https://huggingface.co/Kijai/MMAudio_safetensors/resolve/main/apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors
# git clone https://github.com/kijai/ComfyUI-MMAudio  (en custom_nodes/)
```

## Los dos workflows

| Workflow | Uso | Steps / CFG | Tiempo |
|---|---|---|---|
| `Wan21_T2V_1.3B_6GB.json` | **Calidad final** | 30 / 6.0, sin LoRA | ~24 min |
| `Wan21_T2V_1.3B_PREVIEW_rapido.json` | **Iterar prompts** | 14 / 1.0, LoRA CausVid 0.3 | ~6 min |

Flujo recomendado: busca el encuadre y el prompt con el de *preview*, y cuando te
guste, repite con la **misma semilla** en el de calidad final.

## Configuración de referencia (medida)

| Parámetro | Valor |
|---|---|
| Resolución | 832 × 480 (nativa del modelo) |
| Frames / fps | 81 @ 16 fps = 5.06 s |
| Steps | 30 |
| CFG | 6.0 |
| Sampler / scheduler | `uni_pc` / `simple` |
| `ModelSamplingSD3` shift | 8.0 |
| **Tiempo medido** | **~24 min por clip** |
| **VRAM pico** | **5.3 GB de 6 GB** |

## Comparativa medida (mismo prompt, misma semilla 12345)

| Config | Tiempo | Resultado |
|---|---|---|
| LoRA CausVid 1.0 / 6 pasos | 4.3 min | Sobresaturado, piel naranja, aspecto plastificado |
| LoRA CausVid 0.5 / 10 pasos | 4.0 min | Aceptable, algo saturado, detalle blando |
| LoRA CausVid 0.3 / 14 pasos | 5.7 min | Color natural, pero piel demasiado lisa y sujeto rejuvenecido |
| **Sin LoRA / 15 pasos / CFG 6** | **17.7 min** | **Casi idéntico al de 30 pasos** |
| Sin LoRA / 30 pasos / CFG 6 | 23.7 min | El mejor: poros, arrugas, manchas de edad |

**Conclusión:** el LoRA CausVid acelera 3-4× pero **degrada el realismo**; no es un
"refinamiento", es un intercambio. Sirve solo para previsualizar. El detalle de
piel lo aportan los pasos con CFG real, no el LoRA.

**Sobre 15 vs 30 pasos:** comparando *fotogramas sueltos* los 15 pasos parecían
equivalentes. Viendo los *vídeos en movimiento*, los 30 pasos son claramente
mejores — la diferencia está en la coherencia temporal, que no se aprecia en una
imagen fija. Por eso el workflow de calidad usa 30 pasos. Lección: validar vídeo
mirando vídeo, no fotogramas.

La clave del encaje en 6 GB: el text encoder umt5-XXL se carga **en CPU**
(`load device: cpu`), así que la VRAM queda libre para el modelo de difusión,
que carga completo (`full load: True`) sin offloading.

## Por qué NO usamos modelos más grandes

Se probó **Wan 2.1 14B (Q6_K, 11.6 GB)**. No cabe en 6 GB: los 12 GB viajan por
PCIe en cada evaluación del modelo. Con 30 pasos y CFG 6.0 son 60 evaluaciones,
y el render se fue a **más de 2 horas sin terminar** (estimado ~4 h por clip de
5 s). Descartado por inviable en este hardware.

También se descartó **LTX-Video 0.9.6 distilled**: rápido (2.7 min/clip) pero su
acabado es ilustrado/pictórico, no fotorrealista.

Si algún día se retoma el 14B, la vía es un **LoRA de destilación**
(LightX2V / CausVid) que permite 8 pasos con CFG 1.0 → 8 evaluaciones en vez de
60. Estimado ~35 min/clip. No está instalado.

## Ajustes para mejorar

- **Más detalle:** subir a `960×544`. Ojo, el modelo se entrenó a 480p y por
  encima puede degradarse; probar antes de comprometer una tanda larga.
- **Más duración:** `length` debe ser múltiplo de 4 más 1 (81, 121…). 121 frames
  = 7.5 s, pero sube VRAM y tiempo.
- **Si da out of memory:** bajar a `640×384` o reducir frames.
- **Prompts:** funcionan mucho mejor los descriptivos y fotográficos (óptica,
  luz, textura de piel, "documentary", "35mm") que los épicos o estilizados.
  Los términos grandilocuentes empujan el resultado hacia lo pictórico.

## Audio: música, ambiente y voz

La pipeline de audio tiene tres fuentes que luego se mezclan con ffmpeg.

### 🎵 Música — ACE-Step (nativo en ComfyUI)
Genera música (y voces cantadas) desde etiquetas de estilo + letra. Workflow
manual con los nodos `CheckpointLoaderSimple` → `TextEncodeAceStepAudio` →
`EmptyAceStepLatentAudio` → `KSampler` → `VAEDecodeAudio` → `SaveAudio`.
- Estilo Ghibli medido: tags tipo `studio ghibli, orchestral, gentle piano,
  strings, flute, wordless female vocalise, Joe Hisaishi inspired`.
- 28 s, 50 pasos, cfg 5.0, sampler `euler` → **~80 s** de render.

### 🍃 Ambiente/efectos — MMAudio (custom node)
Genera sonido de campo (viento, lluvia, agua) **a partir del vídeo**. Solo hace
ambiente/foley, **no música ni voces**, y tiende a sonar algo ruidoso.
- **Límite de VRAM:** no cabe generar más de ~15 s de audio de una vez en 6 GB
  (el latente de audio, no los fotogramas, es lo que satura). Generar por
  tramos y liberar VRAM entre pasadas (`POST /free`).

### 🗣️ Voz narrada — OmniVoice (contenedor aparte)
App open-source tipo ElevenLabs, 100% local, con API compatible con OpenAI.
Corre como **contenedor propio** en `omnivoice/docker-compose.yml`.

```powershell
cd omnivoice
docker compose up -d          # arranca OmniVoice en http://localhost:3900
docker compose down           # lo detiene
```

- **UI web:** http://localhost:3900
- **API TTS:** `POST http://localhost:3900/v1/audio/speech`
  ```bash
  curl http://localhost:3900/v1/audio/speech \
    -H "Content-Type: application/json" \
    -d '{"input":"Texto en español","response_format":"wav","language":"es"}' \
    --output voz.wav
  ```
- ⚠️ **SOLO funciona en GPU.** El modo CPU tiene un bug/deadlock: la petición
  entra pero el motor se queda a 0 % de CPU sin generar nada. El compose ya está
  configurado con GPU.
- ⚠️ **Comparte la GPU de 6 GB con ComfyUI.** Antes de generar voz, **para
  ComfyUI** para dejarle la VRAM libre:
  ```powershell
  # en la carpeta raiz:
  docker compose stop comfyui
  # generar la voz con OmniVoice...
  docker compose start comfyui
  ```
- El modelo (`k2-fsa/OmniVoice`, ~2.4 GB) se descarga en el volumen
  `omnivoice-data` la primera vez.

### ✂️ Mezcla final (ffmpeg)
Las tres capas se combinan con ffmpeg (disponible dentro del contenedor
`comfyui`). Técnicas usadas en el tráiler:
- **Crossfades** de vídeo (`xfade`) y audio (`acrossfade`) entre planos.
- **Ducking**: la música baja cuando entra la voz (`sidechaincompress`).
- **Normalización** a −16 LUFS con `loudnorm` + `alimiter` (evita clipping).

## Notas de infraestructura

- `entrypoint.sh` instala los `requirements.txt` de cada custom node en cada
  arranque (viven en un volumen montado, así que no se pueden instalar en build).
- Custom nodes usados: `ComfyUI-GGUF` (text encoder cuantizado),
  `ComfyUI-VideoHelperSuite` (exportar mp4) y `ComfyUI-MMAudio` (ambiente).
- Aviso no fatal en logs: `You need pytorch with cu130 or higher`. Es la ruta
  CUDA optimizada opcional; funciona igual con cu121.
- **Dos servicios, una GPU:** `comfyui` (:8188) y `omnivoice` (:3900) comparten
  la única RTX 3000. No generan a la vez — para OmniVoice se para ComfyUI.
- Los resultados se guardan en `output/`. **Ojo:** esa carpeta se limpia entre
  pruebas — mueve lo que quieras conservar a otra carpeta.
