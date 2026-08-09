/**
 * Reed-Solomon RS(255,223) over GF(256) — byte-compatible port of `reedsolo`
 * 1.7.0 (the library `backend/modules/container.py` uses).
 *
 * Parameters (exactly as `reedsolo.RSCodec(RS_NSYM, nsize=RS_NSIZE)` is
 * instantiated by the backend):
 *   nsize  = 255,  k = nsize - nsym = 223,  nsym = 32
 *   fcr    = 0,    generator = 2,  prim = 0x11D,  c_exp = 8
 *
 * Encoding mirrors `reedsolo.RSCodec.encode`: the message is chunked into
 * 223-byte blocks (the last block may be short) and each block gets 32 parity
 * symbols appended, so the coded length is `n + ceil(n / 223) * 32` — the same
 * `rs_encoded_len` the capacity model uses.
 *
 * Decoding mirrors `reedsolo.RSCodec.decode`: the coded stream is chunked into
 * 255-byte blocks (last block short), each corrected independently. This is
 * byte-exact with the Python side, so a browser-embedded PNG can be extracted
 * by the FastAPI backend and vice versa.
 *
 * Note: when the syndromes are all zero (the lossless LSB channel's normal
 * case) decoding short-circuits to "message = block minus trailing parity",
 * which is exactly what reedsolo returns for an error-free codeword.
 */

export const RS_NSIZE = 255;
export const RS_K = 223;
export const RS_NSYM = RS_NSIZE - RS_K; // 32

const PRIM = 0x11d;
const GENERATOR = 2;
const FCR = 0;
const FIELD_CHARAC = 255;

// GF(256) log/exp tables, generated identically to reedsolo.init_tables.
// gf_exp has 510 entries (doubled) so `gf_log[x] + gf_log[y]` never needs a
// modulo before indexing; gf_log[x] is the discrete log of x base `generator`.
const gfLog = new Uint8Array(256);
const gfExp = new Uint8Array(510);

(function initTables() {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    gfExp[i] = x;
    gfLog[x] = i;
    // multiply x by `generator` (2) with carry-less shift + modulo PRIM.
    x <<= 1;
    if (x & 0x100) x ^= PRIM;
  }
  for (let i = 255; i < 510; i++) gfExp[i] = gfExp[i - 255];
})();

/** GF multiplication (a*b). */
function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return gfExp[gfLog[a] + gfLog[b]];
}

/** GF power (a^p), p may be negative (uses Python-style modulo semantics). */
function gfPow(a: number, p: number): number {
  let idx = ((gfLog[a] * p) % 255 + 255) % 255;
  return gfExp[idx];
}

/** GF inverse (1/a). */
function gfInverse(a: number): number {
  return gfExp[255 - gfLog[a]];
}

/** GF division (a/b). */
function gfDiv(a: number, b: number): number {
  if (b === 0) throw new Error("division by zero");
  if (a === 0) return 0;
  return gfExp[(gfLog[a] + 255 - gfLog[b]) % 255];
}

/** GF subtraction == addition (XOR). */
function gfSub(a: number, b: number): number {
  return a ^ b;
}

/** Polynomial evaluation at x (Horner), lowest-degree coefficient first. */
function gfPolyEval(poly: number[], x: number): number {
  let y = poly[0];
  for (let i = 1; i < poly.length; i++) {
    y = gfMul(y, x) ^ poly[i];
  }
  return y;
}

/** Polynomial addition in GF(256), lowest-degree coefficient first. */
function gfPolyAdd(p: number[], q: number[]): number[] {
  const len = Math.max(p.length, q.length);
  const r = new Array<number>(len).fill(0);
  for (let i = 0; i < p.length; i++) r[i + len - p.length] = p[i];
  for (let i = 0; i < q.length; i++) r[i + len - q.length] ^= q[i];
  return r;
}

/** Polynomial multiplication in GF(256). */
function gfPolyMul(p: number[], q: number[]): number[] {
  const r = new Array<number>(p.length + q.length - 1).fill(0);
  for (let j = 0; j < q.length; j++) {
    const qj = q[j];
    if (qj === 0) continue;
    const lq = gfLog[qj];
    for (let i = 0; i < p.length; i++) {
      const pi = p[i];
      if (pi === 0) continue;
      r[i + j] ^= gfExp[gfLog[pi] + lq];
    }
  }
  return r;
}

