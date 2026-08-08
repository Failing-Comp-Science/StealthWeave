import { useRef, useState, type ChangeEvent, type DragEvent, type ReactNode, type RefObject } from "react";
import { Check, FileImage, FileText, Film, Upload, X } from "lucide-react";
import { formatBytes, formatDuration } from "@/lib/format";

export type DropFileKind = "image" | "video" | "text";

export interface DropFile {
  file: File;
  url: string;
  kind: DropFileKind;
  width?: number;
  height?: number;
  durationSec?: number;
  bitrateKbps?: number;
}

const KIND_ACCEPT: Record<DropFileKind, string> = {
  image: "image/png,image/jpeg,image/webp,image/bmp,image/gif",
  video: "video/mp4,video/webm,video/quicktime,video/x-matroska,video/ogg",
  text: "text/plain,text/markdown,text/html,application/json,text/csv,.txt,.md,.html",
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

function detectKind(file: File, allowed: DropFileKind[]): DropFileKind | null {
  const mime = file.type.toLowerCase();
  const name = file.name.toLowerCase();
  if (allowed.includes("image") && mime.startsWith("image/")) return "image";
  if (allowed.includes("video") && mime.startsWith("video/")) return "video";
  if (allowed.includes("text") && (mime.startsWith("text/") || mime === "application/json" || /\.(txt|md|html|json|csv)$/.test(name))) return "text";
  return null;
}

function buildDropFile(file: File, kind: DropFileKind): Promise<DropFile> {
  const url = URL.createObjectURL(file);
  const base = { file, url, kind };
  if (kind === "image") {
    return new Promise((resolve) => {
      const image = new Image();
      image.onload = () => resolve({ ...base, width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => resolve(base);
      image.src = url;
    });
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
  onSelect: (file: File) => void;
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
      <input ref={inputRef} type="file" accept={accept} onChange={(event) => event.target.files?.[0] && onSelect(event.target.files[0])} hidden data-testid={inputTestId} />
    </div>
  );
}

interface FileDropZoneProps {
  selected: DropFile | null;
  onSelect: (file: DropFile) => void;
  onClear: () => void;
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
  const process = (file?: File) => {
    if (!file) return;
    const kind = detectKind(file, kinds);
    if (!kind) return;
    void buildDropFile(file, kind).then(onSelect);
  };
  const handleChange = (event: ChangeEvent<HTMLInputElement>) => process(event.target.files?.[0]);
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
          onSelect={process}
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
