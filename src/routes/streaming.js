import express from 'express';

const router = express.Router();

// Stream file with optional FFmpeg remuxing
router.get('/stream/:torrentId/:fileIndex?', async (req, res) => {
  try {
    const { torrentId, fileIndex } = req.params;
    const torrentInfo = req.torrentService.getRawTorrent(torrentId);
    
    if (!torrentInfo) {
      return res.status(404).json({ error: 'Torrent not found' });
    }

    if (!torrentInfo.engine || torrentInfo.status === 'error') {
      return res.status(404).json({ error: 'Torrent engine not ready' });
    }

    // Update last streamed timestamp
    req.torrentService.updateLastStreamed(torrentId);

    let file;
    
    if (fileIndex !== undefined) {
      // Specific file requested
      file = torrentInfo.files[parseInt(fileIndex)];
      if (!file) {
        return res.status(404).json({ error: 'File not found' });
      }
    } else {
      // Auto-select best video file
      file = req.streamingService.selectBestVideoFile(torrentInfo.files);
      if (!file) {
        return res.status(404).json({ error: 'No video files found in torrent' });
      }
    }

    // Get the file stream URL from PeerFlix server
    const streamUrl = `http://localhost:${torrentInfo.serverPort}/${file.index}`;
    
    console.log(`Streaming: ${file.name} from ${streamUrl}`);

    // Set streaming headers
    req.streamingService.setStreamingHeaders(res);

    // Handle range requests
    const range = req.headers.range;

    // Determine if remuxing is needed
    const needsRemux = req.streamingService.needsRemux(file.name);
    
    if (needsRemux) {
      console.log(`Remuxing ${file.name} to MP4`);
      
      // Create FFmpeg stream
      const ffmpegStream = req.streamingService.createRemuxStream(streamUrl, res);
      
      // Pipe to response
      ffmpegStream.pipe(res, { end: true });

    } else {
      // Direct streaming for compatible formats
      console.log(`Direct streaming ${file.name}`);
      await req.streamingService.handleDirectStream(streamUrl, res, range);
    }

  } catch (error) {
    console.error('Streaming error:', error);
    if (!res.headersSent) {
      res.status(500).json({ error: 'Streaming failed', details: error.message });
    }
  }
});

export default router;
