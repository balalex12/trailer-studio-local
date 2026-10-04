# Security policy

## Reporting a vulnerability

Please report privately through GitHub: **Security > Report a vulnerability** on this repository. Do not open a public issue for security problems.

## Scope and known risks

- ComfyUI has **no authentication** and custom nodes run arbitrary code. `docker-compose.yml` binds it to `127.0.0.1:8188` only; do not expose that port to a network or the internet.
- Only install the custom nodes listed in `docs/SETUP.es.md`. Review any extra node before installing it.
- Model weights are downloaded from third parties and are not part of this repo; check their licenses and checksums.
- Voice cloning: only use voices you have consent to use.
- `trailer.py` is stdlib-only and calls `docker`/`ffmpeg` with argument lists (no shell).
