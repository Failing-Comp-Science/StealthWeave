import { useRef, useState, type ChangeEvent, type DragEvent, type ReactNode, type RefObject } from "react";
import { Check, FileImage, FileText, Film, Upload, X } from "lucide-react";
import { formatBytes, formatDuration } from "@/lib/format";
import { classifyFile, type Modality } from "@/lib/file-classify";
import { sniffImageDimensions } from "@/lib/image-dimensions";

export type DropFileKind = Modality;

export interface DropFile {
  file: File;
  url: string;
  kind: DropFileKind;
  /** Concrete format token from magic-byte sniffing, e.g. "png" | "mp4". */
  format?: string;
  width?: number;
  height?: number;
  durationSec?: number;
  bitrateKbps?: number;
}

const KIND_ACCEPT: Record<DropFileKind, string> = {
  image: "image/png,image/jpeg,image/webp,image/bmp,image/gif,.png,.jpg,.jpeg,.webp,.bmp,.gif",
  video: "video/mp4,video/webm,video/quicktime,video/x-matroska,video/ogg,.mp4,.webm,.mov,.mkv,.m4v,.ogv",
  text: "text/plain,text/markdown,text/html,application/json,text/csv,.txt,.md,.html,.json,.csv,.log",
};

const KIND_BADGES: Record<DropFileKind, string[]> = {
  image: ["PNG", "JPG", "WEBP", "BMP"],
  video: ["MP4", "WEBM", "MOV"],
  text: ["TXT", "MD", "HTML"],
};

const KIND_ICON: Record<DropFileKind, ReactNode> = {
  image: <FileImage size={16} />,
  video: <Film size={16} />,
  text: <FileText size={16} />,
};

async function buildDropFile(file: File, kind: DropFileKind, format?: string): Promise<DropFile> {
  const url = URL.createObjectURL(file);
  const base: DropFile = { file, url, kind, format };
  if (kind === "image") {
    // Phase 1: sniff dimensions from the header bytes (instant) instead of a
    // full ``new Image()`` pixel decode. Returns null dims for exotic formats
    // — the preview still works, only the dimension readout is omitted.
    const head = new Uint8Array(await file.slice(0, 65536).arrayBuffer().catch(() => new ArrayBuffer(0)));
    const dims = sniffImageDimensions(format ?? "", head);
    if (dims) return { ...base, ...dims };
    try {
      const bitmap = await createImageBitmap(file);
      const width = bitmap.width;
      const height = bitmap.height;
      bitmap.close();
      return width > 0 && height > 0 ? { ...base, width, height } : base;
    } catch {
      return base;
    }
  }
  if (kind === "video") {
    return new Promise((resolve) => {
      const video = document.createElement("video");
      video.preload = "metadata";
      video.onloadedmetadata = () => {
        const durationSec = Number.isFinite(video.duration) ? video.duration : undefined;
        resolve({
          ...base,
          width: video.videoWidth,
          height: video.videoHeight,
          durationSec,
          bitrateKbps: durationSec && durationSec > 0 ? Math.round((file.size * 8) / durationSec / 1000) : undefined,
        });
      };
      video.onerror = () => resolve(base);
      video.src = url;
    });
  }
  return Promise.resolve(base);
}

function previewMeta(selected: DropFile) {
  if (selected.kind === "image") return selected.width && selected.height ? `${selected.width} × ${selected.height}` : "IMAGE READY";
  if (selected.kind === "video") return `${selected.durationSec ? formatDuration(selected.durationSec) : "—"} · VIDEO`;
  return "TEXT FILE";
}

