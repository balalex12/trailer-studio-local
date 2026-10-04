# Manejar el estudio con un LLM (o a mano, como un experto)

[🇬🇧 English](ORCHESTRATION.md)

`trailer.py` es aburrido a propósito: comandos sin estado, archivos en disco, códigos de salida claros. Eso lo
convierte en una buena **herramienta para un agente**. El agente es el *director*; ComfyUI, OmniVoice y ffmpeg son
el *equipo*; la GPU es la *única silla del plató* — solo puede sentarse uno a la vez.

```
        tú ── "tráiler de 30 s sobre un farero"
         │
   ┌─────▼──────┐   escribe     ┌──────────────┐
   │ Agente LLM ├──────────────►│ storyboard   │
   │ (shell)    │               └──────┬───────┘
   └─────▲──────┘                      │
         │ status --json, códigos      │ trailer.py video | music | voice | mix
         │                             ▼
         └──────────────────── ComfyUI · OmniVoice · ffmpeg  ──►  final.mp4
```

Sirve cualquier modelo que pueda ejecutar comandos y leer archivos: Claude Code, un agente GPT con terminal, un
framework de agentes open source… No necesita **ninguna integración especial**: solo ejecuta `python trailer.py …`.

---

## El contrato

| Comando | Qué hace | Idempotente | Duración típica |
|---|---|---|---|
| `doctor [sb]` | Revisa ComfyUI, clases de nodos, nombres de inputs, **archivos de modelo**, ffmpeg y Docker. Sale con 1 si hay problemas. | ✅ | segundos |
| `plan sb` | Escenas, duración total, estimación de tiempo de GPU. | ✅ | instantáneo |
| `status sb --json` | Qué artefactos existen (`clip`, `music`, `voice`, `final`). | ✅ | instantáneo |
| `video sb [--scene ID]… [--preview]` | Un clip por escena. `--preview` = LoRA rápido, menos fidelidad, carpeta aparte. | salta lo existente | **~24 min/clip** (6 en preview) |
| `music sb` | Pista de ACE-Step. | salta lo existente | ~1.5 min |
| `voice sb` | Líneas de OmniVoice. **Cambia la GPU** (para ComfyUI y lo reinicia al acabar). | salta lo existente | ~2 min/línea |
| `mix sb [--preview] [--out F]` | ffmpeg: crossfades, ducking de la música bajo la voz, −16 LUFS. `--print-cmd` muestra el comando. | salta salvo `--force` | segundos |
| `run sb` | video → music → voice → mix. | ✅ | horas |

- **Códigos de salida:** `0` ok · `1` error esperado (mensaje en stderr, dice qué hacer) · `130` interrumpido.
- **Reanudar:** relanza el mismo comando; lo terminado se salta. `--force` rehace.
- **Archivos:** todo en `output/projects/<nombre>/` → `clips/sNN.mp4`, `clips_preview/`, `music.flac`,
  `voice/line_NN.wav`, `final.mp4`. Cámbialo con `"workdir"` en el storyboard.
- **Trabajos largos:** un clip tarda ~24 min. Ejecuta en segundo plano / con timeout largo y consulta
  `status --json`. Nunca lances dos etapas de GPU a la vez.

## Bucle recomendado para el agente

1. `doctor` — si falla, parar y reportar. No improvisar alrededor de modelos que faltan.
2. Escribir el storyboard (copiar [`examples/ghibli_teaser.json`](../examples/ghibli_teaser.json)).
3. `plan` — decir al humano el tiempo de GPU y **preguntar antes de lanzar un render de varias horas**.
4. `video --preview` en las escenas dudosas. Revisarlas (ver *Revisar resultados*), corregir prompts.
5. `video` (calidad final), `music`, `voice`, `mix`.
6. Revisar `final.mp4` y rehacer solo lo flojo: `video --scene s03 --force`, luego `mix --force`.

## Prompts que funcionan en este setup

Salen de pruebas medidas ([detalle](SETUP.es.md)):

- **Realismo → lenguaje fotográfico y mundano**: `50mm f2.8`, `documentary`, `visible skin pores`, `overcast`.
  Evita `cinematic`, `epic`, `golden hour`: empujan a ilustración.
- **Animación → invierte el negativo** (quita `anime, cartoon`, añade `photorealistic, live action`) y abre el
  prompt con el estilo (`Hand-drawn 2D anime animation…`).
