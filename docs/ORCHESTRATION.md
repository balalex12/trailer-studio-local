# Driving the studio with an LLM (or by hand, like an expert)

[🇪🇸 Español](ORCHESTRATION.es.md)

`trailer.py` is deliberately boring: stateless commands, files on disk, clear exit codes. That makes it a good
**tool for an agent**. The agent is the *director*; ComfyUI, OmniVoice and ffmpeg are the *crew*; the GPU is the
*one chair on set*; only one person can sit on it at a time.

```
        you ── "30 s trailer about a lighthouse keeper"
         │
   ┌─────▼──────┐   writes      ┌──────────────┐
   │ LLM agent  ├──────────────►│ storyboard   │
   │ (shell)    │               └──────┬───────┘
   └─────▲──────┘                      │
         │ status --json, exit codes   │ trailer.py video | music | voice | mix
         │                             ▼
         └──────────────────── ComfyUI · OmniVoice · ffmpeg  ──►  final.mp4
```

Any model that can run shell commands and read files works: Claude Code, a GPT agent with a terminal tool,
an open-source agent framework… The model needs **no special integration**: it just runs `python trailer.py …`.

---

## The contract

| Command | What it does | Idempotent | Typical duration |
|---|---|---|---|
| `doctor [sb]` | Checks ComfyUI, node classes, input names, **model filenames**, ffmpeg, Docker. Exit 1 on problems. | ✅ | seconds |
| `plan sb` | Scenes, total length, GPU-time estimate. | ✅ | instant |
| `status sb --json` | Which artifacts exist (`clip`, `music`, `voice`, `final`). | ✅ | instant |
| `video sb [--scene ID]… [--preview]` | One clip per scene. `--preview` = fast LoRA, lower fidelity, separate folder. | skips existing | **~24 min/clip** (6 preview) |
| `music sb` | ACE-Step track. | skips existing | ~1.5 min |
| `voice sb` | OmniVoice lines. **Swaps the GPU** (stops ComfyUI, restarts it after). | skips existing | ~2 min/line |
| `mix sb [--preview] [--out F]` | ffmpeg: crossfades, music ducking under voice, −16 LUFS. `--print-cmd` shows the command. | skips unless `--force` | seconds |
| `run sb` | video → music → voice → mix. | ✅ | hours |

- **Exit codes:** `0` ok · `1` expected error (message on stderr, says what to do) · `130` interrupted.
- **Resuming:** re-run the same command; finished artifacts are skipped. `--force` redoes.
- **Files:** everything under `output/projects/<name>/` → `clips/sNN.mp4`, `clips_preview/`, `music.flac`,
  `voice/line_NN.wav`, `final.mp4`. Override with `"workdir"` in the storyboard.
- **Long jobs:** a clip takes ~24 min. Run commands in the background / with a long timeout and poll `status --json`.
  Never start two GPU stages at once.

## Recommended loop for the agent

1. `doctor`: stop and report if it fails. Don't improvise around missing models.
2. Write the storyboard (copy [`examples/ghibli_teaser.json`](../examples/ghibli_teaser.json)).
3. `plan`: tell the human the GPU time and **ask before launching a multi-hour render**.
4. `video --preview` for scenes you're unsure about. Look at them (see *Reviewing results*), fix prompts.
5. `video` (final quality), `music`, `voice`, `mix`.
6. Review `final.mp4`, redo only the weak scenes: `video --scene s03 --force`, then `mix --force`.

## Writing prompts that work on this setup

These come from measured runs ([details](SETUP.es.md)):

- **Realism → mundane photographic language**: `50mm f2.8`, `documentary`, `visible skin pores`, `overcast`.
  Avoid `cinematic`, `epic`, `golden hour`; they push towards illustration.
- **Animation → flip the negative prompt** (remove `anime, cartoon`, add `photorealistic, live action`) and open
  the prompt with the style (`Hand-drawn 2D anime animation…`).
- **Describe the motion**: active verbs and things that move by themselves (wind, water, clouds, rain, mist).
  Passive prompts give near-frozen clips. Add a camera move (`The camera slowly pans…`).
- **One idea per scene.** 5 s of video can't hold a plot. Think shots, not paragraphs.
- Keep `frames` at 81 (5 s). Longer = more VRAM and time; must be a multiple of 4 + 1.
- **Music tags** are comma-separated style words (`orchestral, gentle piano, strings, flute`). Use
  `"lyrics": "[instrumental]"` unless you want singing.
- **Narration** short and slow: one or two sentences per line, each with a `start` second. Check that lines
  don't overlap (a line takes ~3–6 s to speak).

## Reviewing results (when the model can't watch video)

Extract frames and look at them, but remember **15 vs 30 steps only differs in motion**, so use frames to
catch composition/style errors, not temporal quality:

```bash
ffmpeg -i output/projects/NAME/clips/s01.mp4 -vf "fps=1,scale=416:-1,tile=5x1" -frames:v 1 s01_sheet.jpg
```

For the final mix, loudness is a number you can check: `ffmpeg -i final.mp4 -af ebur128 -f null -` should
report integrated loudness close to −16 LUFS.

## Copy-paste system prompt

> You are the director of a local AI trailer studio. You have a shell and this repository.
> Read `docs/ORCHESTRATION.md` and `examples/ghibli_teaser.json` first.
>
> Rules:
> 1. Run `python trailer.py doctor` before anything else. If it fails, report the problem and stop.
> 2. Create the storyboard in `examples/` or a new folder. Follow the prompt-writing rules in the docs.
> 3. Run `plan` and tell me the estimated GPU time. **Do not start `video`, `voice` or `run` without my OK**:
>    they take hours of the only GPU.
> 4. Never run two GPU stages at the same time. `voice` swaps the GPU on its own; do not stop containers manually.
> 5. Long commands: run in the background and poll `python trailer.py status <storyboard> --json`.
> 6. Prefer `--preview` to iterate on prompts; only then render final quality.
> 7. If a command fails, read the error, fix the storyboard or tell me. Do not edit `trailer.py` to work around
>    missing models or VRAM errors, report them.
> 8. At the end, give me the path to `final.mp4` and a list of what you'd redo and why.

## Using it by hand (experts)

Every stage maps to something you can do yourself; the script is only automation:

| Stage | Manual equivalent |
|---|---|
| video | Load `workflows/Wan21_T2V_1.3B_6GB.json` in ComfyUI, change the prompt, *Queue*. |
| music | ACE-Step nodes in ComfyUI (see [MANUAL.md §11](../MANUAL.md)). |
| voice | `docker compose stop comfyui` → `cd omnivoice && docker compose up -d` → `curl http://localhost:3900/v1/audio/speech -H "Content-Type: application/json" -d '{"input":"…","response_format":"wav","language":"es"}' --output voz.wav` |
| ambience | MMAudio custom node (≤15 s per pass on 6 GB). Set `ambience.file` in the storyboard to include it in the mix. |
| mix | `python trailer.py mix sb --print-cmd` prints the exact ffmpeg command to adapt. |

Mix and match: generate clips by hand, drop them in `clips/` named `sNN.mp4`, and let `trailer.py mix` finish.

## Honest limits

- The agent can't judge motion quality from stills, and clip generation is slow; an agent that re-renders
  everything "to be safe" burns hours. Tell it to redo single scenes.
- `doctor` is your safety net: if ComfyUI or a custom node changes its API, it reports the exact node and input
  rather than failing mid-render.
- Ambience (MMAudio) is not automated. It's the least reliable layer on 6 GB.
