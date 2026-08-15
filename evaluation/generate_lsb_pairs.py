#!/usr/bin/env python3
"""
Generate paired cover/stego PNGs for sequential-WS evaluation.

Synthetic covers only (no copyrighted corpora). Four sources: the three
``evaluation._corpus`` kinds plus a saturated-region generator. Sequential LSB
uses the production embedder (bit 0, raster prefix). Negative controls and
LSB matching live here as evaluation helpers — they are not product embedders.

Usage:
    python evaluation/generate_lsb_pairs.py --out evaluation/results/ws_pairs --size 512
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "backend"))
for p in (_BACKEND, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import _corpus as corpus
from modules.image_stego import LSBEmbedder

COVER_KINDS = ("photo-like", "texture-grid", "noise", "saturated")
PAYLOAD_BYTES = (1024, 2048, 4096, 8192)
PAYLOAD_FRACS = (0.02, 0.05, 0.08)
DEFAULT_SEED = 20260815


def saturated_cover(size: int, rng: np.random.Generator) -> np.ndarray:
    """Mostly clipped highlights/shadows with a textured mid-tone band."""
    yy, xx = np.mgrid[0:size, 0:size]
    band = (yy > size * 0.35) & (yy < size * 0.65)
    r = np.where(band, 40 + 20 * np.sin(xx / 12.0), np.where(yy < size * 0.35, 252, 4))
    g = np.where(band, 80 + 30 * np.cos(xx / 9.0), np.where(yy < size * 0.35, 248, 8))
    b = np.where(band, 60 + 25 * np.sin((xx + yy) / 15.0), np.where(yy < size * 0.35, 250, 6))
    noise = rng.normal(0.0, 3.0, (size, size, 3))
    return np.clip(np.stack([r, g, b], axis=-1) + noise, 0, 255).astype(np.uint8)


def make_cover(kind: str, size: int, seed: int) -> np.ndarray:
    if kind == "saturated":
        rng = np.random.default_rng(seed)
        return saturated_cover(size, rng)
    return corpus.image_cover(kind, size=size, seed=seed)


def random_payload(n_bytes: int, seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, int(n_bytes), dtype=np.uint8).tobytes()


def encrypted_payload(n_bytes: int, seed: int) -> bytes:
    """AES-GCM ciphertext truncated to ``n_bytes`` (encrypted-looking bits)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    rng = np.random.default_rng(seed)
    key = rng.integers(0, 256, 32, dtype=np.uint8).tobytes()
    nonce = rng.integers(0, 256, 12, dtype=np.uint8).tobytes()
    pt = rng.integers(0, 256, max(int(n_bytes), 16), dtype=np.uint8).tobytes()
    ct = AESGCM(key).encrypt(nonce, pt, None)
    return ct[:n_bytes]


def embed_sequential(cover: np.ndarray, payload: bytes, key: str = "eval") -> np.ndarray:
    return LSBEmbedder(random_order=False, bits_per_channel=1).embed(
        cover, payload, key
    ).stego_media


def embed_random_order(cover: np.ndarray, payload: bytes, key: str = "eval") -> np.ndarray:
    return LSBEmbedder(random_order=True, bits_per_channel=1).embed(
        cover, payload, key
    ).stego_media


def embed_lsb_matching(cover: np.ndarray, n_bits: int, seed: int) -> np.ndarray:
    """Evaluation-only ±1 embedding on a raster prefix. Not a product embedder."""
    rng = np.random.default_rng(seed)
    stego = cover.copy()
    flat = stego.reshape(-1).astype(np.int16)
    n_bits = min(int(n_bits), int(flat.size))
    bits = rng.integers(0, 2, size=n_bits, dtype=np.int16)
    idx = np.arange(n_bits)
    cur_lsb = flat[idx] & 1
    flip = bits != cur_lsb
    direction = rng.choice(np.array([-1, 1], dtype=np.int16), size=n_bits)
    direction = np.where(flat[idx] == 0, 1, direction)
    direction = np.where(flat[idx] == 255, -1, direction)
    flat[idx] = np.where(flip, flat[idx] + direction, flat[idx])
    return np.clip(flat, 0, 255).astype(np.uint8).reshape(cover.shape)


def png_roundtrip(rgb: np.ndarray) -> np.ndarray:
    import io

    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    buf.seek(0)
    with Image.open(buf) as im:
        return np.asarray(im.convert("RGB"))


def jpeg_then_png(rgb: np.ndarray, quality: int = 90) -> np.ndarray:
    import io

    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    with Image.open(buf) as im:
        decoded = np.asarray(im.convert("RGB"))
    return png_roundtrip(decoded)