function FilePreview({
  selected,
  onClear,
  onReplace,
  inputRef,
  onSelect,
  accept,
  testIdPrefix,
  inputTestId,
  previewTestId,
}: {
  selected: DropFile;
  onClear: () => void;
  onReplace: () => void;
  inputRef: RefObject<HTMLInputElement | null>;
  onSelect: (file: File, resetInput: () => void) => void;
  accept: string;
  testIdPrefix: string;
  inputTestId: string;
  previewTestId: string;
}) {
  return (
    <div className={`selected-image${selected.kind === "text" ? " no-media" : ""}`} data-testid={previewTestId}>
      {selected.kind !== "text" && (
        <div className={selected.kind === "video" ? "preview-video-wrap" : "preview-image-wrap"}>
          {selected.kind === "image" ? (
            <img src={selected.url} alt="Selected file" />
          ) : (
            <video src={selected.url} muted playsInline preload="metadata" data-testid={`preview-video-${testIdPrefix}`} />
          )}
          <div className="image-overlay"><Check size={20} /></div>
        </div>
      )}
      <div className="file-details">
        <div className="file-name">{KIND_ICON[selected.kind]}<strong>{selected.file.name}</strong></div>
        <div className="file-meta">{formatBytes(selected.file.size)} <span>·</span> {previewMeta(selected)}</div>
        <div className="file-actions">
          <button onClick={onReplace} data-testid={`button-replace-${testIdPrefix}`}>Replace</button>
          <button onClick={onClear} className="remove-link" data-testid={`button-remove-${testIdPrefix}`}><X size={13} /> Remove</button>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={(event) => {
          const el = event.target;
          if (el.files?.[0]) onSelect(el.files[0], () => { el.value = ""; });
        }}
        hidden
        data-testid={inputTestId}
      />
    </div>
  );
}

interface FileDropZoneProps {
  selected: DropFile | null;
  onSelect: (file: DropFile) => void;
  onClear: () => void;
  /** Called with a user-facing reason when a dropped file is rejected. */
  onReject?: (reason: string) => void;
  headline: string;
  subline: string;
  cta: string;
  hint?: string;
  formats?: string[];
  kinds?: DropFileKind[];
  testIdPrefix: string;
  stepNumber?: string;
  inputTestId?: string;
  previewTestId?: string;
}

function FileDropZone({
  selected,
  onSelect,
  onClear,
  onReject,
  headline,
  subline,
  cta,
  hint = "or click to browse from your device",
  formats,
  kinds = ["image"],
  testIdPrefix,
  stepNumber = "01",
  inputTestId,
  previewTestId,
}: FileDropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const accept = kinds.map((kind) => KIND_ACCEPT[kind]).join(",");
  const badges = formats ?? kinds.flatMap((kind) => KIND_BADGES[kind]);
  const resolvedInputTestId = inputTestId ?? `input-file-${testIdPrefix}`;
  const resolvedPreviewTestId = previewTestId ?? `preview-file-${testIdPrefix}`;

  /**
   * Classify + accept a picked file. Uses magic-byte sniffing (not just MIME),
   * surfaces a visible reason on rejection instead of silently ignoring the
   * file, and ALWAYS clears the input value afterwards so re-selecting the same
   * file (e.g. after a failed encode) fires a fresh change event.
   */
  const process = (file: File | undefined, resetInput?: () => void) => {
    if (!file) {
      resetInput?.();
      return;
    }
    void classifyFile(file, kinds)
      .then(async (res) => {
        if (res.ok && res.modality) {
          const dropFile = await buildDropFile(file, res.modality, res.format);
          onSelect(dropFile);
        } else {
          onReject?.(res.reason ?? "Unsupported file.");
        }
      })
      .catch(() => onReject?.("Could not read that file."))
      .finally(() => resetInput?.());
  };

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const el = event.target;
    process(el.files?.[0], () => { el.value = ""; });
  };
  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    process(event.dataTransfer.files?.[0]);
  };
  return (
    <div className="upload-module">
      <div className="step-heading">
        <span className="step-number">{stepNumber}</span>
        <div>
          <h2>{headline}</h2>
          <p>{subline}</p>
        </div>
      </div>
      {!selected ? (
        <div
          className={dragging ? "drop-zone dragging" : "drop-zone"}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          data-testid={`dropzone-${testIdPrefix}`}
        >
          <input ref={inputRef} type="file" accept={accept} onChange={handleChange} hidden data-testid={resolvedInputTestId} />
          <span className="drop-icon"><Upload size={22} strokeWidth={1.3} /></span>
          <strong>{cta}</strong>
          <span>{hint}</span>
          <div className="format-badges">{badges.map((format) => <b key={format}>{format}</b>)}</div>
        </div>
      ) : (
        <FilePreview
          selected={selected}
          onClear={onClear}
          onReplace={() => inputRef.current?.click()}
          inputRef={inputRef}
          onSelect={(file, reset) => process(file, reset)}
          accept={accept}
          testIdPrefix={testIdPrefix}
          inputTestId={resolvedInputTestId}
          previewTestId={resolvedPreviewTestId}
        />
      )}
    </div>
  );
}

export { FileDropZone, type FileDropZoneProps };
