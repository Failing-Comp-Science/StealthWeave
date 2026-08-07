# StealthWeave — Harpocrates

Steganography research platform: hide, extract, and analyze hidden data in images,
audio, and links. Python backend (classical stego library + metrics) and a
client-side React/TypeScript frontend sharing a byte-compatible wire format
(HSTG header + AES-256-GCM + CRC32 + LSB).

> **Note:** this repo name is a mirror/renamed upstream of the internal project
> codename *Harpocrates*.

## Repository layout

```
Harpocrates/
├── backend/              Python stego library (LSB, adaptive, audio, link, steganalysis, metrics) + FastAPI scaffold
│   └── modules/          the working stego engines (image/audio/link/steganalysis)
│   └── tests/            pytest suite
├── frontend/             Replit pnpm workspace (React 19 + Vite + shadcn/ui)
│   ├── artifacts/harpocrates/   main client-side app (primary UI)
│   ├── artifacts/api-server/    Express 5 (currently GET /api/healthz only)
│   ├── artifacts/mockup-sandbox/ shadcn component sandbox
│   └── lib/              Orval-generated api clients + api-spec (OpenAPI) + Drizzle db client
├── docs/HOW_IT_WORKS.md  design documentation (PDF generated)
├── message/              sample payloads (HTML + image)
├── codebase_and_repo_audit.md  single source of truth for reuse/build-fresh decisions
└── evaluation/           (empty scaffold: results/ + test_corpus/)
```

## Development

- **Backend:** `cd backend && python -m pytest` (Python 3.9, deps in `requirements.txt`).
- **Frontend:** `cd frontend && pnpm install && pnpm dev` (Node 24, pnpm workspace).

## Git hygiene

See `.gitignore` for what is intentionally excluded. Local-only by design:

- `.venv/`, `node_modules/`, `**/dist/`, build outputs, caches
- `.env*`, secrets, keys (never commit)
- OS/editor droppings (`.DS_Store`, `.idea/`, `.vscode/`)
- `references/` — external third-party repos, studied locally, never vendored (see below)

## References & credits

The `references/` directory contains third-party repositories cloned **locally for
algorithmic study only** and is **not** tracked in this repo (see `.gitignore`). Full
credit to their authors; license details govern any further use:

| Repo | Author / Org | License | Used for |
|---|---|---|---|
| [openstego](https://github.com/syvaidya/openstego) | The OpenStego Project | GPLv2 ⚠️ | DCT/DWT watermarking algorithms (ideas only — do not copy code) |
| [Steganalysis (GBRAS-Net)](https://github.com/BioAITeam/Steganalysis) | BioAITeam | None ⚠️ | CNN steganalysis research reproduction (study only) |
| [AlphaSteg](https://github.com/bennjordan/AlphaSteg) | bennjordan | None ⚠️ | Audio stego FastAPI app (study only) |
| [videoseal](https://github.com/facebookresearch/videoseal) | Meta / Facebook Research | MIT | Neural image/video watermarking (code-reusable patterns) |
| [javid-steganography](https://github.com/Iman/javid-steganography) | Iman | MIT | Image LSB + Hamming(7,4) ECC (code-reusable patterns) |

**License hygiene policy:** openStego (GPLv2) and the unlicensed repos may be read for
algorithmic inspiration and cited, but their code must never be copied into this
codebase. Only MIT-licensed references (videoseal, javid-steganography) are safe to
borrow from.

## License

This project's own code is provided for academic/portfolio use. Third-party material
retains its upstream license (see table above).
