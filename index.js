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

// Torrent cleanup configuration
const CLEANUP_INTERVAL = 12 * 60 * 60 * 1000; // 12 hours in milliseconds

// Start automatic cleanup of inactive torrents
function startTorrentCleanup() {
  setInterval(() => {
    cleanupInactiveTorrents();
  }, 60 * 60 * 1000); // Check every hour
  
  console.log('Torrent cleanup scheduler started (12-hour inactive cleanup)');
}

// Clean up torrents that haven't been streamed for 12+ hours
function cleanupInactiveTorrents() {
  const now = Date.now();
  const toRemove = [];

  activeTorrents.forEach((torrent, id) => {
    const lastAccessed = torrent.lastAccessedAt || torrent.addedAt;
    const timeSinceAccess = now - new Date(lastAccessed).getTime();
    
    if (timeSinceAccess > CLEANUP_INTERVAL) {
      console.log(`Cleaning up inactive torrent: ${torrent.name} (${id}) - inactive for ${Math.round(timeSinceAccess / 1000 / 60 / 60)} hours`);
      toRemove.push(id);
    }
  });

  // Remove inactive torrents
  toRemove.forEach(id => {
    removeTorrentById(id);
  });

  if (toRemove.length > 0) {
    console.log(`Cleaned up ${toRemove.length} inactive torrents`);
  }
}

// Helper function to remove torrent by ID
function removeTorrentById(torrentId) {
  if (activeTorrents.has(torrentId)) {
    const torrentInfo = activeTorrents.get(torrentId);
    
    // Clear network logger if it exists
    if (torrentInfo.networkLogger) {
      clearInterval(torrentInfo.networkLogger);
    }
    
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
    return true;
  }
  return false;
}

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
    serverPort: t.serverPort || null,
    // Network stats
    downloadSpeed: t.downloadSpeed || 0,
    peers: t.engine && t.engine.swarm ? t.engine.swarm.wires.length : 0,
    downloaded: t.engine && t.engine.swarm ? formatBytes(t.engine.swarm.downloaded) : '0 Bytes',
    uploaded: t.engine && t.engine.swarm ? formatBytes(t.engine.swarm.uploaded) : '0 Bytes'
  }));
  
  res.json({
    activeTorrents: torrents,
    totalTorrents: activeTorrents.size,
    networkSettings: {
      maxConnections: 200,
      maxUploads: 20,
      downloadLimit: 'unlimited',
      uploadLimit: 'unlimited'
    }
  });
});

// Network status endpoint for monitoring bandwidth usage
app.get('/network', (req, res) => {
  const networkStats = Array.from(activeTorrents.values()).map(t => {
    if (!t.engine || !t.engine.swarm) {
      return {
        id: t.id,
        name: t.name,
        status: t.status,
        peers: 0,
        downloadSpeed: 0,
        uploadSpeed: 0,
        downloaded: 0,
        uploaded: 0
      };
    }
    
    const swarm = t.engine.swarm;
    return {
      id: t.id,
      name: t.name,
      status: t.status,
      peers: swarm.wires ? swarm.wires.length : 0,
      downloadSpeed: swarm.downloadSpeed ? swarm.downloadSpeed() : 0,
      uploadSpeed: swarm.uploadSpeed ? swarm.uploadSpeed() : 0,
      downloaded: swarm.downloaded || 0,
      uploaded: swarm.uploaded || 0,
      // Formatted versions
      downloadSpeedFormatted: formatBytes(swarm.downloadSpeed ? swarm.downloadSpeed() : 0) + '/s',
      uploadSpeedFormatted: formatBytes(swarm.uploadSpeed ? swarm.uploadSpeed() : 0) + '/s',
      downloadedFormatted: formatBytes(swarm.downloaded || 0),
      uploadedFormatted: formatBytes(swarm.uploaded || 0)
    };
  });

  const totalDownloadSpeed = networkStats.reduce((sum, t) => sum + t.downloadSpeed, 0);
  const totalUploadSpeed = networkStats.reduce((sum, t) => sum + t.uploadSpeed, 0);
  const totalPeers = networkStats.reduce((sum, t) => sum + t.peers, 0);
  
  res.json({
    torrents: networkStats,
    totals: {
      downloadSpeed: totalDownloadSpeed,
      uploadSpeed: totalUploadSpeed,
      peers: totalPeers,
      downloadSpeedFormatted: formatBytes(totalDownloadSpeed) + '/s',
      uploadSpeedFormatted: formatBytes(totalUploadSpeed) + '/s'
    }
  });
});

