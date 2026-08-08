"""Faithful copy of encode_jpeg's closed loop with instrumentation."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
import numpy as np
import _corpus as c
from modules.container import build_container, CompressionPresetId, PayloadType as PT
from modules.capacity.dct_embedder import (
    _analyze, _render_jpeg, _decode_jpeg, _extractor_order, _parity, _snap_block,
    _framing_broken, _residual_exceeds_ecc,
)
from modules.capacity.presets import scaled_luma_table
from modules.capacity._dct import rgb_to_luma
from modules.capacity._channel import frame_bitstream

PW = "harpocrates-bench"

def main():
    import hashlib
    kind = sys.argv[1] if len(sys.argv) > 1 else "photo-like"
    preset = sys.argv[2] if len(sys.argv) > 2 else "standard"
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 32
    rgb = c.image_cover(kind, 512)
    qf = {"light": 95, "standard": 85, "heavy": 75}[preset]
    nbx = 64
    delta = 2.0
    table = scaled_luma_table(qf)
    payload = c.make_text_payload(size, "message")
    cont = build_container(payload, PT.TEXT_MESSAGE,
                           compression_preset=CompressionPresetId.STANDARD,
                           password=PW)
    bits = frame_bitstream(cont, delta)
    nbits = int(bits.size)
    luma = rgb_to_luma(rgb)
    q, nz, dc = _analyze(luma, table)
    best_count = None
    no_progress = 0
    iters = 0
    hist = []
    while True:
        iters += 1
        if iters > 100:
            print("iters>100"); break
        jpeg = _render_jpeg(rgb, q, table, qf)
        dl, qtab = _decode_jpeg(jpeg)
        if iters == 1:
            print("  iter1 jpeg", hashlib.sha256(jpeg).hexdigest()[:12],
                  "luma", hashlib.sha256(np.asarray(dl).tobytes()).hexdigest()[:12],
                  "qtab", hashlib.sha256(np.asarray(qtab).tobytes()).hexdigest()[:12],
                  "bits", hashlib.sha256(bits.tobytes()).hexdigest()[:12])
        dq, dnz, ddc = _analyze(dl, qtab)
        order = _extractor_order(dnz, ddc, nbits)
        mismatches = []
        for i in range(nbits):
            by, bx = divmod(int(order[i]), nbx)
            want = int(bits[i])
            if _parity(dq, dnz, by, bx, delta) != want:
                mismatches.append(i)
                _snap_block(q, by, bx, delta, want, decoded=(dq, dnz), table=table)
        hist.append(len(mismatches))
        if not mismatches:
            break
        if best_count is None or len(mismatches) < best_count:
            best_count = len(mismatches)
            no_progress = 0
        else:
            no_progress += 1
        if no_progress >= 12:
            if _framing_broken(mismatches) or _residual_exceeds_ecc(mismatches, len(cont)):
                break
            break
    print(kind, preset, size, "iters", iters, "hist", hist[:16],
          "final_jpeg", hashlib.sha256(jpeg).hexdigest()[:12])

if __name__ == "__main__":
    main()
