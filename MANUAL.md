# Manual de ComfyUI — este proyecto

Manual práctico para generar video con **Wan 2.1 1.3B** en una Quadro RTX 3000
(6 GB). No es genérico: describe exactamente los nodos y ajustes de este setup.

---

## 1. Arrancar y parar

```powershell
cd <carpeta-del-proyecto>

docker compose up -d      # arranca (tarda ~1 min la primera vez)
docker compose down       # para y libera la GPU
docker compose logs -f    # ver el log en vivo (Ctrl+C para salir)
```

Interfaz: **http://localhost:8188**

> Al arrancar, `entrypoint.sh` reinstala las dependencias de los custom nodes.
> Es normal ver `pip install` en el log durante los primeros 30-60 s.

**Para de verdad la GPU cuando termines.** El contenedor mantiene el modelo en
memoria y la tarjeta reservada.

---

## 2. Conceptos: qué es un workflow

ComfyUI no tiene "botones de generar". Tiene un **grafo de nodos**: cajas
conectadas por cables, donde cada caja hace una cosa y pasa el resultado a la
siguiente. El video sale por el extremo derecho.

- **Nodo** = una operación (cargar modelo, codificar texto, samplear…)
- **Cable** = un dato viajando (MODEL, CLIP, LATENT, IMAGE…)
- Los colores de los puntos indican el tipo. **Solo encajan tipos iguales.**

### Controles básicos del lienzo

| Acción | Cómo |
|---|---|
| Mover el lienzo | Arrastrar con el fondo, o barra espaciadora + ratón |
| Zoom | Rueda del ratón |
| Mover un nodo | Arrastrar por su barra de título |
| Conectar | Arrastrar de un punto de salida a uno de entrada |
| Desconectar | Arrastrar el cable fuera y soltar en vacío |
| Añadir nodo | Doble clic en el fondo → buscador |
| Borrar nodo | Clic + `Supr` |
| Deshacer | `Ctrl+Z` |
| **Generar** | Botón **Queue** (o `Ctrl+Enter`) |

---

## 3. Cargar los workflows de este proyecto

Menú lateral **Workflows** → verás:

| Workflow | Cuándo usarlo | Tiempo |
|---|---|---|
| `Wan21_T2V_1.3B_6GB` | **Resultado final** | ~24 min |
| `Wan21_T2V_1.3B_PREVIEW_rapido` | Buscar prompt y encuadre | ~6 min |

**Flujo de trabajo recomendado:**

1. Abre el de *preview*. Escribe tu prompt. **Queue**.
2. ¿No te gusta? Cambia el prompt y repite (6 min por intento).
3. ¿Te gusta el encuadre? **Apunta la semilla** (`seed` del KSampler).
4. Abre el de calidad final, pega el mismo prompt y **la misma semilla**.
5. **Queue**. En ~24 min tienes la versión buena.

> La misma semilla + el mismo prompt dan aproximadamente la misma escena. No es
> idéntica entre workflows (cambian pasos y CFG), pero la composición se conserva.

---

## 4. Los nodos de nuestro workflow, uno a uno

Orden de izquierda a derecha:

### `UNETLoader` — el modelo de video
Carga `wan2.1_t2v_1.3B_fp16.safetensors`. Es el que "sabe" hacer video.
`weight_dtype` déjalo en `default`.

### `LoraLoaderModelOnly` *(solo en el workflow de preview)*
Aplica el LoRA CausVid, que permite generar en muchos menos pasos.
`strength_model` 0.3. **Subirlo satura los colores y aplasta el detalle.**

### `ModelSamplingSD3` — `shift`
Controla la distribución del ruido. **8.0** para Wan. Tocarlo sin criterio
rompe el resultado; déjalo quieto.

### `CLIPLoaderGGUF` — el text encoder
Carga `umt5-xxl-encoder-Q5_K_M.gguf`, tipo `wan`. Convierte tu texto en algo que
el modelo entiende. **Corre en CPU**, y esa es la razón de que todo esto quepa
en 6 GB: no gasta VRAM.

