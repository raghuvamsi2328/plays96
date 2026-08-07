# Torrent Streaming API Documentation

## Overview
This is a comprehensive FastAPI-based API for torrent streaming with automatic video selection, FFmpeg HLS conversion, and real-time monitoring. The API accepts torrent hashes and handles magnet URI construction internally.

**Base URL:** `https://localhost:6991` (or your deployed server URL)

This document is intended for direct use from another page, static site, or embedded frontend. The public API returns absolute URLs when the request comes through an HTTPS proxy that forwards `X-Forwarded-Proto` and `X-Forwarded-Host`.

---

## Public API v1

Use these endpoints for external frontends, GitHub Pages, mobile clients, and default media players. The older `/api/torrents` and `/api/stream` routes still work for the built-in test page.

If your frontend is hosted on GitHub Pages over HTTPS, expose this backend over HTTPS too. Browsers block HTTPS pages from calling HTTP APIs even when CORS is enabled.

### HTTPS With Nginx Proxy Manager

Create a DNS record such as `play.server96.com` pointing to your server, then add a Proxy Host in Nginx Proxy Manager:

```text
Domain Names: play.server96.com
Scheme: http
Forward Hostname / IP: your backend host, Docker service name, or server LAN IP
Forward Port: 6991
Cache Assets: off
Block Common Exploits: on
Websockets Support: off
```

On the SSL tab, request a Let's Encrypt certificate and enable Force SSL. HTTP/2 is fine to enable. Turn on HSTS only after you confirm the domain works over HTTPS.

The public API builds absolute stream URLs from `X-Forwarded-Proto` and `X-Forwarded-Host`, which Nginx Proxy Manager normally sends. If you use a custom Nginx config, keep these headers:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
```

### API Index
```http
GET /api/v1
```

Returns endpoint URLs for the server.

### Add Torrent
```http
POST /api/v1/torrents
Content-Type: application/json
```

Request body may use any one of these field names:

```json
{
  "magnet": "magnet:?xt=urn:btih:..."
}
```

Also accepted: `magnet_link` or `link`.

Response:

```json
{
  "id": "infohash",
  "info_hash": "infohash",
  "infoHash": "infohash",
  "name": "Torrent name",
  "status": "downloading",
  "progress": 12.3,
  "stream_url": "https://play.server96.com/api/v1/torrents/infohash/stream.m3u8",
  "playlist_url": "https://play.server96.com/api/v1/torrents/infohash/playlist.m3u",
  "download_url": "https://play.server96.com/api/stream/infohash/download/0",
  "files": [
    {
      "index": 0,
      "name": "video.mkv",
      "size": 123456789,
      "length": 123456789,
      "is_video": true,
      "is_default_stream": true,
      "stream_mode": "hls",
      "stream_url": "https://play.server96.com/api/stream/infohash?file_index=0",
      "playlist_url": "https://play.server96.com/api/v1/torrents/infohash/playlist.m3u?file_index=0",
      "download_url": "https://play.server96.com/api/stream/infohash/download/0"
    }
  ]
}
```

### List Torrents
```http
GET /api/v1/torrents
```

### Torrent Details
```http
GET /api/v1/torrents/{torrent_id}
```

### HLS Stream URL
```http
GET /api/v1/torrents/{torrent_id}/stream.m3u8
```

This redirects to the backend HLS stream and can be used with HLS.js, Safari, VLC, MPV, and other HLS-capable players.

### Per-File Stream URL
```http
GET /api/stream/{torrent_id}?file_index={file_index}
```

This streams a specific file from the torrent. Video files use HLS when available; non-video files are served inline. If you are integrating from another page, use the `stream_url` returned in the file object.

### Per-File Playlist URL
```http
GET /api/v1/torrents/{torrent_id}/playlist.m3u?file_index={file_index}
```

This returns a playlist that targets one file index. Use it when you want a media player to open a specific file directly.

### Per-File Download URL
```http
GET /api/stream/{torrent_id}/download/{file_index}
```

This returns the selected file as an attachment. Use it for a download button in another page.

### External Player Playlist
```http
GET /api/v1/torrents/{torrent_id}/playlist.m3u
```

Returns an M3U playlist containing the HLS stream URL. This is the easiest URL to open in a default media player.

### Remove Torrent
```http
DELETE /api/v1/torrents/{torrent_id}
```

### Download File
```http
GET /api/stream/{torrent_id}/download/{file_index}
```

Returns the selected file as an attachment.

### GitHub Pages Example

```js
const serverUrl = 'https://play.server96.com';

