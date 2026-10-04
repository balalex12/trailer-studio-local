#!/usr/bin/env python3
"""trailer.py - orquestador de la pipeline de trailers locales (ComfyUI + OmniVoice + ffmpeg).

Un solo archivo, sin dependencias (solo libreria estandar de Python 3.8+).
Lee un storyboard JSON (ver examples/) y ejecuta la pipeline por etapas:

    doctor   comprueba servicios, nodos y modelos de ComfyUI
    plan     muestra que se va a generar y cuanto tardara
    video    un clip por escena (Wan 2.1 1.3B)        -> ComfyUI
    music    pista de musica (ACE-Step)                -> ComfyUI
    voice    narracion (OmniVoice)                     -> contenedor aparte, GPU exclusiva
    mix      montaje final con ffmpeg (xfade, ducking, loudnorm)
    status   que artefactos existen ya (--json para maquinas / LLMs)
    run      video -> music -> voice -> mix

Cada etapa es idempotente: si el artefacto ya existe se salta (usa --force para rehacerlo).
Pensado para usarse a mano o desde un agente LLM con acceso a shell (ver docs/ORCHESTRATION.md).
"""
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
OMNIVOICE = os.environ.get("OMNIVOICE_URL", "http://127.0.0.1:3900").rstrip("/")
CONTAINER_OUTPUT = "/app/ComfyUI/output"  # donde ./output esta montado en el contenedor comfyui
CLIENT_ID = uuid.uuid4().hex

DEFAULT_NEGATIVE = (
    "bright colors, oversaturated, overexposed, static, blurred details, subtitles, text, "
    "watermark, logo, worst quality, low quality, JPEG compression residue, ugly, deformed, "
    "disfigured, malformed limbs, extra fingers, fused fingers, poorly drawn hands, "
    "poorly drawn face, mutated, motionless frame, cluttered background"
)

DEFAULTS = {
    "models": {
        "unet": "wan2.1_t2v_1.3B_fp16.safetensors",
        "clip": "umt5-xxl-encoder-Q5_K_M.gguf",
        "vae": "wan_2.1_vae.safetensors",
        "lora": "Wan21_CausVid_bidirect2_T2V_1_3B_lora_rank32.safetensors",
        "ace_step": "ace_step_v1_3.5b.safetensors",
    },
    "style": {"prompt_prefix": "", "prompt_suffix": "", "negative": DEFAULT_NEGATIVE},
    "video": {
        "width": 832, "height": 480, "frames": 81, "fps": 16,
        "steps": 30, "cfg": 6.0, "sampler": "uni_pc", "scheduler": "simple",
        "shift": 8.0, "seed": 12345,
        # modo --preview: LoRA CausVid, rapido pero menos realista (solo para iterar prompts)
        "preview": {"steps": 14, "cfg": 1.0, "lora_strength": 0.3},
    },
    "music": {
        "tags": "", "lyrics": "[instrumental]", "seconds": 28,
        "steps": 50, "cfg": 5.0, "sampler": "euler", "scheduler": "simple",
        "shift": 5.0, "seed": 12345,
    },
    "narration": {"language": "es", "voice": None, "lines": []},
    "ambience": {"file": None},
    "mix": {
        "transition": "fade", "crossfade": 0.5,
        "music_volume": 0.9, "voice_volume": 1.0, "ambience_volume": 0.5,
        "duck": True, "fade_in": 1.0, "fade_out": 2.0, "target_lufs": -16,
    },
}

# Tiempos medidos en una Quadro RTX 3000 (6 GB), en minutos. Solo orientativos.
EST_MIN = {"clip": 24, "clip_preview": 6, "music": 1.5, "voice_line": 2, "mix": 1}


class Fail(Exception):
    """Error esperado: se muestra el mensaje y se sale con codigo 1."""


def log(msg):
    print(f"[trailer] {msg}", flush=True)


# --------------------------------------------------------------------------- proyecto