### `CLIPTextEncode` (verde) — prompt positivo
**Aquí escribes lo que quieres.** Ver la guía de prompts abajo.

### `CLIPTextEncode` (rojo) — prompt negativo
Lo que NO quieres. **Importante:** solo tiene efecto si `cfg` > 1.0. En el
workflow de preview (cfg 1.0) se ignora por completo.

### `EmptyHunyuanLatentVideo` — tamaño y duración
| Campo | Valor | Nota |
|---|---|---|
| `width` × `height` | 832 × 480 | Resolución nativa de Wan 1.3B |
| `length` | 81 | **Debe ser múltiplo de 4 más 1** (49, 81, 121…) |
| `batch_size` | 1 | No lo subas, no cabe |

Duración = `length` ÷ 16 fps. Con 81 → 5.06 s.

### `KSampler` — el motor
| Campo | Valor | Qué hace |
|---|---|---|
| `seed` | cualquier número | Misma semilla = misma escena |
| `control_after_generate` | `fixed` / `randomize` | `fixed` para repetir, `randomize` para explorar |
| `steps` | **30** | Más pasos = más detalle y mejor coherencia temporal |
| `cfg` | **6.0** | Cuánto obedece al prompt. Con LoRA: 1.0 |
| `sampler_name` | `uni_pc` | El que mejor va con Wan |
| `scheduler` | `simple` | |
| `denoise` | 1.0 | No tocar en texto-a-video |

### `VAEDecode` + `VAELoader`
Convierten el resultado interno (latente) en imágenes visibles.

### `VHS_VideoCombine` — la salida
Junta los fotogramas en `.mp4`.
- `frame_rate`: **16** (Wan se entrenó a 16 fps; cambiarlo acelera o ralentiza)
- `filename_prefix`: el nombre del archivo
- `format`: `video/h264-mp4`
- `crf`: 19 (menor = más calidad y más peso)

Los archivos aparecen en `output/`.

---

## 5. Guía de prompts (lo que aprendimos midiendo)

### Para fotorrealismo

Funciona el lenguaje **fotográfico y mundano**:

```
Shot on a 50mm lens at f2.8, natural colour, realistic film grain,
photorealistic, ordinary and unglamorous, like a still from a documentary,
soft overcast daylight, visible skin pores and age spots
```

**Evita** lo épico y grandilocuente (`cinematic`, `epic`, `golden hour`,
`volumetric god rays`, `masterpiece`). Empujan el resultado hacia la
ilustración, no hacia la foto. Esto lo comprobamos: el mismo modelo pasa de
parecer foto a parecer dibujo solo cambiando ese vocabulario.

Negativo útil para foto real:
```
3d render, cgi, plastic skin, airbrushed, beauty filter, smooth skin, glamour,
oversaturated, cartoon, anime, illustration, painting
```

### Para estilo animado / ilustración

**Hay que invertir el negativo.** Si dejas `cartoon, anime, illustration` en el
negativo, el modelo peleará contra tu propia petición.

Positivo:
```
Hand-drawn 2D anime animation, soft hand-painted watercolour backgrounds,
clean bold cel-shaded linework with flat colour fills, warm nostalgic palette,
traditional cel animation aesthetic
```

Negativo:
```
photorealistic, photograph, realistic, live action, 3d render, cgi,
film grain, bokeh, gritty
```

### Reglas generales

1. **Describe el movimiento explícitamente.** "He slowly turns his head",
   "grass sways in the wind". Sin eso salen planos casi congelados.
2. **Describe también la cámara.** "Slow steady pan", "handheld with subtle
   natural movement", "static camera".
3. **Prompts largos funcionan mejor** que cortos. El encoder umt5 admite mucho
   texto y agradece el detalle.
4. **Escenas con movimiento continuo** (agua, viento, nubes, humo) salen mucho
   mejor que sujetos que deben quedarse quietos.