- **Describe el movimiento**: verbos activos y cosas que se mueven solas (viento, agua, nubes, lluvia, niebla).
  Los prompts pasivos dan clips casi congelados. Añade un movimiento de cámara.
- **Una idea por escena.** 5 s de vídeo no aguantan una trama. Piensa en planos, no en párrafos.
- Deja `frames` en 81 (5 s). Más = más VRAM y tiempo; debe ser múltiplo de 4 más 1.
- **Tags de música**: palabras de estilo separadas por comas. Usa `"lyrics": "[instrumental]"` salvo que quieras
  canto.
- **Narración** corta y pausada: una o dos frases por línea, cada una con su segundo `start`. Comprueba que no
  se solapen (una línea tarda ~3–6 s en decirse).

## Revisar resultados (cuando el modelo no puede ver vídeo)

Extrae fotogramas y míralos — pero recuerda que **15 vs 30 pasos solo se distingue en movimiento**: usa los
fotogramas para detectar errores de composición o estilo, no de calidad temporal:

```bash
ffmpeg -i output/projects/NOMBRE/clips/s01.mp4 -vf "fps=1,scale=416:-1,tile=5x1" -frames:v 1 s01_hoja.jpg
```

El volumen del mezclado final sí es un número comprobable: `ffmpeg -i final.mp4 -af ebur128 -f null -` debe dar
una sonoridad integrada cercana a −16 LUFS.

## Prompt de sistema listo para copiar

> Eres el director de un estudio local de tráilers con IA. Tienes shell y este repositorio.
> Lee primero `docs/ORCHESTRATION.es.md` y `examples/ghibli_teaser.json`.
>
> Reglas:
> 1. Ejecuta `python trailer.py doctor` antes de nada. Si falla, reporta el problema y para.
> 2. Crea el storyboard. Sigue las reglas de prompts de los docs.
> 3. Ejecuta `plan` y dime el tiempo estimado de GPU. **No inicies `video`, `voice` ni `run` sin mi OK**: tardan
>    horas de la única GPU.
> 4. Nunca ejecutes dos etapas de GPU a la vez. `voice` cambia la GPU solo; no pares contenedores a mano.
> 5. Comandos largos: ejecútalos en segundo plano y consulta `python trailer.py status <storyboard> --json`.
> 6. Usa `--preview` para iterar prompts; solo después renderiza en calidad final.
> 7. Si un comando falla, lee el error, corrige el storyboard o avísame. No edites `trailer.py` para esquivar
>    modelos que faltan o errores de VRAM: repórtalos.
> 8. Al final dame la ruta de `final.mp4` y una lista de qué rehacerías y por qué.

## A mano (expertos)

Cada etapa tiene su equivalente manual — el script solo automatiza:

| Etapa | Equivalente manual |
|---|---|
| vídeo | Carga `workflows/Wan21_T2V_1.3B_6GB.json` en ComfyUI, cambia el prompt, *Queue*. |
| música | Nodos ACE-Step en ComfyUI (ver [MANUAL.md §11](../MANUAL.md)). |
| voz | `docker compose stop comfyui` → `cd omnivoice && docker compose up -d` → `curl http://localhost:3900/v1/audio/speech -H "Content-Type: application/json" -d '{"input":"…","response_format":"wav","language":"es"}' --output voz.wav` |
| ambiente | Custom node MMAudio (≤15 s por pasada en 6 GB). Pon `ambience.file` en el storyboard para incluirlo en la mezcla. |
| mezcla | `python trailer.py mix sb --print-cmd` imprime el comando ffmpeg exacto para adaptarlo. |

Combínalo: genera los clips a mano, déjalos en `clips/` con nombre `sNN.mp4` y deja que `trailer.py mix` termine.

## Límites, con honestidad

- El agente no puede juzgar la calidad del movimiento con fotogramas fijos, y generar clips es lento: un agente
  que lo rehace todo "por si acaso" quema horas. Dile que rehaga escenas sueltas.
- `doctor` es tu red de seguridad: si ComfyUI o un custom node cambian su API, te indica el nodo e input exactos
  en vez de fallar a mitad del render.
- El ambiente (MMAudio) no está automatizado. Es la capa menos fiable en 6 GB.