/** Generator polynomial g(x) = prod_{i=0}^{nsym-1} (x - alpha^(i+fcr)). */
function generatorPoly(nsym: number): number[] {
  let g = [1];
  for (let i = 0; i < nsym; i++) {
    g = gfPolyMul(g, [1, gfPow(GENERATOR, i + FCR)]);
  }
  return g;
}

const GEN = generatorPoly(RS_NSYM);
// Precompute logs of the generator coefficients (reedsolo.rs_encode_msg).
const LGEN = GEN.map((c) => gfLog[c]);

/**
 * Encode one RS(255,223) block via extended synthetic division.
 * Returns msg + nsym parity bytes (systematic). Mirrors reedsolo.rs_encode_msg.
 */
function rsEncodeBlock(msg: Uint8Array, nsym: number): Uint8Array {
  const out = new Uint8Array(msg.length + nsym);
  out.set(msg, 0);
  // polynomial division: out[i + j] ^= gf_exp[lcoef + lgen[j]]
  for (let i = 0; i < msg.length; i++) {
    const coef = out[i];
    if (coef === 0) continue;
    const lcoef = gfLog[coef];
    for (let j = 1; j < GEN.length; j++) {
      out[i + j] ^= gfExp[lcoef + LGEN[j]];
    }
  }
  // The quotient region holds the original message (already in place).
  out.set(msg, 0);
  return out;
}

/** RS(255,223) encode: chunk into 223-byte blocks, append 32 parity each. */
export function rsEncode(data: Uint8Array): Uint8Array {
  if (data.length === 0) return new Uint8Array(0);
  const blocks = Math.ceil(data.length / RS_K);
  const out = new Uint8Array(data.length + blocks * RS_NSYM);
  for (let b = 0; b < blocks; b++) {
    const start = b * RS_K;
    const len = Math.min(RS_K, data.length - start);
    const block = rsEncodeBlock(data.subarray(start, start + len), RS_NSYM);
    out.set(block, start + b * RS_NSYM);
  }
  return out;
}

/** Compute the syndromes of a received codeword (reedsolo.rs_calc_syndromes). */
function syndromes(msg: Uint8Array, nsym: number): number[] {
  // [0] + [gf_poly_eval(msg, generator^(i+fcr)) for i in range(nsym)]
  const synd = new Array<number>(nsym + 1).fill(0);
  for (let i = 0; i < nsym; i++) {
    synd[i + 1] = gfPolyEval(Array.from(msg), gfPow(GENERATOR, i + FCR));
  }
  return synd;
}

/** Berlekamp-Massey error locator (reedsolo.rs_find_error_locator). */
function findErrorLocator(synd: number[], nsym: number): number[] {
  let errLoc = [1];
  let oldLoc = [1];
  for (let i = 0; i < nsym; i++) {
    const k = i;
    let delta = synd[k];
    for (let j = 1; j < errLoc.length; j++) {
      delta ^= gfMul(errLoc[-(j + 1)], synd[k - j]);
    }
    oldLoc = [...oldLoc, 0];
    if (delta !== 0) {
      if (oldLoc.length > errLoc.length) {
        const newLoc = oldLoc.map((c) => gfMul(c, delta));
        oldLoc = errLoc.map((c) => gfMul(c, gfInverse(delta)));
        errLoc = newLoc;
      }
      errLoc = gfPolyAdd(errLoc, oldLoc.map((c) => gfMul(c, delta)));
    }
  }
  // drop leading zeros
  let start = 0;
  while (start < errLoc.length && errLoc[start] === 0) start++;
  errLoc = errLoc.slice(start);
  const errs = errLoc.length - 1;
  if (errs * 2 > nsym) {
    throw new Error("Too many errors to correct");
  }
  return errLoc;
}

/**
 * Locate errors by brute-force Chien search (reedsolo.rs_find_errors).
 * Returns error positions relative to the START of the codeword.
 */
function findErrors(errLoc: number[], nmess: number): number[] {
  const errs = errLoc.length - 1;
  const positions: number[] = [];
  for (let i = 0; i < nmess; i++) {
    if (gfPolyEval(errLoc, gfPow(GENERATOR, i)) === 0) {
      positions.push(nmess - 1 - i);
    }
  }
  if (positions.length !== errs) {
    throw new Error("Could not locate errors");
  }
  return positions;
}