def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_project(path):
    path = Path(path)
    if not path.is_file():
        raise Fail(f"No existe el storyboard: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Fail(f"JSON invalido en {path}: {e}")
    p = deep_merge(DEFAULTS, raw)
    if not raw.get("name"):
        raise Fail("El storyboard necesita un campo 'name'.")
    scenes = raw.get("scenes") or []
    if not scenes:
        raise Fail("El storyboard necesita al menos una escena en 'scenes'.")
    ids = [s.get("id") for s in scenes]
    if None in ids or len(set(ids)) != len(ids):
        raise Fail("Cada escena necesita un 'id' unico (ej. 's01').")
    for s in scenes:
        if not s.get("prompt"):
            raise Fail(f"La escena {s['id']} no tiene 'prompt'.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(s["id"])):
            raise Fail(f"Id de escena invalido '{s['id']}': usa letras, numeros, _ o -.")
    p["scenes"] = scenes
    workdir = Path(raw.get("workdir") or ROOT / "output" / "projects" / raw["name"])
    p["_dir"] = workdir if workdir.is_absolute() else ROOT / workdir
    return p


def paths(p, preview=False):
    d = p["_dir"]
    return {
        "dir": d,
        "clips": d / ("clips_preview" if preview else "clips"),
        "music": d,  # music.<ext>, la extension la decide ComfyUI
        "voice": d / "voice",
        "final": d / ("final_preview.mp4" if preview else "final.mp4"),
    }


def find_music(p):
    for ext in (".flac", ".mp3", ".wav", ".ogg"):
        f = p["_dir"] / f"music{ext}"
        if f.exists():
            return f
    return None


def voice_files(p):
    return [paths(p)["voice"] / f"line_{i + 1:02d}.wav" for i in range(len(p["narration"]["lines"]))]


def scene_seed(p, scene, idx):
    return int(scene.get("seed", p["video"]["seed"] + idx))


def clip_seconds(p):
    return p["video"]["frames"] / p["video"]["fps"]


# --------------------------------------------------------------------------- HTTP

def http(method, url, payload=None, timeout=60, raw=False):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:1500]
        raise Fail(f"{method} {url} -> HTTP {e.code}\n{detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise Fail(f"Sin conexion con {url} ({reason})") from None
    return body if raw else json.loads(body or b"null")


def service_up(url):
    """True si el servicio responde algo (incluso un 404)."""
    try:
        urllib.request.urlopen(url, timeout=5)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- grafos de ComfyUI (formato API)

def wan_graph(p, scene, idx, preview=False):
    """Grafo de Wan 2.1 T2V 1.3B. Equivale a workflows/Wan21_T2V_1.3B_*.json."""
    m, v, st = p["models"], p["video"], p["style"]
    steps, cfg = v["steps"], v["cfg"]
    model_src = ["1", 0]
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": m["unet"], "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": m["clip"], "type": "wan"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
    }
    if preview:
        pv = v["preview"]
        steps, cfg = pv["steps"], pv["cfg"]
        g["11"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": ["1", 0], "lora_name": m["lora"], "strength_model": pv["lora_strength"]}}
        model_src = ["11", 0]
    text = " ".join(x for x in (st["prompt_prefix"], scene["prompt"], st["prompt_suffix"]) if x).strip()
    negative = scene.get("negative", st["negative"])
    frames = int(scene.get("frames", v["frames"]))
    if (frames - 1) % 4:
        raise Fail(f"Escena {scene['id']}: 'frames' debe ser multiplo de 4 mas 1 (81, 121...), tiene {frames}.")
    g.update({
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": model_src, "shift": v["shift"]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["3", 0]}},
        "7": {"class_type": "EmptyHunyuanLatentVideo",
              "inputs": {"width": v["width"], "height": v["height"], "length": frames, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
            "seed": scene_seed(p, scene, idx), "steps": steps, "cfg": cfg,
            "sampler_name": v["sampler"], "scheduler": v["scheduler"], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
        "10": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["9", 0], "frame_rate": v["fps"], "loop_count": 0,
            "filename_prefix": f"{p['name']}/{'preview_' if preview else ''}{scene['id']}",
            "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 19,
            "save_metadata": True, "pingpong": False, "save_output": True}},
    })
    return g


def ace_graph(p):
    """Grafo de ACE-Step 3.5B (musica). Basado en el workflow de ejemplo de ComfyUI; ver docs."""
    m, mu = p["models"], p["music"]
    if not mu["tags"]:
        raise Fail("music.tags esta vacio: describe el estilo (ej. 'orchestral, gentle piano, strings').")
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": m["ace_step"]}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": mu["shift"]}},
        "3": {"class_type": "TextEncodeAceStepAudio", "inputs": {
            "clip": ["1", 1], "tags": mu["tags"], "lyrics": mu["lyrics"], "lyrics_strength": 1.0}},
        "4": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["3", 0]}},
        "5": {"class_type": "EmptyAceStepLatentAudio", "inputs": {"seconds": mu["seconds"], "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
            "seed": mu["seed"], "steps": mu["steps"], "cfg": mu["cfg"],
            "sampler_name": mu["sampler"], "scheduler": mu["scheduler"], "denoise": 1.0}},
        "7": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveAudio", "inputs": {"audio": ["7", 0], "filename_prefix": f"{p['name']}/music"}},
    }


