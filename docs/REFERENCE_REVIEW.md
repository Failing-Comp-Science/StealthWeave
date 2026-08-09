# Harpocrates — Reference Implementation Review (2026-08-09)

> Companion to `codebase_and_repo_audit.md` (licenses & salvage map) and
> `docs/HOW_IT_WORKS.md` (our own architecture). This file records the
> *behavioral* deep-dive of the reference and competitor implementations
> studied on 2026-08-09: what they actually detect, embed, frame, and fail on —
> and which of their behaviors we should copy, adapt, or deliberately avoid.
>
> Status: written from direct source reading (local `references/`) and live
> GitHub inspection (web repos). Open threads are tagged `[TODO]`.

---

## 0. TL;DR table

| Reference | Modality | Type detection | Capacity logic | Framing / sync | Robustness intent | What we copy / avoid |
|---|---|---|---|---|---|---|
| AlphaSteg (`references/`) | Audio (wave) | Magic bytes | ffmpeg-driven, naive | Custom header + checksum | Streaming only | **Copy:** magic-byte sniffing + media-type inference pattern |
| javid-steganography | Image (PNG/JPEG) | Extension only | Char-count bound (500) / LSB | HSTG-like header + PBKDF2 | PNG=local, JPEG=%QR local | Copy Hamming(7,4); avoid extension-only probing |
| openstego (GPL) | Image | None | Block-DCT tiers | N/A | Local | Ideas only (already documented) |
| Wavest (web) | Acoustic (GGWave) | N/A (mic) | N/A | ggwave frame | Packet-sync | Avoid — not file carrier |
| BUM16 (web) | Acoustic 16-FSK | N/A (mic/WAV) | RLE + CRC32 framing | Goertzel sync | N/A | **Copy:** framing pipeline (RLE→packet→CRC32→modem) as conceptual model |
| HideUrBits (web) | — (covert-flag?) | N/A | N/A | N/A | N/A | [TODO: verify payload format] |

---

## 1. AlphaSteg (`references/AlphaSteg/main.py`) — audio

Read: `main.py` directly (unlicensed — study only).

- **Magic-byte detection is real and complete**: `guess_extension_and_media_type(data: bytes)` at
  `main.py:171` sniffs and completes the classic magic-byte table (JPEG, PNG, GIF, MP3, WAV, etc.) and
  falls back on the original filename extension. Used for **both** media display and output
  correctness (`main.py:769-815`).
- **Uses magic bytes → "carrier detection" even for audio**, which Harpocrates' audio engine currently
  `[TODO: what does ours do — probe?] does not do; ours assumes from UI.
- **Error handling:** returns per-call `False` + user-facing text; never raises mid-UI.
- **Reuse:** the `guess_extension_and_media_type` mapping table (careful — this is implemented
  without copying later; also AlphaStag has no license). Take the structure, write our own table.

## 2. javid-steganography (`references/javid-steganography`)

- **Carrier detection:** the *user's* extension string is the whole "detect"; `.png`/`.jpg` are checked
  at the `_check_compatibility` level (square + path checks, `text-image-advance.py:125`). No sniffing.
- **PNG engine:** `PNGDeflateSteg` embeds into **deflate-compressed data** (not raw pixels) —
  changes zlib stream; decode works if Zoom/WhatsApp don't re-compress. **Robust plan:** internal
  "robust" (JPEG DCT-LSB with 3× redundancy, strength=25 param; carries ~100 chars vs 500) — noisy
  mode at `text-image-advance.py:172-369`. Uses **Hamming(7,4)** (`_hamming_encode`) → robust tier.
- **Capacity:** documented at `README.md:41` "Max capacity 500 chars (text)" (LSB page), lower for
  robust mode. Not a general formula — fixed char ceiling guides the UI warning.
- **Framing:** encrypted salt+payload with CRC32 trailing chunk (`_create_payload`/`_extract_payload`)
  → a HSTG-type pattern matching ours.
- **Copy:** the Hamming(7,4) pattern (MIT) — already flagged in `codebase_and_repo_audit.md` §5.

---

## 3. VideoSteal note

Unchanged from §5/§6 of the audit (MIT). Its ffmpeg/PyAV/evals are the patterns; **>versions** gap
(torch 2.1.2 vs ≥2.3.1) still open [TODO].

---

## 4. Web reference repos (inspected live, GitHub)

### 4.1 Wavest (bennjordan/Wavest)
- Ultrasonic/acoustic modem (ggwave-backed). No carrier-file stego; microphone/speaker transport.
- **Relevant ideas:** packet-based framing + CRC; applies sound via anything that can play audio —
  carrier-independent. Nothing transferable to file stego beyond "our transmission web path uses
  SoundCard → replace with Wavest-style" **Not relevant to current scope (file stego)**.

### 4.2 BUM16 (bennjordan/BUM16)
"Benn's Ultrasonic Modem (16-FSK), 16.5–19 kHz" — browser-based offline modem.

- **Full pipeline view** (from its README mermaid):
  - TX: input text/image → **RLE compression** → packet assembly + **CRC32** → **16-FSK modulator**
    → sine gen + Hanning window → uncompressed WAV.
  - RX: mic/WAV → sliding-window ring buffer → **preamble/sync-tone detection** → Goertzel filter
    array → symbol demod/reassembly → CRC32 → decryption/RLE decompress → output.
- **Useful transferable ideas:** (1) frame every fixed-size header with a sync/preamble; (2) RLE in
  the pre-modulation stage; (3) **CRC32 at the tail of each packet** — identical contract to our
  `PayloadHeader` CRC. Nothing GPL (plain MIT-style code as noted).
- **Reuse:** treat as conceptual — *copy the modular inter-step contract*; the *implementation*
  pipeline would be ours.

### 4.3 HideUrBits (bennjordan/HideUrBits)
- README is a one-line live-link — no description in the README. `[TODO]` needs a live crawl of the
  app is needed to record its carrier/method (it was not inspected past the live-demo link; the
  prior run did not finish the deep dive).

---

## 5. Synthesis for Harpocrates

**Where the reference class of tools (web/audio/copy) are uncluttered, Harpocrates is ahead or even.**
The only clean takeaways:

1. **Magic-byte probing should be a first-class `carrier_detect` step**, before the
   capacity/encode path (AlphaStag does it; javid ships no detector and its docs warn users
   "always include extension"). → aligns with `docs/CARRIER_PRESETS.md`.
2. **License quarantine remains**: reuse patterns only from MIT (`javid-steganography`); reimplement
   Alpha magic detection table from scratch and reference BUM16 framing only conceptually.
3. **Missing in our stack vs. BUM16-style frames:** none (we already carry CRC32+size+password header).
4. **Video engine.** our I-frame keyframe DCT-QIM + H.264 CRF re-encode goes *past* every web
   reference; the gloss-of-following is the local `HOW_IT_WORKS` video section — no new finding.

---

## 6. Open threads (carry to next session)

- [TODO] Finish `HideUrBits` live crawl (payload method); add to §4.3.
- [TODO] Reconcile videoseal torch gap decision (research only, not urgent).
- [TODO] Decide/DRY the backend whose probe method we build: magic-byte sniff in
  `backend/services/probe.py` vs extension fallback.
- [TODO] Extract javid Hamming(7,4) into our `modules/coding.py` (MIT-permitted) & test.

---

*Next prompt in this sequence should open with this file and `codebase_and_repo_audit.md`
accoriding to the standing instructions at the top of the audit.*