# Instructions for AI agents working in this repo

This repo makes short AI trailers locally on a 6 GB GPU. The orchestrator is `trailer.py`.

- Read `docs/ORCHESTRATION.md` first (contract, loop, prompt rules, system prompt).
- Always run `python trailer.py doctor` before generating anything.
- **Ask the human before `video`, `voice` or `run`** — they take hours of the single GPU.
- Never run two GPU stages at once; `voice` swaps ComfyUI/OmniVoice itself.
- Generated media goes to `output/projects/` (git-ignored). Don't commit models, `custom_nodes/` or outputs.
- `trailer.py` is stdlib-only Python 3.8+: keep it dependency-free.