def all_graphs(p):
    """Grafos representativos para validar contra /object_info."""
    s0 = p["scenes"][0]
    out = {"video": wan_graph(p, s0, 0), "video (preview)": wan_graph(p, s0, 0, preview=True)}
    if p["music"]["tags"]:
        out["music"] = ace_graph(p)
    return out


# --------------------------------------------------------------------------- ComfyUI

def combo_options(spec):
    """Opciones de un input tipo combo en /object_info (formato viejo y nuevo), o None."""
    if not isinstance(spec, list) or not spec:
        return None
    head = spec[0]
    if isinstance(head, list):
        return head
    if head == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None


def validate_graph(graph, info):
    """Devuelve una lista de problemas: nodos ausentes, inputs desconocidos, modelos/valores inexistentes."""
    problems = []
    for nid, node in graph.items():
        cls = node["class_type"]
        if cls not in info:
            problems.append(f"nodo {nid}: la clase '{cls}' no esta instalada (falta un custom node?)")
            continue
        spec = {**info[cls]["input"].get("required", {}), **info[cls]["input"].get("optional", {})}
        for name, value in node["inputs"].items():
            if name not in spec:
                problems.append(f"{cls}: input desconocido '{name}' (ComfyUI cambio la API?)")
                continue
            opts = combo_options(spec[name])
            if opts is not None and not isinstance(value, list) and value not in opts:
                shown = ", ".join(map(str, opts[:6])) + (" ..." if len(opts) > 6 else "")
                problems.append(f"{cls}.{name}: '{value}' no esta disponible. Opciones: [{shown}]")
    return problems


def free_vram():
    try:
        http("POST", f"{COMFY}/free", {"unload_models": True, "free_memory": True})
    except Fail:
        pass


def output_files(outputs):
    files = []
    for node in outputs.values():
        for val in node.values():
            if isinstance(val, list):
                files += [x for x in val if isinstance(x, dict) and "filename" in x and x.get("type") == "output"]
    return files


def run_graph(graph, label, timeout):
    """Envia el grafo a ComfyUI y espera a que termine. Devuelve el dict de outputs."""
    r = http("POST", f"{COMFY}/prompt", {"prompt": graph, "client_id": CLIENT_ID})
    if r.get("node_errors"):
        raise Fail(f"ComfyUI rechazo el grafo de '{label}':\n{json.dumps(r['node_errors'], indent=2)[:1500]}")
    pid = r["prompt_id"]
    t0, last_print, failures = time.time(), 0, 0
    log(f"{label}: en cola (prompt_id {pid[:8]})")
    while True:
        try:
            h = http("GET", f"{COMFY}/history/{pid}")
            failures = 0
        except Fail:
            failures += 1
            if failures >= 5:
                raise Fail("Se perdio la conexion con ComfyUI mientras generaba (se reinicio el contenedor?).")
            h = {}
        entry = h.get(pid)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                detail = json.dumps(msgs[-1][1], indent=2)[:1500] if msgs else "sin detalle"
                raise Fail(f"'{label}' fallo en ComfyUI (suele ser falta de VRAM):\n{detail}")
            if entry.get("outputs"):
                log(f"{label}: listo en {(time.time() - t0) / 60:.1f} min")
                return entry["outputs"]
        elapsed = time.time() - t0
        if elapsed > timeout:
            raise Fail(f"'{label}' supero el timeout de {timeout / 60:.0f} min (usa --timeout).")
        if elapsed - last_print >= 60:
            log(f"{label}: generando... {elapsed / 60:.0f} min")
            last_print = elapsed
        time.sleep(5)