/** Error evaluator Omega = (Synd * Sigma) mod x^(nsym+1). */
function findErrorEvaluator(synd: number[], errLoc: number[], nsym: number): number[] {
  const product = gfPolyMul(synd, errLoc);
  const end = product.length - (nsym + 1);
  return end >= 0 ? product.slice(end) : product;
}

/** Errata locator polynomial from coefficient positions (reedsolo). */
function rsFindErrataLocator(ePos: number[]): number[] {
  let eLoc = [1];
  for (const p of ePos) {
    // multiply by (1 + generator^p * x)
    eLoc = gfPolyMul(eLoc, [1, gfPow(GENERATOR, p)]);
  }
  return eLoc;
}

/**
 * Forney algorithm: compute error magnitudes and apply them (reedsolo.
 * rs_correct_errata). `errPos` are positions relative to the start of the
 * codeword.
 */
function correctErrata(msg: Uint8Array, synd: number[], errPos: number[]): Uint8Array {
  const out = new Uint8Array(msg);
  // coefficient degrees (reversed from message positions)
  const coefPos = errPos.map((p) => msg.length - 1 - p);
  const errLoc = rsFindErrataLocator(coefPos);
  const errEval = findErrorEvaluator([...synd].reverse(), errLoc, errLoc.length - 1).reverse();
  // X[j] = generator^(field_charac - coefPos[j])  (negative exponent)
  const X: number[] = coefPos.map((c) => gfPow(GENERATOR, -(FIELD_CHARAC - c)));
  for (let i = 0; i < X.length; i++) {
    const XiInv = gfInverse(X[i]);
    // denominator: formal derivative of the errata locator at XiInv
    let errLocPrime = 1;
    for (let j = 0; j < X.length; j++) {
      if (j === i) continue;
      errLocPrime = gfMul(errLocPrime, gfSub(1, gfMul(XiInv, X[j])));
    }
    if (errLocPrime === 0) throw new Error("Forney could not locate errors");
    let y = gfPolyEval([...errEval].reverse(), XiInv);
    y = gfMul(gfPow(X[i], 1 - FCR), y);
    const magnitude = gfDiv(y, errLocPrime);
    out[errPos[i]] ^= magnitude;
  }
  return out;
}

/**
 * Correct one received block; returns the original message bytes (reedsolo.
 * rs_correct_msg). When syndromes are all zero (error-free) this trims the
 * parity without allocating anything else.
 */
function rsCorrectBlock(msg: Uint8Array, nsym: number): Uint8Array {
  const synd = syndromes(msg, nsym);
  let any = false;
  for (const s of synd) {
    if (s !== 0) {
      any = true;
      break;
    }
  }
  if (!any) {
    // error-free codeword: message = block minus trailing parity
    return msg.slice(0, msg.length - nsym);
  }
  const errLoc = findErrorLocator(synd, nsym);
  const errPos = findErrors(errLoc, msg.length);
  const corrected = correctErrata(msg, synd, errPos);
  // verify
  const synd2 = syndromes(corrected, nsym);
  for (const s of synd2) {
    if (s !== 0) throw new Error("Could not correct message");
  }
  return corrected.slice(0, corrected.length - nsym);
}

/** RS(255,223) decode: chunk into 255-byte blocks, correct each. */
export function rsDecode(data: Uint8Array): Uint8Array {
  if (data.length === 0) return new Uint8Array(0);
  const blocks = Math.ceil(data.length / RS_NSIZE);
  const parts: Uint8Array[] = [];
  for (let b = 0; b < blocks; b++) {
    const start = b * RS_NSIZE;
    const len = Math.min(RS_NSIZE, data.length - start);
    parts.push(rsCorrectBlock(data.subarray(start, start + len), RS_NSYM));
  }
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

/**
 * RS(255,223) encoded length of ``n_bytes`` (capacity model, matches the
 * backend's ``rs_encoded_len``).
 */
export function rsEncodedLen(nBytes: number): number {
  if (nBytes <= 0) return 0;
  return nBytes + Math.ceil(nBytes / RS_K) * RS_NSYM;
}
