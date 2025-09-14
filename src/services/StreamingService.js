import ffmpeg from 'fluent-ffmpeg';
import path from 'path';

class StreamingService {
  constructor() {
    this.setupFFmpeg();
  }

  // Configure FFmpeg
  setupFFmpeg() {
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
  }

  // Auto-select best video file from torrent files
  selectBestVideoFile(files) {
    const videoFiles = files.filter(f => f.isVideo);
    
    if (videoFiles.length === 0) {
      return null;
    }
    
    // Priority order: MP4 > MKV > AVI > others
    const priorityOrder = ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm'];
    
    const selectedFile = videoFiles.sort((a, b) => {
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
    
    console.log(`Auto-selected video file: ${selectedFile.name} (${this.formatBytes(selectedFile.size)})`);
    return selectedFile;
  }

  // Check if file needs remuxing
  needsRemux(filename) {
    return /\.(mkv|avi|wmv|flv)$/i.test(filename);
  }

  // Create FFmpeg remux stream
  createRemuxStream(sourceUrl, res) {
    console.log(`Creating remux stream from: ${sourceUrl}`);
    
    const ffmpegStream = ffmpeg(sourceUrl)
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
          res.status(500).json({ error: 'Streaming failed', details: err.message });
        }
      })
      .on('end', () => {
        console.log('FFmpeg finished');
      });

    return ffmpegStream;
  }

  // Set streaming headers
  setStreamingHeaders(res, range = null) {
    res.setHeader('Content-Type', 'video/mp4');
    res.setHeader('Accept-Ranges', 'bytes');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Range');
  }

  // Handle direct streaming (proxy from PeerFlix)
  async handleDirectStream(sourceUrl, res, range = null) {
    console.log(`Direct streaming from: ${sourceUrl}`);
    
    const fetch = (await import('node-fetch')).default;
    let requestOptions = {};

    if (range) {
      requestOptions.headers = { Range: range };
    }
    
    try {
      const response = await fetch(sourceUrl, requestOptions);
      
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
    } catch (error) {
      console.error('Direct streaming error:', error);
      throw error;
    }
  }

  // Format bytes utility
  formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }
}

export default StreamingService;