5. **Manos y rostros en primer plano** son la zona frágil. Encuadres medios o
   con poca profundidad de campo perdonan más.

---

## 6. Ajustes y sus consecuencias

| Quiero… | Cambio | Coste |
|---|---|---|
| Más duración | `length` 81 → 121 | +50% tiempo y VRAM |
| Más resolución | 832×480 → 960×544 | +40% tiempo; **puede degradar** (el modelo se entrenó a 480p) |
| Menos VRAM | 832×480 → 640×384 | Menos detalle |
| Iterar rápido | Workflow de preview | Pierde realismo |
| Otra variación | Cambiar `seed` | — |
| Repetir exacto | Misma `seed` + mismo prompt | — |

**Regla de la VRAM:** el consumo escala con `ancho × alto × frames`. Con la
configuración por defecto vas a 5.3 GB de 6 GB. **Queda muy poco margen**, así
que sube un parámetro cada vez y comprueba.

---

## 7. Problemas frecuentes

### Un nodo aparece en rojo
Falta el custom node o el archivo del modelo. Comprueba que existe en `models/`
y reinicia: `docker compose restart`.

### `CUDA out of memory`
Baja `length` a 49, o la resolución a 640×384. Si insiste, cambia el flag
`--lowvram` por `--novram` en `docker-compose.yml` (más lento, menos VRAM).

### El video sale casi congelado
El prompt no describe movimiento. Añade acción explícita y elementos que se
muevan solos (viento, agua, nubes).

### Colores saturados y aspecto plastificado
Estás usando el workflow de preview con el LoRA. Es esperable. Usa el de
calidad final.

### El progreso no se ve en el log
`tqdm` no vuelca la barra hasta terminar. Para saber si trabaja:
```powershell
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
```
Si la GPU está al 100%, está generando.

### Cancelar un render
Botón **Cancel** en la UI, o:
```powershell
Invoke-RestMethod -Uri "http://localhost:8188/interrupt" -Method Post
```

---

## 8. Añadir modelos nuevos

Coloca el archivo en la carpeta correcta y **reinicia el contenedor**:

| Tipo | Carpeta |
|---|---|
| Modelo de difusión (`.safetensors`) | `models/diffusion_models/` |
| Modelo en GGUF | `models/unet/` |
| Text encoder | `models/text_encoders/` |
| VAE | `models/vae/` |
| LoRA | `models/loras/` |

```powershell
docker compose restart
```

> **Antes de descargar varios GB**, comprueba que la URL existe:
> ```powershell
> curl.exe -sIL <url> | Select-String "HTTP/|content-length"
> ```

---

## 9. Límites de este equipo — lo que NO va a funcionar

Comprobado con mediciones, no supuesto:

- **Wan 2.1 14B** (11.6 GB): no cabe en 6 GB. Va por streaming desde RAM y un
  clip de 5 s se fue de **2 horas sin terminar**. Descartado.
- **Modelos tipo Seedance / Veo / Kling**: son propietarios y corren en GPUs de
  datacenter. No son descargables. Solo por API de pago.
- **LTX-Video destilado**: rápido (2.7 min) pero acabado ilustrado, no foto.

**El 1.3B es el punto óptimo real de esta máquina.** Es el modelo de video más
grande que cabe **entero** en 6 GB de VRAM, y por eso rinde bien.

---

## 10. Cuidado con `output/`

Los resultados se guardan en `output/`. Esa carpeta se ha limpiado varias veces
durante las pruebas. **Mueve a otra carpeta lo que quieras conservar**, por
ejemplo `output/_buenos/`.

---

## 11. Audio: música, ambiente y voz

Además de vídeo, el proyecto genera las tres capas de audio de un tráiler. Todas
se mezclan al final con ffmpeg (que está dentro del contenedor `comfyui`).

