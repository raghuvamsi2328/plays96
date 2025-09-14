// Utility functions

// Format bytes to human-readable format
export function formatBytes(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Generate unique torrent ID from magnet URI
export function generateTorrentId(magnetURI) {
  return Buffer.from(magnetURI).toString('base64').slice(0, 16);
}

// Get file extension
export function getFileExtension(filename) {
  return filename.slice((filename.lastIndexOf(".") - 1 >>> 0) + 2);
}

// Check if file is video
export function isVideoFile(filename) {
  return /\.(mp4|avi|mkv|mov|wmv|flv|webm)$/i.test(filename);
}

// Check if file is audio
export function isAudioFile(filename) {
  return /\.(mp3|flac|wav|aac|ogg|wma)$/i.test(filename);
}

// Parse range header for video streaming
export function parseRange(range, fileSize) {
  if (!range) return null;
  
  const parts = range.replace(/bytes=/, "").split("-");
  const start = parseInt(parts[0], 10);
  const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
  
  return { start, end };
}

// Create error response
export function createErrorResponse(message, status = 500, details = null) {
  const response = { error: message };
  if (details) {
    response.details = details;
  }
  return { status, body: response };
}

// Safe JSON stringify (handles circular references)
export function safeStringify(obj) {
  const seen = new WeakSet();
  return JSON.stringify(obj, (key, val) => {
    if (val != null && typeof val === "object") {
      if (seen.has(val)) {
        return "[Circular]";
      }
      seen.add(val);
    }
    return val;
  });
}

// Get current timestamp
export function getCurrentTimestamp() {
  return new Date().toISOString();
}

// Sleep utility
export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
