import express from 'express';
import cors from 'cors';
import morgan from 'morgan';
import ffmpeg from 'fluent-ffmpeg';
import peerflix from 'peerflix';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

// Get __dirname equivalent for ES modules
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 6991;

// Utility function to format bytes
function formatBytes(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Middleware
app.use(cors());
app.use(morgan('combined'));
app.use(express.json());

// Serve static files (for test interface)
app.use('/public', express.static(path.join(__dirname, 'public')));

// PeerFlix engines storage
const activeTorrents = new Map();

// Global error handlers
process.on('uncaughtException', (err) => {
  console.error('Uncaught Exception:', err);
  console.error('Stack:', err.stack);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

// Configure FFmpeg path (should be available in container)
ffmpeg.setFfmpegPath('/usr/bin/ffmpeg');
ffmpeg.setFfprobePath('/usr/bin/ffprobe');

// Test FFmpeg availability
ffmpeg.getAvailableFormats((err, formats) => {
  if (err) {
    console.error('FFmpeg not available:', err);
  } else {
    console.log('FFmpeg is available with', Object.keys(formats).length, 'formats');
  }
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ 
    status: 'OK', 
    timestamp: new Date().toISOString(),
    activeTorrents: activeTorrents.size,
    memory: process.memoryUsage(),
    uptime: process.uptime()
  });
});

// Debug endpoint to see server logs
app.get('/debug', (req, res) => {
  const torrents = Array.from(activeTorrents.values()).map(t => ({
    id: t.id,
    name: t.name,
    status: t.status,
    progress: t.progress,
    filesCount: t.files.length,
    error: t.error || null,
    serverPort: t.serverPort || null
  }));
  
  res.json({
    activeTorrents: torrents,
    totalTorrents: activeTorrents.size
  });
});

// Add torrent endpoint
app.post('/add-torrent', async (req, res) => {
  try {
    const { magnetURI, name } = req.body;
    
    if (!magnetURI) {
      return res.status(400).json({ error: 'Magnet URI is required' });
    }

    console.log('Adding torrent with PeerFlix:', magnetURI.slice(0, 100) + '...');

    const torrentId = Buffer.from(magnetURI).toString('base64').slice(0, 16);
    
    // Check if torrent is already active
    if (activeTorrents.has(torrentId)) {
      return res.json({ 
        torrentId, 
        status: 'already_active',
        torrent: activeTorrents.get(torrentId)
      });
    }

    const torrentInfo = {
      id: torrentId,
      magnetURI,
      name: name || `torrent_${torrentId}`,
      status: 'adding',
      progress: 0,
      downloadSpeed: 0,
      files: [],
      addedAt: new Date().toISOString(),
      engine: null,
      serverPort: null
    };

    activeTorrents.set(torrentId, torrentInfo);

    // Send immediate response
    res.json({ torrentId, status: 'adding', torrent: torrentInfo });

    // Add torrent with PeerFlix
    try {
      console.log('Creating PeerFlix engine...');
      
      const engine = peerflix(magnetURI, {
        connections: 50,
        uploads: 5,
        path: path.join(__dirname, 'downloads'),
        buffer: (1.5 * 1000 * 1000).toString(), // 1.5 MB buffer
        port: 0 // Let PeerFlix choose port
      });

      torrentInfo.engine = engine;
      torrentInfo.status = 'connecting';

      // Handle engine events
      engine.on('ready', () => {
        try {
          console.log('PeerFlix engine ready');
          torrentInfo.status = 'downloading';
          torrentInfo.name = name || engine.torrent.name || torrentInfo.name;
          
          // Map files
          torrentInfo.files = engine.files.map((file, index) => ({
            index,
            name: file.name,
            path: file.path,
            size: file.length,
            offset: file.offset,
            isVideo: /\.(mp4|avi|mkv|mov|wmv|flv|webm)$/i.test(file.name),
            isAudio: /\.(mp3|flac|wav|aac|ogg|wma)$/i.test(file.name)
          }));

          console.log(`Found ${torrentInfo.files.length} files in torrent`);
        } catch (err) {
          console.error('Error in ready event:', err);
          torrentInfo.status = 'error';
          torrentInfo.error = err.message;
        }
      });

      engine.on('download', () => {
        try {
          const swarm = engine.swarm;
          torrentInfo.progress = Math.round((swarm.downloaded / swarm.size) * 100);
          torrentInfo.downloadSpeed = swarm.downloadSpeed();
        } catch (err) {
          // Ignore progress update errors
        }
      });

      engine.on('idle', () => {
        torrentInfo.status = 'completed';
        console.log(`Torrent ${torrentId} completed`);
      });

      engine.on('error', (err) => {
        console.error(`PeerFlix engine error for ${torrentId}:`, err);
        torrentInfo.status = 'error';
        torrentInfo.error = err.message;
      });

      // Start PeerFlix server
      engine.server.on('listening', () => {
        torrentInfo.serverPort = engine.server.address().port;
        console.log(`PeerFlix server listening on port ${torrentInfo.serverPort}`);
      });

    } catch (err) {
      console.error('Error creating PeerFlix engine:', err);
      torrentInfo.status = 'error';
      torrentInfo.error = err.message;
    }

  } catch (error) {
    console.error('Error in add-torrent endpoint:', error);
    res.status(500).json({ error: 'Failed to add torrent', details: error.message });
  }
});

// Get torrent status
app.get('/torrent/:id', (req, res) => {
  const torrentId = req.params.id;
  const torrent = activeTorrents.get(torrentId);
  
  if (!torrent) {
    return res.status(404).json({ error: 'Torrent not found' });
  }
  
  // Clean torrent data to avoid circular references
  const cleanTorrent = {
    id: torrent.id,
    magnetURI: torrent.magnetURI,
    name: torrent.name,
    status: torrent.status,
    progress: torrent.progress,
    downloadSpeed: torrent.downloadSpeed,
    files: torrent.files,
    addedAt: torrent.addedAt,
    serverPort: torrent.serverPort,
    error: torrent.error || null
  };
  
  res.json(cleanTorrent);
});

// List all torrents
app.get('/torrents', (req, res) => {
  const torrents = Array.from(activeTorrents.values()).map(torrent => ({
    id: torrent.id,
    magnetURI: torrent.magnetURI,
    name: torrent.name,
    status: torrent.status,
    progress: torrent.progress,
    downloadSpeed: torrent.downloadSpeed,
    files: torrent.files,
    addedAt: torrent.addedAt,
    serverPort: torrent.serverPort,
    error: torrent.error || null
  }));
  
  res.json(torrents);
});

// Stream file with FFmpeg remuxing
app.get('/stream/:torrentId/:fileIndex?', async (req, res) => {
  try {
    const { torrentId, fileIndex } = req.params;
    const torrentInfo = activeTorrents.get(torrentId);
    
    if (!torrentInfo) {
      return res.status(404).json({ error: 'Torrent not found' });
    }

    if (!torrentInfo.engine || torrentInfo.status === 'error') {
      return res.status(404).json({ error: 'Torrent engine not ready' });
    }

    let file;
    
    if (fileIndex !== undefined) {
      // Specific file requested
      file = torrentInfo.files[parseInt(fileIndex)];
      if (!file) {
        return res.status(404).json({ error: 'File not found' });
      }
    } else {
      // Auto-select best video file
      const videoFiles = torrentInfo.files.filter(f => f.isVideo);
      
      if (videoFiles.length === 0) {
        return res.status(404).json({ error: 'No video files found in torrent' });
      }
      
      // Priority order: MP4 > MKV > AVI > others
      const priorityOrder = ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm'];
      
      file = videoFiles.sort((a, b) => {
        const aExt = path.extname(a.name).toLowerCase();
        const bExt = path.extname(b.name).toLowerCase();
        const aPriority = priorityOrder.indexOf(aExt);
        const bPriority = priorityOrder.indexOf(bExt);
        
        // If both have priority, sort by priority (lower index = higher priority)
        if (aPriority !== -1 && bPriority !== -1) {
          return aPriority - bPriority;
        }
        
        // If only one has priority, prefer it
        if (aPriority !== -1) return -1;
        if (bPriority !== -1) return 1;
        
        // If neither has priority, sort by file size (largest first)
        return b.size - a.size;
      })[0];
      
      console.log(`Auto-selected video file: ${file.name} (${formatBytes(file.size)})`);
    }

    // Get the file stream URL from PeerFlix server
    const streamUrl = `http://localhost:${torrentInfo.serverPort}/${file.index}`;
    
    console.log(`Streaming from PeerFlix: ${streamUrl}`);

    // Set appropriate headers
    res.setHeader('Content-Type', 'video/mp4');
    res.setHeader('Accept-Ranges', 'bytes');
    res.setHeader('Cache-Control', 'no-cache');

    // Handle range requests for video seeking
    const range = req.headers.range;
    let requestOptions = {};

    if (range) {
      requestOptions.headers = { Range: range };
    }

    // Determine if remuxing is needed
    const needsRemux = /\.(mkv|avi|wmv|flv)$/i.test(file.name);
    
    if (needsRemux) {
      console.log(`Remuxing ${file.name} to MP4`);
      
      // Create FFmpeg stream from PeerFlix URL
      const ffmpegStream = ffmpeg(streamUrl)
        .format('mp4')
        .videoCodec('copy')  // Copy video stream (no re-encoding)
        .audioCodec('aac')   // Convert audio to AAC if needed
        .outputOptions([
          '-movflags', 'frag_keyframe+empty_moov',  // Enable streaming
          '-f', 'mp4',
          '-avoid_negative_ts', 'make_zero'
        ])
        .on('start', (cmdLine) => {
          console.log('FFmpeg started:', cmdLine);
        })
        .on('error', (err) => {
          console.error('FFmpeg error:', err);
          if (!res.headersSent) {
            res.status(500).json({ error: 'Streaming failed' });
          }
        })
        .on('end', () => {
          console.log('FFmpeg finished');
        });

      // Pipe to response
      ffmpegStream.pipe(res, { end: true });

    } else {
      // Direct streaming for compatible formats - proxy from PeerFlix
      console.log(`Direct streaming ${file.name}`);
      
      const fetch = (await import('node-fetch')).default;
      const response = await fetch(streamUrl, requestOptions);
      
      // Copy headers from PeerFlix response
      response.headers.forEach((value, key) => {
        if (key.toLowerCase() !== 'transfer-encoding') {
          res.setHeader(key, value);
        }
      });
      
      // Set status code
      res.status(response.status);
      
      // Pipe the response
      response.body.pipe(res);
    }

  } catch (error) {
    console.error('Streaming error:', error);
    if (!res.headersSent) {
      res.status(500).json({ error: 'Streaming failed', details: error.message });
    }
  }
});

// Remove torrent
app.delete('/torrent/:id', (req, res) => {
  const torrentId = req.params.id;
  
  if (activeTorrents.has(torrentId)) {
    const torrentInfo = activeTorrents.get(torrentId);
    
    // Destroy PeerFlix engine
    if (torrentInfo.engine) {
      try {
        torrentInfo.engine.destroy();
        console.log(`PeerFlix engine destroyed for torrent ${torrentId}`);
      } catch (err) {
        console.error('Error destroying PeerFlix engine:', err);
      }
    }
    
    activeTorrents.delete(torrentId);
    res.json({ message: 'Torrent removed' });
  } else {
    res.status(404).json({ error: 'Torrent not found' });
  }
});

// Error handling middleware
app.use((error, req, res, next) => {
  console.error('Unhandled error:', error);
  res.status(500).json({ error: 'Internal server error' });
});

// Start server
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Torrent streaming server running on port ${PORT} with PeerFlix`);
  console.log(`Health check: http://localhost:${PORT}/health`);
  console.log(`Debug info: http://localhost:${PORT}/debug`);
  
  // Heartbeat to keep process alive and log status
  setInterval(() => {
    console.log(`[${new Date().toISOString()}] Server alive - Active torrents: ${activeTorrents.size}`);
  }, 30000); // Every 30 seconds
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('Shutting down gracefully...');
  // Destroy all PeerFlix engines
  activeTorrents.forEach((torrent) => {
    if (torrent.engine) {
      torrent.engine.destroy();
    }
  });
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('Shutting down gracefully...');
  // Destroy all PeerFlix engines
  activeTorrents.forEach((torrent) => {
    if (torrent.engine) {
      torrent.engine.destroy();
    }
  });
  process.exit(0);
});
