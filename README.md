# Torrent Streaming Server

A Python-based server for streaming torrent content with automatic video file selection, FFmpeg remuxing, and container deployment support.

## Features

- **Auto Video Selection**: Automatically selects the best video file (MP4 > MKV > AVI priority)
- **FFmpeg Remuxing**: Converts video files to HLS format for browser compatibility
- **Auto Cleanup**: Removes inactive streams
- **Docker Support**: Ready for container deployment
- **Web Interface**: Built-in HTML test interface
- **RESTful API**: Complete FastAPI-based API for torrent management and streaming

## Project Structure

```
├── app.py                   # Main FastAPI application entry point
├── src/
│   ├── __init__.py         # Package initialization
│   ├── background.py       # Background tasks (cleanup, alerts)
│   ├── config.py           # Configuration settings
│   ├── state.py           # Global state management
│   ├── utils.py           # Utility functions
│   └── api/
│       ├── __init__.py    # API package initialization
│       ├── streaming.py   # Video streaming endpoints
│       └── torrents.py    # Torrent management endpoints
├── public/
│   └── test.html         # Web interface for testing
├── downloads/            # Torrent download directory
├── hls/                 # HLS streaming directory
├── Dockerfile           # Container build configuration
└── docker-compose.yml   # Docker Compose setup
```

## Quick Start

### Local Development
```bash
# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the server
python app.py
```

### Production
```bash
uvicorn app:app --host 0.0.0.0 --port 6991
```

### Docker
```bash
docker build -t torrent-stream .
docker run -p 6991:6991 torrent-stream
```

### Docker Compose
```bash
docker-compose up -d
```

## API Endpoints

### Torrent Management
- `POST /api/torrents/` - Add new torrent
- `GET /api/torrents/` - List all torrents
- `GET /api/torrents/{torrent_id}` - Get torrent details
- `DELETE /api/torrents/{torrent_id}` - Remove torrent

### Streaming
- `GET /api/stream/{torrent_id}` - Get HLS playlist
- `GET /api/stream/{torrent_id}/{segment}` - Get HLS segment

## Auto Cleanup Feature

The server automatically removes inactive streams:

- **Background Task**: Runs cleanup periodically
- **Cleanup Threshold**: Based on inactivity period
- **Activity Tracking**: Updates timestamps on stream access
- **Graceful Removal**: Properly cleans up resources

## Features Overview

### Torrent Management
- Manages active torrents using libtorrent
- Handles torrent lifecycle (add, remove, cleanup)
- Auto-cleanup scheduler for inactive streams
- Background alert listener for torrent events

### Streaming Service
- Auto-selects best video files
- Handles FFmpeg conversion to HLS
- Manages HLS playlist and segments
- Sets appropriate streaming headers

## Configuration

### Environment Variables
- `PORT` - Server port (default: 6991)
- `DOWNLOAD_PATH` - Path for torrent downloads
- `HLS_PATH` - Path for HLS segments
- `WARM_CACHE_TIMEOUT_MINUTES` - Cleanup timeout

### Dependencies
- FFmpeg for video conversion
- libtorrent for torrent handling
- FastAPI for REST API
- uvicorn for ASGI server

## Web Interface

Access the test interface at: `http://localhost:6991/`

Features:
- Add torrents by magnet URI
- View torrent status and progress
- Auto-stream best video files
- Real-time status updates

## Docker Deployment

The application is containerized with all dependencies:

1. Port 6991 exposed
2. FFmpeg included in container
3. Automatic dependency installation
4. Graceful shutdown handling

## Troubleshooting

### Common Issues
1. **FFmpeg not found**: Ensure FFmpeg is installed
2. **Port conflicts**: Change PORT environment variable
3. **Memory issues**: Monitor stream cleanup
4. **Streaming issues**: Check libtorrent configuration

### Logs
- Background task execution logs
- Torrent activity monitoring
- FFmpeg conversion status
- Cleanup operation logs

## Legal Notice

This software is for educational purposes. Only use with content you have legal rights to download and distribute.