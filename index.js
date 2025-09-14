import express from 'express';
import cors from 'cors';
import morgan from 'morgan';
import ffmpeg from 'fluent-ffmpeg';
import WebTorrent from 'webtorrent';
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

// WebTorrent client with error handling
const client = new WebTorrent({
  maxConns: 50,  // Reduced connections
  nodeId: Buffer.alloc(20).fill(1),
  tracker: {
    announce: [],
    getAnnounceOpts() {
      return { numwant: 25 }
    }
  },
  dht: false,    // Disable DHT in container
  lsd: false,    // Disable local service discovery
  natUpnp: false, // Disable UPnP
  webSeeds: false // Disable web seeds
});

// Add global error handlers for WebTorrent
client.on('error', (err) => {
  console.error('WebTorrent client error:', err);
});

process.on('uncaughtException', (err) => {
  console.error('Uncaught Exception:', err);
  console.error('Stack:', err.stack);
  // Don't exit, just log
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
  // Don't exit, just log
});

const activeTorrents = new Map();

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
    clientTorrents: client.torrents.length,
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
    error: t.error || null
  }));
  
  res.json({
    activeTorrents: torrents,
    clientTorrents: client.torrents.length,
    clientRatio: client.ratio,
    clientDownloadSpeed: client.downloadSpeed,
    clientUploadSpeed: client.uploadSpeed
  });
});

// Add torrent endpoint
app.post('/add-torrent', async (req, res) => {
  try {
    const { magnetURI, name } = req.body;
    
    if (!magnetURI) {
      return res.status(400).json({ error: 'Magnet URI is required' });
    }

    console.log('Adding torrent:', magnetURI.slice(0, 100) + '...');

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
      webTorrentId: null
    };

    activeTorrents.set(torrentId, torrentInfo);

    // Send immediate response
    res.json({ torrentId, status: 'adding', torrent: torrentInfo });

    // Add torrent to WebTorrent client asynchronously
    setTimeout(() => {
      try {
        console.log('Creating torrent with WebTorrent...');
        
        const torrent = client.add(magnetURI, {
          path: path.join(__dirname, 'downloads')
        }, (torrent) => {
          console.log('Torrent added successfully:', torrent.name);
          torrentInfo.webTorrentId = torrent.infoHash;
          torrentInfo.status = 'downloading';
          torrentInfo.name = name || torrent.name;
        });

        // Handle torrent events with error catching
        torrent.on('metadata', () => {
          try {
            console.log(`Torrent metadata received: ${torrent.name}`);
            torrentInfo.name = name || torrent.name;
            torrentInfo.status = 'downloading';
            
            // Map files from WebTorrent format
            torrentInfo.files = torrent.files.map((file, index) => ({
              index,
              name: file.name,
              path: file.path,
              size: file.length,
              offset: 0,
              isVideo: /\.(mp4|avi|mkv|mov|wmv|flv|webm)$/i.test(file.name),
              isAudio: /\.(mp3|flac|wav|aac|ogg|wma)$/i.test(file.name)
            }));

            console.log(`Found ${torrentInfo.files.length} files in torrent`);
          } catch (err) {
            console.error('Error processing metadata:', err);
            torrentInfo.status = 'error';
          }
        });

        torrent.on('download', () => {
          try {
            torrentInfo.progress = Math.round(torrent.progress * 100);
            torrentInfo.downloadSpeed = torrent.downloadSpeed;
          } catch (err) {
            console.error('Error updating progress:', err);
          }
        });

        torrent.on('done', () => {
          try {
            torrentInfo.status = 'completed';
            console.log(`Torrent ${torrentId} completed`);
          } catch (err) {
            console.error('Error marking torrent as done:', err);
          }
        });

        torrent.on('error', (err) => {
          console.error(`Torrent ${torrentId} error:`, err);
          torrentInfo.status = 'error';
          torrentInfo.error = err.message;
        });

      } catch (err) {
        console.error('Error adding torrent to WebTorrent:', err);
        torrentInfo.status = 'error';
        torrentInfo.error = err.message;
      }
    }, 100); // Small delay to ensure response is sent first

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
  
  res.json(torrent);
});

// List all torrents
app.get('/torrents', (req, res) => {
  const torrents = Array.from(activeTorrents.values());
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

    // Find the WebTorrent instance
    const torrent = client.torrents.find(t => t.infoHash === torrentInfo.webTorrentId);
    if (!torrent) {
      return res.status(404).json({ error: 'Torrent not active in client' });
    }

    let file;
    
    if (fileIndex !== undefined) {
      // Specific file requested
      file = torrent.files[parseInt(fileIndex)];
      if (!file) {
        return res.status(404).json({ error: 'File not found' });
      }
    } else {
      // Auto-select best video file
      const videoFiles = torrent.files.filter(f => 
        /\.(mp4|avi|mkv|mov|wmv|flv|webm)$/i.test(f.name)
      );
      
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
        return b.length - a.length;
      })[0];
      
      console.log(`Auto-selected video file: ${file.name} (${formatBytes(file.length)})`);
    }

    // Set appropriate headers
    res.setHeader('Content-Type', 'video/mp4');
    res.setHeader('Accept-Ranges', 'bytes');
    res.setHeader('Cache-Control', 'no-cache');

    // Handle range requests for video seeking
    const range = req.headers.range;
    let start = 0;
    let end = file.length - 1;

    if (range) {
      const parts = range.replace(/bytes=/, "").split("-");
      start = parseInt(parts[0], 10);
      end = parts[1] ? parseInt(parts[1], 10) : file.length - 1;
      const chunksize = (end - start) + 1;

      res.status(206);
      res.setHeader('Content-Range', `bytes ${start}-${end}/${file.length}`);
      res.setHeader('Content-Length', chunksize);
    } else {
      res.setHeader('Content-Length', file.length);
    }

    // Determine if remuxing is needed
    const needsRemux = /\.(mkv|avi|wmv|flv)$/i.test(file.name);
    
    if (needsRemux) {
      console.log(`Remuxing ${file.name} to MP4`);
      
      // Create read stream from WebTorrent file
      const fileStream = file.createReadStream({ start, end });
      
      // Create FFmpeg stream
      const ffmpegStream = ffmpeg(fileStream)
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
      // Direct streaming for compatible formats
      console.log(`Direct streaming ${file.name}`);
      
      const fileStream = file.createReadStream({ start, end });
      fileStream.pipe(res);
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
    
    // Remove from WebTorrent client
    const torrent = client.torrents.find(t => t.infoHash === torrentInfo.webTorrentId);
    if (torrent) {
      torrent.destroy();
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
  console.log(`Torrent streaming server running on port ${PORT}`);
  console.log(`Health check: http://localhost:${PORT}/health`);
  console.log(`Debug info: http://localhost:${PORT}/debug`);
  
  // Heartbeat to keep process alive and log status
  setInterval(() => {
    console.log(`[${new Date().toISOString()}] Server alive - Active torrents: ${activeTorrents.size}, Client torrents: ${client.torrents.length}`);
  }, 30000); // Every 30 seconds
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('Shutting down gracefully...');
  client.destroy();
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('Shutting down gracefully...');
  client.destroy();
  process.exit(0);
});