def download(fileinfo, dest):
    q = urllib.parse.urlencode({k: fileinfo.get(k, "") for k in ("filename", "subfolder", "type")})
    data = http("GET", f"{COMFY}/view?{q}", raw=True, timeout=300)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def pick(files, exts, label):
    for f in files:
        if f["filename"].lower().endswith(exts):
            return f
    raise Fail(f"ComfyUI no devolvio ningun archivo {exts} para '{label}' (outputs: {[f['filename'] for f in files]})")


# --------------------------------------------------------------------------- etapas

def cmd_video(p, a):
    pa = paths(p, a.preview)
    todo = [(i, s) for i, s in enumerate(p["scenes"]) if not a.scene or s["id"] in a.scene]
    if a.scene and len(todo) != len(a.scene):
        raise Fail(f"Escenas desconocidas: {set(a.scene) - {s['id'] for _, s in todo}}")
    for i, s in todo:
        dest = pa["clips"] / f"{s['id']}.mp4"
        if dest.exists() and not a.force:
            log(f"{s['id']}: ya existe, se salta ({dest.name})")
            continue
        label = f"escena {s['id']} ({i + 1}/{len(p['scenes'])}){' [preview]' if a.preview else ''}"
        outs = run_graph(wan_graph(p, s, i, a.preview), label, a.timeout)
        download(pick(output_files(outs), (".mp4", ".webm", ".mov"), label), dest)
        log(f"{s['id']}: guardado en {dest}")
    free_vram()


def cmd_music(p, a):
    existing = find_music(p)
    if existing and not a.force:
        log(f"musica: ya existe, se salta ({existing.name})")
        return
    outs = run_graph(ace_graph(p), "musica", a.timeout)
    f = pick(output_files(outs), (".flac", ".mp3", ".wav", ".ogg"), "musica")
    dest = p["_dir"] / ("music" + Path(f["filename"]).suffix.lower())
    download(f, dest)
    log(f"musica: guardada en {dest}")
    free_vram()


def compose(*args, cwd=ROOT):
    if not shutil.which("docker"):
        raise Fail("Hace falta Docker en el PATH para cambiar de servicio en la GPU (o usa --no-gpu-switch).")
    r = subprocess.run(["docker", "compose", *args], cwd=cwd)
    if r.returncode:
        raise Fail(f"docker compose {' '.join(args)} fallo (codigo {r.returncode}).")


