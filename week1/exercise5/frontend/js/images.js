export const ACCEPTED_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

// Longest side sent to the model. Larger images cost more tokens without helping most models.
const MAX_SIDE = 1568;
const LOSSY_QUALITY = 0.9;

export class ImageError extends Error {}

export function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${+(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** MIME type and decoded size of a base64 data URL, without decoding it. */
export function dataUrlInfo(url) {
  const match = /^data:([^;,]+);base64,/.exec(url);
  if (!match) return null;
  const base64Length = url.length - match[0].length;
  const padding = url.endsWith("==") ? 2 : url.endsWith("=") ? 1 : 0;
  return { mimeType: match[1], sizeBytes: Math.floor((base64Length * 3) / 4) - padding };
}

/** The placeholder that stands in for base64 data in the context viewer, e.g. "[image/png · 182 KB]". */
export function imagePlaceholder(mimeType, sizeBytes) {
  return `[${mimeType} · ${formatBytes(sizeBytes)}]`;
}

/**
 * Check a file and turn it into a data URL, shrinking it on a canvas first when its longest
 * side is over MAX_SIDE. Throws ImageError with a message for the user.
 */
export async function prepareImage(file, maxBytes) {
  if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
    throw new ImageError(`"${file.name}" is not a supported image. Use PNG, JPEG, WebP or GIF.`);
  }
  if (file.size > maxBytes) {
    throw new ImageError(`"${file.name}" is ${formatBytes(file.size)}. The limit is ${formatBytes(maxBytes)} (MAX_IMAGE_MB).`);
  }

  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new ImageError(`"${file.name}" could not be read as an image.`);
  }

  let blob = file;
  const { width, height } = bitmap;
  const scale = MAX_SIDE / Math.max(width, height);
  if (scale < 1) {
    blob = await resize(bitmap, Math.round(width * scale), Math.round(height * scale), file.type);
  }
  bitmap.close();

  if (blob.size > maxBytes) {
    throw new ImageError(`"${file.name}" is still ${formatBytes(blob.size)} after resizing. The limit is ${formatBytes(maxBytes)}.`);
  }
  return { name: file.name, url: await readAsDataUrl(blob), mimeType: blob.type, sizeBytes: blob.size };
}

async function resize(bitmap, width, height, type) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  context.imageSmoothingQuality = "high";
  context.drawImage(bitmap, 0, 0, width, height);

  // A canvas cannot encode GIF, so a resized GIF becomes a PNG of its first frame. Browsers
  // that cannot encode WebP fall back to PNG on their own; blob.type reports what was produced.
  const target = type === "image/jpeg" || type === "image/webp" ? type : "image/png";
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, target, LOSSY_QUALITY));
  if (!blob) throw new ImageError("The image could not be resized.");
  return blob;
}

function readAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new ImageError("The image could not be read."));
    reader.readAsDataURL(blob);
  });
}
