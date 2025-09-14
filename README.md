# Torrent Streaming Server with FFmpeg Remuxing

A Node.js torrent streaming server that uses libtorrent for downloading and FFmpeg for remuxing video files to MP4 for better web compatibility.

## Features

- 🚀 Fast torrent downloading with libtorrent
- 🎬 Automatic remuxing of MKV/AVI to MP4 using FFmpeg
- 📱 Range request support for video seeking
- 🔄 Real-time torrent status and progress
- 🌐 RESTful API for easy integration
- 📊 Health monitoring and logging

## API Endpoints

### Add Torrent
```http
POST /add-torrent
Content-Type: application/json

{
  "magnetURI": "magnet:?xt=urn:btih:...",
  "name": "Movie Name (optional)"
}
```

### Get Torrent Status
```http
GET /torrent/:id
```

### List All Torrents
```http
GET /torrents
```

### Stream File
```http
GET /stream/:torrentId/:fileIndex
```

### Remove Torrent
```http
DELETE /torrent/:id
```

### Health Check
```http
GET /health
```

## Deployment in Portainer

1. **Create Stack in Portainer**:
   - Go to Stacks → Add Stack
   - Upload this folder or paste the docker-compose.yml

2. **Environment Variables**:
   - `PORT`: Server port (default: 3000)
   - `NODE_ENV`: production

3. **Volume Mapping**:
   - `./downloads:/app/downloads` - For downloaded files

## Usage Example

```javascript
// Add a torrent
const response = await fetch('/add-torrent', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    magnetURI: 'magnet:?xt=urn:btih:...',
    name: 'My Movie'
  })
});

const { torrentId } = await response.json();

// Check status
const status = await fetch(`/torrent/${torrentId}`).then(r => r.json());

// Stream the first video file
const streamUrl = `/stream/${torrentId}/0`;
```

## FFmpeg Remuxing

The server automatically detects video formats and applies appropriate remuxing:

- **MKV, AVI, WMV, FLV** → Remuxed to MP4 with AAC audio
- **MP4, MOV, WEBM** → Direct streaming (no remuxing)
- **Audio formats** → Direct streaming

## Performance Notes

- Video streams are copied (no re-encoding) for fast processing
- Only audio is transcoded to AAC when necessary
- Supports progressive download and streaming
- Range requests enabled for video seeking

## Legal Notice

This software is for educational purposes. Only use with content you have legal rights to download and distribute.
