# Torrent Streaming API Documentation

## Overview
This is a comprehensive API for torrent streaming with automatic video selection, FFmpeg remuxing, and real-time monitoring. The API accepts torrent hashes and handles magnet URI construction internally.

**Base URL:** `http://localhost:6991` (or your deployed server URL)

---

## 🚀 Getting Started

### Prerequisites
- Server running on port 6991
- Torrent hash (40 hex characters or 32 base32 characters)

### Quick Test
```bash
curl -X GET http://localhost:6991/health
```

---

## 📡 API Endpoints

### 1. System Endpoints

#### **Health Check**
```
GET /health
```

**Response:**
```json
{
  "status": "OK",
  "timestamp": "2025-09-16T10:30:00.000Z",
  "activeTorrents": 2,
  "memory": {
    "rss": 45678592,
    "heapTotal": 20971520,
    "heapUsed": 15728640,
    "external": 1024000,
    "arrayBuffers": 512000
  },
  "uptime": 3600.5
}
```

#### **Debug Information**
```
GET /debug
```

**Response:**
```json
{
  "activeTorrents": [
    {
      "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
      "name": "Coolie (2025) 720p",
      "status": "downloading",
      "progress": 45,
      "filesCount": 1,
      "error": null,
      "serverPort": 8000,
      "downloadSpeed": 1048576,
      "peers": 25,
      "downloaded": "450 MB",
      "uploaded": "120 MB"
    }
  ],
  "totalTorrents": 1,
  "networkSettings": {
    "maxConnections": 200,
    "maxUploads": 20,
    "downloadLimit": "unlimited",
    "uploadLimit": "unlimited"
  }
}
```

#### **Network Statistics**
```
GET /network
```

**Response:**
```json
{
  "torrents": [
    {
      "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
      "name": "Coolie (2025) 720p",
      "status": "downloading",
      "peers": 25,
      "downloadSpeed": 1048576,
      "uploadSpeed": 262144,
      "downloaded": 471859200,
      "uploaded": 125829120,
      "downloadSpeedFormatted": "1.00 MB/s",
      "uploadSpeedFormatted": "256.00 KB/s",
      "downloadedFormatted": "450.00 MB",
      "uploadedFormatted": "120.00 MB"
    }
  ],
  "totals": {
    "downloadSpeed": 1048576,
    "uploadSpeed": 262144,
    "peers": 25,
    "downloadSpeedFormatted": "1.00 MB/s",
    "uploadSpeedFormatted": "256.00 KB/s"
  }
}
```

---

### 2. Torrent Management

#### **Add Torrent**
```
POST /add-torrent
```

**Request Body:**
```json
{
  "torrentHash": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "name": "Coolie (2025) 720p Telugu"  // Optional
}
```

**Response (Success):**
```json
{
  "torrentId": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "torrentHash": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "status": "adding",
  "torrent": {
    "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
    "hash": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
    "magnetURI": "magnet:?xt=urn:btih:179b3b176a2df09e1d1deee9b52e78ad85ec270c",
    "name": "Coolie (2025) 720p Telugu",
    "status": "adding",
    "progress": 0,
    "downloadSpeed": 0,
    "files": [],
    "addedAt": "2025-09-16T10:30:00.000Z",
    "lastAccessedAt": "2025-09-16T10:30:00.000Z",
    "engine": null,
    "serverPort": null
  }
}
```

**Response (Already Active):**
```json
{
  "torrentId": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "torrentHash": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "status": "already_active",
  "torrent": { /* existing torrent data */ }
}
```

**Error Responses:**
```json
{
  "error": "Torrent hash is required"
}

{
  "error": "Invalid torrent hash format"
}
```

#### **Get Torrent Status**
```
GET /torrent/:id
```

**Parameters:**
- `id` - Torrent hash (40 hex characters)