def wait_for(url, what, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if service_up(url):
            return
        time.sleep(5)
    raise Fail(f"{what} no respondio en {seconds}s ({url}).")


def cmd_voice(p, a):
    lines = p["narration"]["lines"]
    if not lines:
        log("voz: el storyboard no tiene narration.lines, se salta")
        return
    targets = voice_files(p)
    pending = [(i, t) for i, t in enumerate(targets) if a.force or not t.exists()]
    if not pending:
        log("voz: todas las lineas ya existen, se salta")
        return
    switch = not a.no_gpu_switch
    if switch:
        log("voz: liberando la GPU (parando ComfyUI) y arrancando OmniVoice...")
        compose("stop", "comfyui")
        compose("up", "-d", cwd=ROOT / "omnivoice")
    try:
        wait_for(OMNIVOICE, "OmniVoice", 900)  # la primera vez descarga ~2.4 GB de modelo
        n = p["narration"]
        for i, dest in pending:
            body = {"input": lines[i]["text"], "response_format": "wav", "language": n["language"]}
            if n.get("voice"):
                body["voice"] = n["voice"]
            log(f"voz: linea {i + 1}/{len(lines)} ...")
            audio = http("POST", f"{OMNIVOICE}/v1/audio/speech", body, timeout=900, raw=True)
            if audio[:4] != b"RIFF":
                raise Fail(f"OmniVoice no devolvio un WAV valido: {audio[:200]!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(audio)
            log(f"voz: guardada {dest.name}")
    finally:
        if switch:
            log("voz: devolviendo la GPU a ComfyUI...")
            subprocess.run(["docker", "compose", "stop"], cwd=ROOT / "omnivoice")
            subprocess.run(["docker", "compose", "start", "comfyui"], cwd=ROOT)


# --- ffmpeg

def ffmpeg_runner():
    """(argv base, usa_contenedor). Prefiere un ffmpeg local; si no, el del contenedor comfyui."""
    exe = shutil.which("ffmpeg")
    if exe:
        return [exe], False
    if shutil.which("docker"):
        return ["docker", "compose", "exec", "-T", "comfyui", "ffmpeg"], True
    raise Fail("No hay ffmpeg: instala uno local o levanta el contenedor comfyui (docker compose up -d).")


def to_runner_path(path, in_container):
    if not in_container:
        return str(path)
    try:
        rel = Path(path).resolve().relative_to((ROOT / "output").resolve())
    except ValueError:
        raise Fail(f"Con ffmpeg en el contenedor, {path} debe estar dentro de ./output (workdir por defecto).")
    return f"{CONTAINER_OUTPUT}/{rel.as_posix()}"


def probe_duration(base, in_container, path):
    r = subprocess.run([*base, "-hide_banner", "-i", to_runner_path(path, in_container)],
                       capture_output=True, text=True, cwd=ROOT)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise Fail(f"No pude leer la duracion de {path}:\n{r.stderr[-500:]}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def build_mix_filter(durs, music, ambience, voices, mx):
    """Construye el filter_complex. Devuelve (filtro, duracion_total, tiene_audio).

    Orden de inputs: clips 0..n-1, luego musica, ambiente y lineas de voz (si existen)."""
    n, xf = len(durs), float(mx["crossfade"])
    parts, cur, acc = [], "[0:v]", durs[0]
    for i in range(1, n):
        off = acc - xf
        parts.append(f"{cur}[{i}:v]xfade=transition={mx['transition']}:duration={xf}:offset={off:.3f}[v{i}]")
        cur, acc = f"[v{i}]", off + durs[i]
    total = acc
    parts.append(f"{cur}format=yuv420p[v]")

    idx, fmt = n, "aformat=sample_rates=48000:channel_layouts=stereo"
    mixed = []
    if music:
        fo = float(mx["fade_out"])
        parts.append(f"[{idx}:a]atrim=0:{total:.3f},afade=t=in:d={mx['fade_in']},"
                     f"afade=t=out:st={max(total - fo, 0):.3f}:d={fo},volume={mx['music_volume']},{fmt}[mus]")
        mixed.append("[mus]")
        idx += 1
    if ambience:
        parts.append(f"[{idx}:a]atrim=0:{total:.3f},volume={mx['ambience_volume']},{fmt}[amb]")
        mixed.append("[amb]")
        idx += 1
    if voices:
        vl = []
        for k, (start, _) in enumerate(voices):
            ms = int(float(start) * 1000)
            parts.append(f"[{idx}:a]adelay={ms}:all=1,volume={mx['voice_volume']},{fmt}[vo{k}]")
            vl.append(f"[vo{k}]")
            idx += 1
        parts.append(f"{''.join(vl)}amix=inputs={len(vl)}:normalize=0[voice]" if len(vl) > 1
                     else f"{vl[0]}anull[voice]")
        if music and mx["duck"]:
            parts.append("[voice]asplit=2[vsc][vmix]")
            parts.append("[mus][vsc]sidechaincompress=threshold=0.04:ratio=10:attack=15:release=350[musd]")
            mixed = ["[musd]" if m == "[mus]" else m for m in mixed] + ["[vmix]"]
        else:
            mixed.append("[voice]")
    if not mixed:
        return ";".join(parts), total, False
    body = (f"{''.join(mixed)}amix=inputs={len(mixed)}:normalize=0:duration=longest,"
            if len(mixed) > 1 else f"{mixed[0]}")
    tail = f"loudnorm=I={mx['target_lufs']}:TP=-1.5:LRA=11,alimiter=limit=0.95,atrim=0:{total:.3f}[a]"
    parts.append(body + ("" if len(mixed) > 1 else "anull,") + tail)
    return ";".join(parts), total, True


def cmd_mix(p, a):
    pa = paths(p, a.preview)
    clips = [pa["clips"] / f"{s['id']}.mp4" for s in p["scenes"]]
    missing = [c.name for c in clips if not c.exists()]
    if missing:
        raise Fail(f"Faltan clips: {missing}. Ejecuta primero: trailer.py video <storyboard>{' --preview' if a.preview else ''}")
    music = find_music(p)
    amb = None
    if p["ambience"]["file"]:
        amb = Path(p["ambience"]["file"])
        amb = amb if amb.is_absolute() else p["_dir"] / amb
        if not amb.exists():
            raise Fail(f"ambience.file no existe: {amb}")
    voices = []
    for line, f in zip(p["narration"]["lines"], voice_files(p)):
        if f.exists():
            voices.append((line.get("start", 0), f))
        else:
            log(f"aviso: falta {f.name}; el mix saldra sin esa linea (ejecuta 'voice')")
    out = Path(a.out) if a.out else pa["final"]
    if out.exists() and not a.force:
        log(f"mix: {out} ya existe, se salta (usa --force)")
        return
    base, in_c = ffmpeg_runner()
    durs = [probe_duration(base, in_c, c) for c in clips]
    flt, total, has_audio = build_mix_filter(durs, music, amb, voices, p["mix"])
    cmd = [*base, "-hide_banner", "-loglevel", "warning", "-stats", "-y"]
    for c in clips:
        cmd += ["-i", to_runner_path(c, in_c)]
    for extra in ([music] if music else []) + ([amb] if amb else []) + [f for _, f in voices]:
        cmd += ["-i", to_runner_path(extra, in_c)]
    cmd += ["-filter_complex", flt, "-map", "[v]"]
    cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if has_audio else ["-an"]
    cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", to_runner_path(out, in_c)]
    out.parent.mkdir(parents=True, exist_ok=True)
    log(f"mix: {len(clips)} clips, {total:.1f}s, audio: "
        f"{'musica ' if music else ''}{'ambiente ' if amb else ''}{'voz' if voices else ''}".rstrip(", "))
    if a.print_cmd:
        print(json.dumps(cmd, indent=1))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        raise Fail(f"ffmpeg fallo (codigo {r.returncode}). Revisa el filtro con --print-cmd.")
    log(f"mix: listo -> {out}")


# --- informativos

def artifacts(p):
    items = []
    for s in p["scenes"]:
        items.append(("clip", s["id"], paths(p)["clips"] / f"{s['id']}.mp4"))
    items.append(("music", "music", find_music(p) or p["_dir"] / "music.flac"))
    for i, f in enumerate(voice_files(p)):
        items.append(("voice", f"line_{i + 1:02d}", f))
    items.append(("final", "final", paths(p)["final"]))
    return items


def cmd_status(p, a):
    rows = [{"kind": k, "id": i, "path": str(f), "exists": f.exists()} for k, i, f in artifacts(p)]
    if a.json:
        print(json.dumps({"project": p["name"], "workdir": str(p["_dir"]), "artifacts": rows}, indent=2))
        return
    print(f"Proyecto: {p['name']}   carpeta: {p['_dir']}")
    for r in rows:
        print(f"  [{'x' if r['exists'] else ' '}] {r['kind']:<6} {r['id']}")


def cmd_plan(p, a):
    n = len(p["scenes"])
    clip = clip_seconds(p)
    xf = p["mix"]["crossfade"]
    total = n * clip - (n - 1) * xf
    lines = len(p["narration"]["lines"])
    est = n * EST_MIN["clip"] + (EST_MIN["music"] if p["music"]["tags"] else 0) + lines * EST_MIN["voice_line"] + EST_MIN["mix"]
    print(f"Proyecto : {p['name']}")
    print(f"Escenas  : {n} x {clip:.1f}s ({p['video']['width']}x{p['video']['height']}, {p['video']['frames']} frames @ {p['video']['fps']} fps)")
    print(f"Duracion : ~{total:.0f}s con crossfade de {xf}s")
    print(f"Musica   : {'si, ' + str(p['music']['seconds']) + 's' if p['music']['tags'] else 'no (music.tags vacio)'}")
    print(f"Voz      : {lines} linea(s)   Ambiente: {'si' if p['ambience']['file'] else 'no'}")
    print(f"Tiempo   : ~{est / 60:.1f} h de GPU (calidad final; referencia RTX 3000 6 GB)")
    print(f"Preview  : ~{(n * EST_MIN['clip_preview']) / 60:.1f} h solo de video con --preview")
    print(f"Carpeta  : {p['_dir']}")
    for i, s in enumerate(p["scenes"]):
        print(f"  {s['id']}  seed={scene_seed(p, s, i)}  {s['prompt'][:70]}...")


def cmd_doctor(p, a):
    ok = True
    print(f"ComfyUI   {COMFY}")
    try:
        stats = http("GET", f"{COMFY}/system_stats", timeout=10)
        dev = (stats.get("devices") or [{}])[0]
        vram = dev.get("vram_total", 0) / 1024 ** 3
        print(f"  OK  {dev.get('name', '?')}  VRAM {vram:.1f} GB")
        info = http("GET", f"{COMFY}/object_info", timeout=60)
        for label, g in all_graphs(p).items():
            problems = validate_graph(g, info)
            if problems:
                ok = False
                print(f"  XX  grafo '{label}':")
                for pr in problems:
                    print(f"        - {pr}")
            else:
                print(f"  OK  grafo '{label}': nodos, inputs y modelos disponibles")
    except Fail as e:
        print(f"  --  no disponible: {str(e).splitlines()[0]}")
        print("      (normal si ComfyUI esta parado para dar la GPU a OmniVoice)")
    print(f"OmniVoice {OMNIVOICE}")
    print(f"  {'OK' if service_up(OMNIVOICE) else '--'}  {'responde' if service_up(OMNIVOICE) else 'no responde (normal si ComfyUI tiene la GPU)'}")
    try:
        base, in_c = ffmpeg_runner()
        print(f"ffmpeg    OK  {'en el contenedor comfyui' if in_c else base[0]}")
    except Fail as e:
        ok = False
        print(f"ffmpeg    XX  {e}")
    print(f"Docker    {'OK' if shutil.which('docker') else '--  no esta en el PATH (necesario para cambiar la GPU entre ComfyUI y OmniVoice)'}")
    if not ok:
        raise Fail("doctor encontro problemas (ver arriba).")


def cmd_run(p, a):
    a.scene = None
    cmd_video(p, a)
    cmd_music(p, a)
    cmd_voice(p, a)
    cmd_mix(p, a)


# --------------------------------------------------------------------------- CLI

def build_parser():
    ap = argparse.ArgumentParser(prog="trailer.py", description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, **flags):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("project", nargs="?", default=str(ROOT / "examples" / "ghibli_teaser.json"),
                        help="storyboard JSON (por defecto examples/ghibli_teaser.json)")
        sp.add_argument("--force", action="store_true", help="rehacer aunque el artefacto exista")
        sp.add_argument("--timeout", type=int, default=3 * 3600, help="segundos maximos por trabajo en ComfyUI")
        if flags.get("preview"):
            sp.add_argument("--preview", action="store_true", help="LoRA CausVid: rapido pero menos realista")
        if flags.get("scene"):
            sp.add_argument("--scene", action="append", help="solo esta escena (repetible)")
        if flags.get("gpu"):
            sp.add_argument("--no-gpu-switch", action="store_true",
                            help="no parar/arrancar contenedores: tu gestionas la GPU")
        if flags.get("mix"):
            sp.add_argument("--out", help="ruta de salida del mp4 final")
            sp.add_argument("--print-cmd", action="store_true", help="imprime el comando ffmpeg")
        if flags.get("json"):
            sp.add_argument("--json", action="store_true", help="salida JSON")
        sp.set_defaults(fn=fn)

    add("doctor", cmd_doctor, "comprobar servicios, nodos y modelos")
    add("plan", cmd_plan, "resumen y tiempo estimado")
    add("status", cmd_status, "artefactos existentes", json=True)
    add("video", cmd_video, "generar clips (Wan 2.1)", preview=True, scene=True)
    add("music", cmd_music, "generar musica (ACE-Step)")
    add("voice", cmd_voice, "generar narracion (OmniVoice)", gpu=True)
    add("mix", cmd_mix, "montaje final con ffmpeg", preview=True, mix=True)
    add("run", cmd_run, "video -> music -> voice -> mix", preview=True, gpu=True, mix=True)
    return ap


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args()
    try:
        args.fn(load_project(args.project), args)
    except Fail as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrumpido. Lo ya generado se conserva; relanza el mismo comando para continuar.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