def write_png(path: str, rgb: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(rgb).save(path, format="PNG")


def pair_plan(*, size: int, covers_per_kind: int, seed: int) -> list[dict]:
    """Manifest rows describing every cover/stego pair to generate."""
    rows: list[dict] = []
    for kind_i, kind in enumerate(COVER_KINDS):
        for i in range(covers_per_kind):
            cover_seed = seed + 1000 * kind_i + i
            cover_id = f"{kind}_{i:03d}"
            cap_bits = size * size * 3
            # Hold-out: even indices are the eval split, odd unused here
            # (formula detector has no train set; still unique covers per row).
            rows.append({
                "cover_id": cover_id,
                "kind": kind,
                "seed": cover_seed,
                "role": "cover_clean",
                "payload_kind": "",
                "payload_bytes": 0,
                "true_payload_bits": 0,
            })
            for nbytes in PAYLOAD_BYTES:
                for pkind, factory_name in (("random", "random"), ("encrypted", "encrypted")):
                    rows.append({
                        "cover_id": cover_id,
                        "kind": kind,
                        "seed": cover_seed,
                        "role": "sequential_lsb",
                        "payload_kind": pkind,
                        "payload_bytes": nbytes,
                        "true_payload_bits": nbytes * 8,
                        "factory": factory_name,
                    })
            for frac in PAYLOAD_FRACS:
                nbytes = max(1, int(cap_bits * frac) // 8)
                rows.append({
                    "cover_id": cover_id,
                    "kind": kind,
                    "seed": cover_seed,
                    "role": "sequential_lsb",
                    "payload_kind": "random",
                    "payload_bytes": nbytes,
                    "true_payload_bits": nbytes * 8,
                    "factory": "random",
                    "payload_frac": frac,
                })
            rows.append({
                "cover_id": cover_id, "kind": kind, "seed": cover_seed,
                "role": "png_reencode", "payload_kind": "", "payload_bytes": 0,
                "true_payload_bits": 0,
            })
            rows.append({
                "cover_id": cover_id, "kind": kind, "seed": cover_seed,
                "role": "jpeg_png", "payload_kind": "", "payload_bytes": 0,
                "true_payload_bits": 0,
            })
            rows.append({
                "cover_id": cover_id, "kind": kind, "seed": cover_seed,
                "role": "random_order_lsb", "payload_kind": "random",
                "payload_bytes": 2048, "true_payload_bits": 2048 * 8,
                "factory": "random",
            })
            rows.append({
                "cover_id": cover_id, "kind": kind, "seed": cover_seed,
                "role": "lsb_matching", "payload_kind": "random",
                "payload_bytes": 2048, "true_payload_bits": 2048 * 8,
            })
    return rows


def materialize_row(row: dict, size: int) -> np.ndarray:
    cover = make_cover(row["kind"], size, int(row["seed"]))
    role = row["role"]
    if role in ("cover_clean",):
        return cover
    if role == "png_reencode":
        return png_roundtrip(cover)
    if role == "jpeg_png":
        return jpeg_then_png(cover)
    nbytes = int(row["payload_bytes"])
    payload_seed = int(row["seed"]) + 17 + nbytes
    if row.get("factory") == "encrypted":
        payload = encrypted_payload(nbytes, payload_seed)
    else:
        payload = random_payload(nbytes, payload_seed)
    if role == "sequential_lsb":
        return embed_sequential(cover, payload)
    if role == "random_order_lsb":
        return embed_random_order(cover, payload)
    if role == "lsb_matching":
        return embed_lsb_matching(cover, nbytes * 8, payload_seed)
    raise ValueError(f"unknown role {role}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(_HERE, "results", "ws_pairs"))
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--covers-per-kind", type=int, default=4)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = pair_plan(
        size=args.size, covers_per_kind=args.covers_per_kind, seed=args.seed
    )
    manifest_path = os.path.join(args.out, "manifest.csv")
    fieldnames = [
        "path", "cover_id", "kind", "seed", "role", "payload_kind",
        "payload_bytes", "true_payload_bits", "y_true",
    ]
    with open(manifest_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for i, row in enumerate(rows):
            rgb = materialize_row(row, args.size)
            y_true = 1 if row["role"] == "sequential_lsb" else 0
            name = f"{i:05d}_{row['cover_id']}_{row['role']}_{row['payload_bytes']}.png"
            path = os.path.join(args.out, name)
            write_png(path, rgb)
            writer.writerow({
                "path": name,
                "cover_id": row["cover_id"],
                "kind": row["kind"],
                "seed": row["seed"],
                "role": row["role"],
                "payload_kind": row.get("payload_kind", ""),
                "payload_bytes": row["payload_bytes"],
                "true_payload_bits": row["true_payload_bits"],
                "y_true": y_true,
            })
    meta = {
        "size": args.size,
        "covers_per_kind": args.covers_per_kind,
        "seed": args.seed,
        "n_rows": len(rows),
        "note": "Do not quote published 0.1–0.4 bpp WS figures as Harpocrates performance.",
    }
    with open(os.path.join(args.out, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"wrote {len(rows)} PNGs + manifest to {args.out}")


if __name__ == "__main__":
    main()