// Helper function to construct magnet URI from hash
function constructMagnetURI(torrentHash) {
  // Basic magnet URI with just the hash - PeerFlix will use DHT to find peers
  return `magnet:?xt=urn:btih:${torrentHash}`;
}

// Add torrent endpoint
app.post('/add-torrent', async (req, res) => {
  try {
    const { torrentHash, name } = req.body;
    
    if (!torrentHash) {
      return res.status(400).json({ error: 'Torrent hash is required' });
    }

    // Validate hash format (40 hex chars or 32 base32 chars)
    if (!/^[a-fA-F0-9]{40}$/.test(torrentHash) && !/^[a-zA-Z0-9]{32}$/.test(torrentHash)) {
      return res.status(400).json({ error: 'Invalid torrent hash format' });
    }

    const normalizedHash = torrentHash.toLowerCase();
    console.log('Adding torrent with hash:', normalizedHash);

    // Construct magnet URI from hash
    const magnetURI = constructMagnetURI(normalizedHash);
    console.log('Constructed magnet URI:', magnetURI);

    const torrentId = normalizedHash;
    
    // Check if torrent is already active
    if (activeTorrents.has(torrentId)) {
      const existingTorrent = activeTorrents.get(torrentId);
      existingTorrent.lastAccessedAt = new Date().toISOString();
      return res.json({ 
        torrentId, 
        torrentHash: normalizedHash,
        status: 'already_active',
        torrent: existingTorrent
      });
    }

    const torrentInfo = {
      id: torrentId,
      hash: normalizedHash,
      magnetURI,
      name: name || `torrent_${normalizedHash.slice(0, 8)}`,
      status: 'adding',
      progress: 0,
      downloadSpeed: 0,
      files: [],
      addedAt: new Date().toISOString(),
      lastAccessedAt: new Date().toISOString(),
      engine: null,
      serverPort: null
    };

    activeTorrents.set(torrentId, torrentInfo);

    // Send immediate response
    res.json({ torrentId, torrentHash: normalizedHash, status: 'adding', torrent: torrentInfo });

    // Add torrent with PeerFlix
    try {
      console.log('Creating PeerFlix engine...');
      
      const engine = peerflix(magnetURI, {
        connections: 200,        // Maximum connections for aggressive networking
        uploads: 20,             // High upload slots for better ratio
        path: path.join(__dirname, 'downloads'),
        buffer: (5 * 1000 * 1000).toString(), // 5 MB buffer for high-speed streaming
        port: 0,                 // Let PeerFlix choose port
        // Aggressive networking options
        verify: true,            // Verify pieces
        dht: true,               // Enable DHT for peer discovery
        tracker: true,           // Enable all tracker support
        pex: true,               // Enable peer exchange
        // Network performance settings
        maxConnsPerTorrent: 200, // Max connections per torrent
        maxUploadsPerTorrent: 20, // Max uploads per torrent
        // Force sequential downloading for streaming
        strategy: 'sequential',  // Sequential download for streaming
        // Additional network optimization
        pieceLength: 16384,      // Smaller piece length for faster initial buffering
        maxConnections: 200,     // Global max connections
        downloadLimit: -1,       // No download limit (use all available bandwidth)
        uploadLimit: -1          // No upload limit (better swarm participation)
      });

      torrentInfo.engine = engine;
      torrentInfo.status = 'connecting';

      // Handle engine events
      engine.on('ready', () => {
        try {
          console.log('PeerFlix engine ready - Starting aggressive peer discovery');
          torrentInfo.status = 'downloading';
          torrentInfo.name = name || engine.torrent.name || torrentInfo.name;
          
          // Enable more aggressive peer discovery
          if (engine.swarm) {
            engine.swarm.maxConnections = 200;
            console.log(`Set max connections to ${engine.swarm.maxConnections} for torrent: ${torrentInfo.name}`);
          }
          
          // Map files
          torrentInfo.files = engine.files.map((file, index) => ({
            index,
            name: file.name,
            path: file.path,
            size: file.length,
            offset: file.offset,
            isVideo: /\.(mp4|avi|mkv|mov|wmv|flv|webm|m4v)$/i.test(file.name),
            isAudio: /\.(mp3|flac|wav|aac|ogg|wma|m4a)$/i.test(file.name)
          }));

          console.log(`Found ${torrentInfo.files.length} files in torrent`);
          
          // Log video files for debugging
          const videoFiles = torrentInfo.files.filter(f => f.isVideo);
          if (videoFiles.length > 0) {
            console.log('Video files found:', videoFiles.map(f => `${f.name} (${formatBytes(f.size)})`).join(', '));
          } else {
            console.log('No video files detected in torrent');
          }

          // Start periodic network status logging
          const networkLogger = setInterval(() => {
            if (engine.swarm) {
              const peers = engine.swarm.wires ? engine.swarm.wires.length : 0;
              const downloadSpeed = engine.swarm.downloadSpeed ? engine.swarm.downloadSpeed() : 0;
              const uploadSpeed = engine.swarm.uploadSpeed ? engine.swarm.uploadSpeed() : 0;
              
              if (peers > 0 || downloadSpeed > 0) {
                console.log(`[${torrentInfo.name}] Peers: ${peers}, Down: ${formatBytes(downloadSpeed)}/s, Up: ${formatBytes(uploadSpeed)}/s, Progress: ${torrentInfo.progress}%`);
              }
            }
          }, 30000); // Log every 30 seconds

          // Store logger reference for cleanup
          torrentInfo.networkLogger = networkLogger;
          
        } catch (err) {
          console.error('Error in ready event:', err);
          torrentInfo.status = 'error';
          torrentInfo.error = err.message;
        }
      });

      engine.on('download', () => {
        try {
          const swarm = engine.swarm;
          if (swarm && swarm.downloaded !== undefined && swarm.size !== undefined) {
            torrentInfo.progress = Math.round((swarm.downloaded / swarm.size) * 100);
            torrentInfo.downloadSpeed = swarm.downloadSpeed ? swarm.downloadSpeed() : 0;
            torrentInfo.uploadSpeed = swarm.uploadSpeed ? swarm.uploadSpeed() : 0;
            torrentInfo.peers = swarm.wires ? swarm.wires.length : 0;
            torrentInfo.downloaded = swarm.downloaded;
            torrentInfo.uploaded = swarm.uploaded;
          }
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
        
        // Log additional debug info for troubleshooting
        console.error('Error details:', {
          torrentId,
          torrentHash: normalizedHash,
          errorType: err.name,
          errorMessage: err.message,
          stack: err.stack
        });
      });

      // Start PeerFlix server with error handling
      engine.server.on('listening', () => {
        torrentInfo.serverPort = engine.server.address().port;
        console.log(`PeerFlix server listening on port ${torrentInfo.serverPort}`);
      });

      engine.server.on('error', (err) => {
        console.error(`PeerFlix server error for ${torrentId}:`, err);
        torrentInfo.status = 'error';
        torrentInfo.error = `Server error: ${err.message}`;
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
    lastAccessedAt: torrent.lastAccessedAt,
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
    lastAccessedAt: torrent.lastAccessedAt,
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

    // Update last accessed time for cleanup tracking
    torrentInfo.lastAccessedAt = new Date().toISOString();

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
  console.log(`Network stats: http://localhost:${PORT}/network`);
  console.log('');
  console.log('Network Optimization Settings:');
  console.log('- Max connections per torrent: 200');
  console.log('- Max uploads per torrent: 20');
  console.log('- Buffer size: 5MB');
  console.log('- Download limit: Unlimited');
  console.log('- Upload limit: Unlimited');
  console.log('- DHT, PEX, and Tracker: Enabled');
  console.log('');
  
  // Start automatic cleanup scheduler
  startTorrentCleanup();
  
  // Heartbeat to keep process alive and log status
  setInterval(() => {
    const totalPeers = Array.from(activeTorrents.values()).reduce((sum, t) => {
      return sum + (t.engine && t.engine.swarm && t.engine.swarm.wires ? t.engine.swarm.wires.length : 0);
    }, 0);
    
    console.log(`[${new Date().toISOString()}] Server alive - Active torrents: ${activeTorrents.size}, Total peers: ${totalPeers}`);
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
