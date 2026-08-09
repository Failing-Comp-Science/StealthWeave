import { test, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// 91 KB H.264 clip from the evaluation corpus. A real video cover forces the
// server capacity probe (network round trip), unlike PNG/BMP which are computed
// client-side. The test stalls that request beyond the client's 10s
// VIDEO_TIMEOUT_MS so CapacityTimeoutError fires and the page keeps Encode
// enabled — the server re-verifies fit at encode time (see capacity-api.ts).
const VIDEO_FIXTURE = path.resolve(
  __dirname,
  "../../../../evaluation/test_corpus/cover_video.mp4",
);

test("video capacity probe timeout keeps Encode enabled", async ({ page }) => {
  // Stall the capacity request well past the 10s client-side timeout. The
  // browser aborts the fetch itself at 10s, so the route handler only needs to
  // outlive it; fulfilling afterwards is harmless (the abort wins). The URL
  // carries query params (?payload_type=..&preset=..), so a regex is required —
  // Playwright globs must match the whole URL including the query string.
  await page.route(/\/api\/stego\/capacity(?:\?.*)?$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 15_000));
    try {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    } catch {
      // Client aborted the fetch at its 10s timeout — expected.
    }
  });

  await page.goto("/encode");
  await page.setInputFiles('[data-testid="input-cover-encode"]', {
    name: "cover_video.mp4",
    mimeType: "video/mp4",
    buffer: fs.readFileSync(VIDEO_FIXTURE),
  });

  // The stalled probe times out client-side -> the warning alert surfaces and
  // analysis stops (the payload options are still derived from the cover kind).
  await expect(page.getByTestId("capacity-timeout")).toBeVisible({ timeout: 25_000 });
  await expect(page.getByTestId("capacity-timeout")).toContainText("timed out");

  // A text payload is enough to re-enable Encode: the server re-verifies fit.
  await page.getByTestId("payload-type-text").click();
  await page.getByTestId("input-secret-message").fill("timeout smoke test");
  await expect(page.getByTestId("button-encode")).toBeEnabled();
});
