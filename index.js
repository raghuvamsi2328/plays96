const express = require('express');
const cors = require('cors');
const morgan = require('morgan');
const ffmpeg = require('fluent-ffmpeg');
const lt = require('libtorrent');
const path = require('path');
const fs = require('fs');

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
app.use('/public', express.static('public'));

// LibTorrent session
const session = new lt.session();
const activeTorrents = new Map();

// Configure FFmpeg path (should be available in container)
ffmpeg.setFfmpegPath('/usr/local/bin/ffmpeg');
ffmpeg.setFfprobePath('/usr/local/bin/ffprobe');

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
    activeTorrents: activeTorrents.size
  });
});

// Add torrent endpoint
app.post('/add-torrent', async (req, res) => {
  try {
    const { magnetURI, name } = req.body;
    
    if (!magnetURI) {
      return res.status(400).json({ error: 'Magnet URI is required' });
    }

    const torrentId = Buffer.from(magnetURI).toString('base64').slice(0, 16);
    
    // Check if torrent is already active
    if (activeTorrents.has(torrentId)) {
      return res.json({ 
        torrentId, 
        status: 'already_active',
        torrent: activeTorrents.get(torrentId)
      });
    }

    // Add torrent to session
    const torrent = session.add_torrent({
      url: magnetURI,
      save_path: './downloads',
      name: name || `torrent_${torrentId}`
    });

    const torrentInfo = {
      id: torrentId,
      magnetURI,
      name: name || `torrent_${torrentId}`,
      status: 'downloading',
      progress: 0,
      downloadSpeed: 0,
      files: [],
      addedAt: new Date().toISOString()
    };

    activeTorrents.set(torrentId, torrentInfo);

    // Handle torrent events
    torrent.on('progress', (progress) => {
      torrentInfo.progress = Math.round(progress * 100);
    });

    torrent.on('download', (bytesDownloaded, totalBytes) => {
      torrentInfo.downloadSpeed = bytesDownloaded;
    });

    torrent.on('finished', () => {
      torrentInfo.status = 'completed';
      console.log(`Torrent ${torrentId} completed`);
    });

    torrent.on('metadata', () => {
      // Get file list when metadata is available
      const files = torrent.torrent_file().files();
      torrentInfo.files = files.map((file, index) => ({
        index,
        name: file.path,
        size: file.size,
        offset: file.offset,
        isVideo: /\.(mp4|avi|mkv|mov|wmv|flv|webm)$/i.test(file.path),
        isAudio: /\.(mp3|flac|wav|aac|ogg|wma)$/i.test(file.path)
      }));
    });

    res.json({ torrentId, status: 'added', torrent: torrentInfo });

  } catch (error) {
    console.error('Error adding torrent:', error);
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
    const torrent = activeTorrents.get(torrentId);
    
    if (!torrent) {
      return res.status(404).json({ error: 'Torrent not found' });
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
      const videoFiles = torrent.files.filter(f => f.isVideo);
      
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

    const filePath = path.join('./downloads', torrent.name, file.name);
    
    // Check if file exists and has some content
    if (!fs.existsSync(filePath)) {
      return res.status(404).json({ error: 'File not yet available' });
    }

    const stats = fs.statSync(filePath);
    if (stats.size === 0) {
      return res.status(425).json({ error: 'File is still downloading' });
    }

    // Set appropriate headers
    res.setHeader('Content-Type', 'video/mp4');
    res.setHeader('Accept-Ranges', 'bytes');
    res.setHeader('Cache-Control', 'no-cache');

    // Handle range requests for video seeking
    const range = req.headers.range;
    if (range) {
      const parts = range.replace(/bytes=/, "").split("-");
      const start = parseInt(parts[0], 10);
      const end = parts[1] ? parseInt(parts[1], 10) : stats.size - 1;
      const chunksize = (end - start) + 1;

      res.status(206);
      res.setHeader('Content-Range', `bytes ${start}-${end}/${stats.size}`);
      res.setHeader('Content-Length', chunksize);
    }

    // Determine if remuxing is needed
    const needsRemux = /\.(mkv|avi|wmv|flv)$/i.test(file.name);
    
    if (needsRemux) {
      console.log(`Remuxing ${file.name} to MP4`);
      
      // Create FFmpeg stream
      const ffmpegStream = ffmpeg(filePath)
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
      
      if (range) {
        const parts = range.replace(/bytes=/, "").split("-");
        const start = parseInt(parts[0], 10);
        const end = parts[1] ? parseInt(parts[1], 10) : stats.size - 1;
        
        const stream = fs.createReadStream(filePath, { start, end });
        stream.pipe(res);
      } else {
        const stream = fs.createReadStream(filePath);
        stream.pipe(res);
      }
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
    // TODO: Remove from libtorrent session
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
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('Shutting down gracefully...');
  session.destroy();
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('Shutting down gracefully...');
  session.destroy();
  process.exit(0);
});
