# Torrent Streaming API Documentation

## Overview
This is a comprehensive FastAPI-based API for torrent streaming with automatic video selection, FFmpeg HLS conversion, and real-time monitoring. The API accepts torrent hashes and handles magnet URI construction internally.

**Base URL:** `http://localhost:6991` (or your deployed server URL)

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
    <source src="/api/stream/{torrent_id}" type="application/x-mpegURL">
    Your browser does not support HLS video.
</video>

<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<script>
    const video = document.getElementById('player');
    const videoSrc = '/api/stream/{torrent_id}';
    
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