**Response:**
```json
{
  "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "magnetURI": "magnet:?xt=urn:btih:179b3b176a2df09e1d1deee9b52e78ad85ec270c",
  "name": "Coolie (2025) 720p Telugu",
  "status": "downloading",
  "progress": 75,
  "downloadSpeed": 1048576,
  "files": [
    {
      "index": 0,
      "name": "Coolie.2025.720p.Telugu.mkv",
      "path": "Coolie.2025.720p.Telugu.mkv",
      "size": 995932160,
      "offset": 0,
      "isVideo": true,
      "isAudio": false
    }
  ],
  "addedAt": "2025-09-16T10:30:00.000Z",
  "lastAccessedAt": "2025-09-16T11:15:00.000Z",
  "serverPort": 8000,
  "error": null
}
```

#### **List All Torrents**
```
GET /torrents
```

**Response:**
```json
[
  {
    "id": "179b3b176a2df09e1d1deee9b52e78ad85ec270c",
    "magnetURI": "magnet:?xt=urn:btih:179b3b176a2df09e1d1deee9b52e78ad85ec270c",
    "name": "Coolie (2025) 720p Telugu",
    "status": "downloading",
    "progress": 75,
    "downloadSpeed": 1048576,
    "files": [...],
    "addedAt": "2025-09-16T10:30:00.000Z",
    "lastAccessedAt": "2025-09-16T11:15:00.000Z",
    "serverPort": 8000,
    "error": null
  }
]
```

#### **Remove Torrent**
```
DELETE /torrent/:id
```

**Parameters:**
- `id` - Torrent hash

**Response:**
```json
{
  "message": "Torrent removed"
}
```

---

### 3. Video Streaming

#### **Stream Video (Auto-Select)**
```
GET /stream/:torrentId
```

**Parameters:**
- `torrentId` - Torrent hash

**Response:**
- Direct video stream with appropriate headers
- Content-Type: `video/mp4`
- Supports HTTP range requests for seeking
- Automatic remuxing for MKV/AVI files

#### **Stream Specific File**
```
GET /stream/:torrentId/:fileIndex
```

**Parameters:**
- `torrentId` - Torrent hash
- `fileIndex` - File index from the torrent files array

---

## 📋 Status Values

### Torrent Status
- `adding` - Initial state when torrent is being added
- `connecting` - PeerFlix engine created, connecting to peers
- `downloading` - Actively downloading from peers
- `completed` - Download finished
- `error` - Error occurred during processing

---

## 🔧 Frontend Integration Examples

### JavaScript/React Example
```javascript
class TorrentStreamingAPI {
  constructor(baseURL = 'http://localhost:6991') {
    this.baseURL = baseURL;
  }

  async addTorrent(torrentHash, name = '') {
    const response = await fetch(`${this.baseURL}/add-torrent`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        torrentHash,
        name
      })
    });
    return response.json();
  }

  async getTorrentStatus(torrentId) {
    const response = await fetch(`${this.baseURL}/torrent/${torrentId}`);
    return response.json();
  }

  async getAllTorrents() {
    const response = await fetch(`${this.baseURL}/torrents`);
    return response.json();
  }

  async removeTorrent(torrentId) {
    const response = await fetch(`${this.baseURL}/torrent/${torrentId}`, {
      method: 'DELETE'
    });
    return response.json();
  }

  getStreamURL(torrentId, fileIndex = null) {
    if (fileIndex !== null) {
      return `${this.baseURL}/stream/${torrentId}/${fileIndex}`;
    }
    return `${this.baseURL}/stream/${torrentId}`;
  }

  async getNetworkStats() {
    const response = await fetch(`${this.baseURL}/network`);
    return response.json();
  }

  async getDebugInfo() {
    const response = await fetch(`${this.baseURL}/debug`);
    return response.json();
  }
}

// Usage Example
const api = new TorrentStreamingAPI();

// Add a torrent
const result = await api.addTorrent(
  '179b3b176a2df09e1d1deee9b52e78ad85ec270c',
  'Coolie (2025) 720p Telugu'
);

// Get stream URL for video player
const streamURL = api.getStreamURL(result.torrentId);

// Use in HTML5 video player
document.getElementById('video-player').src = streamURL;
```