async function addAndOpen(magnet) {
  const response = await fetch(`${serverUrl}/api/v1/torrents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ magnet })
  });

  const torrent = await response.json();
  const defaultFile = torrent.default_file || torrent.files.find((file) => file.is_default_stream) || torrent.files[0];
  window.open(defaultFile?.playlist_url || torrent.playlist_url, '_blank');
}

async function downloadFile(torrentId, fileIndex) {
  window.open(`${serverUrl}/api/stream/${torrentId}/download/${fileIndex}`, '_blank');
}

async function renderTorrentFiles(torrent) {
  const list = document.getElementById('file-list');
  list.innerHTML = '';

  torrent.files.forEach((file) => {
    const item = document.createElement('li');
    item.textContent = `${file.name} (${file.is_video ? 'video' : 'file'})`;

    const streamButton = document.createElement('button');
    streamButton.textContent = 'Stream';
    streamButton.onclick = () => window.open(file.stream_url, '_blank');

    const downloadButton = document.createElement('button');
    downloadButton.textContent = 'Download';
    downloadButton.onclick = () => window.open(file.download_url, '_blank');

    item.appendChild(streamButton);
    item.appendChild(downloadButton);
    list.appendChild(item);
  });
}
```

### Other Page Integration Example

Use this pattern when the UI lives on a different page from the backend:

```html
<input id="magnet" placeholder="magnet:?xt=urn:btih:..." />
<button id="add">Add Torrent</button>
<div id="status"></div>
<ul id="file-list"></ul>

<script>
  const apiBase = 'https://play.server96.com';

  async function addTorrent() {
    const magnet = document.getElementById('magnet').value.trim();
    const status = document.getElementById('status');

    const response = await fetch(`${apiBase}/api/v1/torrents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ magnet })
    });

    const torrent = await response.json();
    if (!response.ok) {
      status.textContent = torrent.detail || 'Unable to add torrent';
      return;
    }

    status.textContent = torrent.name;

    const list = document.getElementById('file-list');
    list.innerHTML = '';
    torrent.files.forEach((file) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <strong>${file.name}</strong>
        <a href="${file.stream_url}" target="_blank" rel="noreferrer">Stream</a>
        <a href="${file.download_url}" target="_blank" rel="noreferrer">Download</a>
      `;
      list.appendChild(li);
    });

    const defaultFile = torrent.default_file || torrent.files.find((file) => file.is_default_stream) || torrent.files[0];
    if (defaultFile && defaultFile.playlist_url) {
      window.open(defaultFile.playlist_url, '_blank');
    }
  }

  document.getElementById('add').addEventListener('click', addTorrent);
</script>
```

### Compatibility Aliases

For simple migration from Peerflix-style clients:

```http
POST /torrents              { "link": "magnet:?xt=urn:btih:..." }
GET  /torrents
GET  /torrents/{torrent_id}
GET  /torrents/{torrent_id}/files
```

`POST /torrents` returns an array with one torrent object so existing code that reads `responseData[0].infoHash` can keep working.

---

## API Endpoints

### Torrent Management

#### Add Torrent
```http
POST /api/torrents/
```

**Request Body:**
```json
{
  "magnet_uri": "magnet:?xt=urn:btih:179b3b176a2df09e1d1deee9b52e78ad85ec270c"
}
```

**Response:**
```json
{
  "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "name": "Example Movie",
  "status": "downloading",
  "progress": 0,
  "files": [],
  "added_at": "2025-11-02T10:30:00.000Z"
}
```

#### Get All Torrents
```http
GET /api/torrents/
```

**Response:**
```json
[
  {
    "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
    "name": "Example Movie",
    "status": "downloading",
    "progress": 45.6,
    "files": [
      {
        "index": 0,
        "name": "movie.mp4",
        "size": 1073741824,
        "progress": 45.6,
        "is_video": true
      }
    ],
    "added_at": "2025-11-02T10:30:00.000Z"
  }
]
```

#### Get Torrent Details
```http
GET /api/torrents/{torrent_id}
```

**Response:**
```json
{
  "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "name": "Example Movie",
  "status": "downloading",
  "progress": 45.6,
  "files": [
    {
      "index": 0,
      "name": "movie.mp4",
      "size": 1073741824,
      "progress": 45.6,
      "is_video": true
    }
  ],
  "added_at": "2025-11-02T10:30:00.000Z"
}
```

#### Remove Torrent
```http
DELETE /api/torrents/{torrent_id}
```

**Response:**
```json
{
  "message": "Torrent removed successfully"
}
```

### Streaming

#### Get HLS Stream
```http
GET /api/stream/{torrent_id}
```

**Response:**
- Returns an M3U8 playlist file
- Content-Type: application/vnd.apple.mpegurl

#### Get HLS Segment
```http
GET /api/stream/{torrent_id}/{segment}
```

**Response:**
- Returns a .ts segment file
- Content-Type: video/MP2T

## Status Values

### Torrent Status
- `downloading` - Actively downloading
- `error` - Error occurred
- `removed` - Torrent was removed

## Python Integration Example

```python
import aiohttp
import asyncio

class TorrentStreamingAPI:
    def __init__(self, base_url="http://localhost:6991"):
        self.base_url = base_url
        
    async def add_torrent(self, magnet_uri):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/torrents/",
                json={"magnet_uri": magnet_uri}
            ) as response:
                return await response.json()
                
    async def get_torrents(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/api/torrents/"
            ) as response:
                return await response.json()
                
    async def get_torrent(self, torrent_id):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/api/torrents/{torrent_id}"
            ) as response:
                return await response.json()
                
    async def remove_torrent(self, torrent_id):
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{self.base_url}/api/torrents/{torrent_id}"
            ) as response:
                return await response.json()
                
    def get_stream_url(self, torrent_id):
        return f"{self.base_url}/api/stream/{torrent_id}"

# Usage Example
async def main():
    api = TorrentStreamingAPI()
    
    # Add torrent
    torrent = await api.add_torrent(
        "magnet:?xt=urn:btih:179b3b176a2df09e1d1deee9b52e78ad85ec270c"
    )
    
    # Poll until ready
    while True:
        status = await api.get_torrent(torrent["id"])
        if status["status"] == "error":
            print("Error:", status.get("error"))
            break
        elif status["progress"] > 5:  # Wait for 5% downloaded
            print("Ready to stream!")
            stream_url = api.get_stream_url(torrent["id"])
            print("Stream URL:", stream_url)
            break
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
```

## HTML5 Video Player Integration

```html
<video id="player" controls>
  <source src="/api/stream/{torrent_id}?file_index={file_index}" type="application/x-mpegURL">
    Your browser does not support HLS video.
</video>

<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<script>
    const video = document.getElementById('player');
  const videoSrc = '/api/stream/{torrent_id}?file_index={file_index}';
    
    if (Hls.isSupported()) {
        const hls = new Hls();
        hls.loadSource(videoSrc);
        hls.attachMedia(video);
    }
    else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = videoSrc;
    }
</script>
```

## Error Handling

### HTTP Status Codes
- `200` - Success
- `404` - Torrent/segment not found
- `500` - Server error

### Error Response Format
```json
{
  "detail": "Error message"
}
```

## Best Practices

1. **HLS Support**: Use HLS.js for browser compatibility
2. **Progress Monitoring**: Poll torrent status before streaming
3. **Error Handling**: Implement proper error handling
4. **Cleanup**: Remove torrents when done
5. **Timeouts**: Set appropriate timeouts for API calls

## Docker Deployment

```yaml
version: '3'
services:
  torrent-streamer:
    build: .
    ports:
      - "6991:6991"
    volumes:
      - ./downloads:/app/downloads
      - ./hls:/app/hls
    environment:
      - PORT=6991
      - DOWNLOAD_PATH=/app/downloads
      - HLS_PATH=/app/hls
      - WARM_CACHE_TIMEOUT_MINUTES=60
```

This API documentation provides everything needed to integrate the torrent streaming service into your application, with a focus on the Python/FastAPI implementation and HLS streaming.