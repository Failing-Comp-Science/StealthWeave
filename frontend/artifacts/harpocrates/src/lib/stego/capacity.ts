/**
 * Client-side PNG/BMP capacity — Phase 1 (instant, exact).
 *
 * PNG/BMP covers are embedded by the lossless spatial-LSB engine, whose
 * capacity is fully deterministic from the cover geometry (HxW). The backend
 * computes it via ``modules/capacity/image_capacity.spatial_capacity``; this
 * module mirrors that EXACT model in the browser so the UI never needs a
 * network round trip for PNG/BMP covers.
 *
 * Model (backend ``spatial_container_budget`` + ``max_payload_from_container_bytes``):
 *
 *   budget_bytes = (H * W * 3 * bpc) // 8
 *   container_budget = budget_bytes - 14 (v1 header) - 14 (v1 header) - 44 (AES-GCM)
 *   max_payload p s.t. overhead + rs_encoded_len(p) <= container_budget
 *
 *   overhead = 94  (TEXT_MESSAGE: 50-byte fixed header + 44 AES-GCM)
 *   overhead = 190 (TEXT_FILE: + 64-byte filename + 32-byte mime budgets)
 *
 *   rs_encoded_len(n) = n + ceil(n / 223) * 32   (RS(255,223), reedsolo semantics)
 */

const PAYLOAD_HEADER_SIZE = 14; // backend PayloadHeader.SIZE
const AES_GCM_OVERHEAD = 44; // 16 salt + 12 nonce + 16 tag (SteganoCrypto)
const FIXED_HEADER_SIZE = 50; // HSTG v2 fixed header (backend container.py)
const FILENAME_BUDGET = 64; // reserved for TEXT_FILE payload names
const MIME_BUDGET = 32; // reserved for TEXT_FILE mime types
const RS_K = 223; // RS(255,223) data symbols per block
const RS_NSYM = 32; // parity symbols per block
const LOSSLESS_PRESET_ID = "lossless_high_capacity";

export function rsEncodedLen(bytes: number): number {
  if (bytes <= 0) return 0;
  return bytes + Math.ceil(bytes / RS_K) * RS_NSYM;
}

/** Max container bytes the LSB engine can store in an HxW RGB cover (bpc=1). */
export function spatialContainerBudget(height: number, width: number): number {
  const budgetBytes = Math.floor((height * width * 3) / 8);
  return Math.max(0, budgetBytes - PAYLOAD_HEADER_SIZE - PAYLOAD_HEADER_SIZE - AES_GCM_OVERHEAD);
}

/**
 * Largest original payload whose serialized container fits ``containerBudget``.
 * Mirrors ``max_payload_from_container_bytes`` (binary search over payload size).
 */
export function maxPayloadFromContainerBytes(
  containerBudget: number,
  fixedOverhead: number,
  ratio = 1.0,
): number {
  if (containerBudget <= fixedOverhead) return 0;
  let lo = 0;
  let hi = containerBudget - fixedOverhead;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi + 1) / 2);
    const compressed = ratio > 1.0 ? Math.ceil(mid / ratio) : mid;
    if (fixedOverhead + rsEncodedLen(compressed) <= containerBudget) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }
  return lo;
}

/** (TEXT_MESSAGE, TEXT_FILE) container overheads — matches backend `_overheads()`. */
export function containerOverheads(): { message: number; file: number } {
  const message = FIXED_HEADER_SIZE + AES_GCM_OVERHEAD;
  const file = FIXED_HEADER_SIZE + FILENAME_BUDGET + MIME_BUDGET + AES_GCM_OVERHEAD;
  return { message, file };
}

export interface SpatialCapacityRow {
  id: string;
  name: string;
  description: string;
  expected_ber: number;
  survivability_description: string;
  max_bytes_text_message: number;
  max_bytes_text_file: number;
  max_bytes_image: number;
  /** Mirror of the backend spatial_capacity diagnostics. */
  total_blocks: number;
  eligible_blocks: number;
  usable_coeff_slots: number;
}

/** Full LOSSLESS capacity for a PNG/BMP cover of given dimensions. */
export function computeSpatialCapacity(height: number, width: number): SpatialCapacityRow {
  const budget = spatialContainerBudget(height, width);
  const { message, file } = containerOverheads();
  return {
    id: LOSSLESS_PRESET_ID,
    name: "Lossless (PNG/BMP)",
    description:
      "Lossless LSB embedding: full spatial capacity, byte-exact recovery, no re-compression loss.",
    expected_ber: 0.0,
    survivability_description:
      "Survives: lossless round-trips (PNG/BMP re-save), no lossy re-encode",
    max_bytes_text_message: maxPayloadFromContainerBytes(budget, message, 1.0),
    max_bytes_text_file: maxPayloadFromContainerBytes(budget, file, 1.0),
    max_bytes_image: maxPayloadFromContainerBytes(budget, file, 1.0),
    total_blocks: Math.floor(height / 8) * Math.floor(width / 8),
    eligible_blocks: height * width * 3,
    usable_coeff_slots: height * width * 3,
  };
}