### 🎵 Música — ACE-Step (dentro de ComfyUI)
Genera música instrumental (y voces cantadas) desde etiquetas de estilo + letra.
- Nodos: `CheckpointLoaderSimple` (ace_step) → `TextEncodeAceStepAudio` (tags +
  lyrics) → `EmptyAceStepLatentAudio` (segundos) → `KSampler` (50 pasos, cfg 5,
  `euler`) → `VAEDecodeAudio` → `SaveAudio`.
- Estilo Ghibli que funcionó: `studio ghibli, orchestral, gentle piano, strings,
  flute, wordless female vocalise, Joe Hisaishi inspired`.
- ~80 s por pieza de 28 s.

### 🍃 Ambiente — MMAudio (dentro de ComfyUI)
Sonido de campo (viento, lluvia, agua) generado desde el propio vídeo. **Solo
ambiente, no música ni voces.**
- **No generes más de ~15 s de audio de una vez** (revienta la VRAM de 6 GB).
  Genera por tramos y libera memoria entre pasadas:
  ```powershell
  Invoke-RestMethod -Uri "http://localhost:8188/free" -Method Post -Body '{"unload_models":true,"free_memory":true}' -ContentType "application/json"
  ```

### 🗣️ Voz narrada — OmniVoice (contenedor aparte, http://localhost:3900)
TTS local en español. Es un **servicio Docker independiente** en la carpeta
`omnivoice/`.

```powershell
cd omnivoice
docker compose up -d      # arranca OmniVoice
docker compose down       # lo para
```

**Reglas de oro de OmniVoice (aprendidas a la fuerza):**
1. **SOLO funciona en GPU.** En CPU se cuelga a 0 % sin generar. El compose ya
   trae GPU configurada.
2. **Comparte la GPU con ComfyUI.** Antes de generar voz, **para ComfyUI**:
   ```powershell
   cd ..            # raiz del proyecto
   docker compose stop comfyui
   # ...generar la voz...
   docker compose start comfyui
   ```
3. Generar voz por API:
   ```powershell
   curl.exe http://localhost:3900/v1/audio/speech -H "Content-Type: application/json" -d '{\"input\":\"Tu frase en español\",\"response_format\":\"wav\",\"language\":\"es\"}' --output voz.wav
   ```
   O usa la **interfaz web en http://localhost:3900** (más cómoda: voces,
   clonación, ajustes).

### ✂️ Mezclar las capas (ffmpeg)
Ejemplo de las técnicas usadas en el tráiler final:
- **Unir planos con fundido:** `xfade` (vídeo) + `acrossfade` (audio).
- **Ducking** (la música baja al hablar la voz): `sidechaincompress`.
- **Sin clipping y volumen parejo:** `alimiter` + `loudnorm=I=-16:TP=-1.5`.

> ffmpeg se ejecuta dentro del contenedor:
> ```powershell
> docker compose exec comfyui bash -c "cd /app/ComfyUI/output && ffmpeg ..."
> ```

---

## Referencia rápida

```powershell
# --- ComfyUI (video, musica, ambiente) ---
docker compose up -d                    # arrancar
docker compose down                     # parar
docker compose restart                  # recargar tras añadir modelos
docker compose logs --tail 20 comfyui   # ver log

# --- OmniVoice (voz) ---
cd omnivoice; docker compose up -d       # arrancar OmniVoice
docker compose stop comfyui              # ¡liberar GPU antes de generar voz!

nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
```

| | |
|---|---|
| UI ComfyUI (vídeo/audio) | http://localhost:8188 |
| UI OmniVoice (voz) | http://localhost:3900 |
| Calidad final vídeo | 832×480, 81 frames, 30 pasos, cfg 6.0, `uni_pc`/`simple`, shift 8.0 |
| Preview | igual pero 14 pasos, cfg 1.0, LoRA 0.3 |
| Música (ACE-Step) | 28 s, 50 pasos, cfg 5.0, `euler` |
| Salida | `output/*.mp4` a 16 fps |
