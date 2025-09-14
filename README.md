# Torrent Streaming Server

A Node.js server for streaming torrent content with automatic video file selection, FFmpeg remuxing, and container deployment support.

## Features

- **Auto Video Selection**: Automatically selects the best video file (MP4 > MKV > AVI priority)
- **FFmpeg Remuxing**: Converts MKV/AVI files to MP4 for browser compatibility
- **Auto Cleanup**: Removes torrents inactive for 12+ hours
- **Docker Support**: Ready for container deployment
- **Web Interface**: Built-in HTML test interface
- **RESTful API**: Complete API for torrent management and streaming

## Project Structure

```
├── server.js                 # Main entry point
├── src/
│   ├── app.js                # Application setup and configuration
│   ├── services/
│   │   ├── TorrentService.js # Torrent management with PeerFlix
│   │   └── StreamingService.js # Video streaming and FFmpeg handling
│   ├── routes/
│   │   ├── system.js         # Health and debug endpoints
│   │   ├── torrents.js       # Torrent CRUD operations
│   │   └── streaming.js      # Video streaming endpoints
│   └── utils/
│       └── helpers.js        # Utility functions
├── public/
│   └── test.html            # Web interface for testing
├── downloads/               # Torrent download directory
├── Dockerfile              # Container build configuration
├── docker-compose.yml      # Docker Compose setup
└── index.js.backup         # Original monolithic version (backup)
```

## Quick Start

### Local Development
```bash
npm install
npm run dev
```

### Production
```bash
npm start
```

### Docker
```bash
docker build -t torrent-stream .
docker run -p 6991:6991 torrent-stream
```

### Docker Compose (Portainer)
```bash
docker-compose up -d
```

## API Endpoints

### System
- `GET /health` - Health check
- `GET /debug` - Debug information

### Torrent Management
- `POST /add-torrent` - Add new torrent
- `GET /torrents` - List all torrents
- `GET /torrent/:id` - Get torrent details
- `DELETE /torrent/:id` - Remove torrent

### Streaming
- `GET /stream/:torrentId` - Stream auto-selected video file
- `GET /stream/:torrentId/:fileIndex` - Stream specific file

## Auto Cleanup Feature

The server automatically removes torrents that haven't been streamed for 12+ hours:

- **Check Interval**: Every hour
- **Cleanup Threshold**: 12 hours of inactivity
- **Activity Tracking**: Updates `lastStreamedAt` timestamp on each stream request
- **Graceful Removal**: Properly destroys PeerFlix engines before removal

## Services Overview

### TorrentService
- Manages active torrents using PeerFlix
- Handles torrent lifecycle (add, remove, cleanup)
- Provides clean data serialization (no circular references)
- Auto-cleanup scheduler for inactive torrents

### StreamingService
- Auto-selects best video files
- Handles FFmpeg remuxing for incompatible formats
- Manages direct streaming for compatible formats
- Sets appropriate streaming headers

## Configuration

### Environment Variables
- `PORT` - Server port (default: 6991)

### FFmpeg Paths
- FFmpeg: `/usr/bin/ffmpeg`
- FFprobe: `/usr/bin/ffprobe`

## Web Interface

Access the test interface at: `http://localhost:6991/public/test.html`

Features:
- Add torrents by magnet URI
- View torrent status and progress
- Auto-stream best video files
- Manual file selection

## Migration from Monolithic Version

The original `index.js` has been refactored into a modular structure:

1. **Services**: Core business logic separated into dedicated services
2. **Routes**: API endpoints organized by functionality
3. **Utils**: Common utilities and helpers
4. **Auto Cleanup**: New 12-hour inactive torrent cleanup
5. **Better Error Handling**: Improved error management across services

The original file is preserved as `index.js.backup` for reference.
## Docker Deployment

The application is containerized and ready for deployment in Portainer:

1. Port 6991 exposed
2. FFmpeg included in container
3. Automatic dependency installation
4. Graceful shutdown handling

## Troubleshooting

### Common Issues
1. **FFmpeg not found**: Ensure FFmpeg is installed in container
2. **Port conflicts**: Change PORT environment variable
3. **Memory issues**: Monitor torrent cleanup and memory usage
4. **Streaming issues**: Check PeerFlix server ports and connectivity

### Logs
- Server heartbeat every 30 seconds
- Detailed torrent lifecycle logging
- FFmpeg operation logging
- Auto-cleanup operation logging

## Legal Notice

This software is for educational purposes. Only use with content you have legal rights to download and distribute.