### React Component Example
```jsx
import React, { useState, useEffect } from 'react';

const TorrentPlayer = () => {
  const [torrents, setTorrents] = useState([]);
  const [selectedTorrent, setSelectedTorrent] = useState(null);
  const [torrentHash, setTorrentHash] = useState('');
  const api = new TorrentStreamingAPI();

  const addTorrent = async () => {
    if (!torrentHash) return;
    
    try {
      const result = await api.addTorrent(torrentHash);
      console.log('Torrent added:', result);
      loadTorrents();
    } catch (error) {
      console.error('Error adding torrent:', error);
    }
  };

  const loadTorrents = async () => {
    try {
      const data = await api.getAllTorrents();
      setTorrents(data);
    } catch (error) {
      console.error('Error loading torrents:', error);
    }
  };

  const startStreaming = (torrent) => {
    const streamURL = api.getStreamURL(torrent.id);
    setSelectedTorrent({ ...torrent, streamURL });
  };

  useEffect(() => {
    loadTorrents();
    const interval = setInterval(loadTorrents, 5000); // Update every 5 seconds
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <div>
        <input
          type="text"
          placeholder="Enter torrent hash"
          value={torrentHash}
          onChange={(e) => setTorrentHash(e.target.value)}
        />
        <button onClick={addTorrent}>Add Torrent</button>
      </div>

      <div>
        <h3>Active Torrents</h3>
        {torrents.map(torrent => (
          <div key={torrent.id}>
            <h4>{torrent.name}</h4>
            <p>Status: {torrent.status}</p>
            <p>Progress: {torrent.progress}%</p>
            {torrent.status === 'downloading' && (
              <button onClick={() => startStreaming(torrent)}>
                Start Streaming
              </button>
            )}
          </div>
        ))}
      </div>

      {selectedTorrent && (
        <div>
          <h3>Now Playing: {selectedTorrent.name}</h3>
          <video
            controls
            width="800"
            height="600"
            src={selectedTorrent.streamURL}
          >
            Your browser does not support the video tag.
          </video>
        </div>
      )}
    </div>
  );
};

export default TorrentPlayer;
```

---

## 🚨 Error Handling

### Common HTTP Status Codes
- `200` - Success
- `400` - Bad Request (invalid hash format, missing parameters)
- `404` - Torrent not found
- `500` - Internal server error

### Error Response Format
```json
{
  "error": "Error message",
  "details": "Additional error details (optional)"
}
```

---

## 🔄 Polling for Updates

Since torrents take time to download and become available for streaming, implement polling:

```javascript
const pollTorrentStatus = async (torrentId) => {
  const poll = async () => {
    try {
      const status = await api.getTorrentStatus(torrentId);
      
      if (status.status === 'downloading' && status.files.length > 0) {
        // Ready to stream
        console.log('Torrent ready for streaming!');
        return status;
      } else if (status.status === 'error') {
        console.error('Torrent error:', status.error);
        return status;
      } else {
        // Still connecting, continue polling
        setTimeout(poll, 2000); // Poll every 2 seconds
      }
    } catch (error) {
      console.error('Polling error:', error);
      setTimeout(poll, 5000); // Retry after 5 seconds
    }
  };
  
  return poll();
};
```

---

## 🎯 Best Practices

1. **Hash Validation**: Always validate torrent hashes on frontend before sending
2. **Polling**: Use reasonable intervals (2-5 seconds) for status updates
3. **Error Handling**: Implement proper error handling for network issues
4. **Cleanup**: Remove torrents when done to free resources
5. **Progress Tracking**: Show download progress to users
6. **Auto-retry**: Implement retry logic for failed requests

---

## 🐳 Docker Deployment

If deploying with Docker, make sure to:
- Map port 6991
- Use network optimization settings
- Set proper resource limits

```yaml
services:
  torrent-streamer:
    image: your-image
    ports:
      - "6991:6991"
    environment:
      - NODE_ENV=production
    sysctls:
      - net.core.rmem_max=134217728
      - net.core.wmem_max=134217728
```

---

## 🔍 Monitoring & Analytics

Use the `/network` endpoint to build dashboards showing:
- Total download/upload speeds
- Active peer connections
- Per-torrent statistics
- Server resource usage (from `/health`)

This API provides everything needed to build a modern torrent streaming frontend application with real-time monitoring and automatic video optimization!
