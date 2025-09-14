import peerflix from 'peerflix';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

class TorrentService {
  constructor() {
    this.activeTorrents = new Map();
    this.cleanupInterval = 12 * 60 * 60 * 1000; // 12 hours in milliseconds
    this.startCleanupScheduler();
  }

  // Start automatic cleanup scheduler
  startCleanupScheduler() {
    setInterval(() => {
      this.cleanupInactiveTorrents();
    }, 60 * 60 * 1000); // Check every hour
    
    console.log('Torrent cleanup scheduler started (12-hour inactive cleanup)');
  }

  // Clean up torrents that haven't been streamed for 12+ hours
  cleanupInactiveTorrents() {
    const now = Date.now();
    const toRemove = [];

    this.activeTorrents.forEach((torrent, id) => {
      const lastStreamed = torrent.lastStreamedAt || torrent.addedAt;
      const timeSinceLastStream = now - new Date(lastStreamed).getTime();
      
      if (timeSinceLastStream > this.cleanupInterval) {
        console.log(`Cleaning up inactive torrent: ${torrent.name} (${id})`);
        toRemove.push(id);
      }
    });

    // Remove inactive torrents
    toRemove.forEach(id => {
      this.removeTorrent(id);
    });

    if (toRemove.length > 0) {
      console.log(`Cleaned up ${toRemove.length} inactive torrents`);
    }
  }

  // Update last streamed timestamp
  updateLastStreamed(torrentId) {
    const torrent = this.activeTorrents.get(torrentId);
    if (torrent) {
      torrent.lastStreamedAt = new Date().toISOString();
    }
  }

  // Add a new torrent
  async addTorrent(magnetURI, name = null) {
    const torrentId = Buffer.from(magnetURI).toString('base64').slice(0, 16);
    
    // Check if torrent is already active
    if (this.activeTorrents.has(torrentId)) {
      return { 
        torrentId, 
        status: 'already_active',
        torrent: this.getCleanTorrentData(this.activeTorrents.get(torrentId))
      };
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
      lastStreamedAt: new Date().toISOString(),
      engine: null,
      serverPort: null
    };

    this.activeTorrents.set(torrentId, torrentInfo);

    // Create PeerFlix engine asynchronously
    this.createPeerFlixEngine(torrentInfo);

    return { 
      torrentId, 
      status: 'adding', 
      torrent: this.getCleanTorrentData(torrentInfo) 
    };
  }

  // Create and configure PeerFlix engine
  createPeerFlixEngine(torrentInfo) {
    try {
      console.log('Creating PeerFlix engine for:', torrentInfo.name);
      
      const engine = peerflix(torrentInfo.magnetURI, {
        connections: 50,
        uploads: 5,
        path: path.join(__dirname, '../../downloads'),
        buffer: (1.5 * 1000 * 1000).toString(),
        port: 0
      });

      torrentInfo.engine = engine;
      torrentInfo.status = 'connecting';

      this.setupEngineEventHandlers(engine, torrentInfo);

    } catch (err) {
      console.error('Error creating PeerFlix engine:', err);
      torrentInfo.status = 'error';
      torrentInfo.error = err.message;
    }
  }

  // Setup event handlers for PeerFlix engine
  setupEngineEventHandlers(engine, torrentInfo) {
    engine.on('ready', () => {
      try {
        console.log('PeerFlix engine ready for:', torrentInfo.name);
        torrentInfo.status = 'downloading';
        torrentInfo.name = torrentInfo.name || engine.torrent.name || torrentInfo.name;
        
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

        console.log(`Found ${torrentInfo.files.length} files in torrent: ${torrentInfo.name}`);
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
      console.log(`Torrent ${torrentInfo.id} completed: ${torrentInfo.name}`);
    });

    engine.on('error', (err) => {
      console.error(`PeerFlix engine error for ${torrentInfo.id}:`, err);
      torrentInfo.status = 'error';
      torrentInfo.error = err.message;
    });

    // Start PeerFlix server
    engine.server.on('listening', () => {
      torrentInfo.serverPort = engine.server.address().port;
      console.log(`PeerFlix server listening on port ${torrentInfo.serverPort} for: ${torrentInfo.name}`);
    });
  }

  // Get torrent by ID
  getTorrent(torrentId) {
    const torrent = this.activeTorrents.get(torrentId);
    return torrent ? this.getCleanTorrentData(torrent) : null;
  }

  // Get all torrents
  getAllTorrents() {
    return Array.from(this.activeTorrents.values()).map(torrent => 
      this.getCleanTorrentData(torrent)
    );
  }

  // Get raw torrent (with engine) for internal use
  getRawTorrent(torrentId) {
    return this.activeTorrents.get(torrentId);
  }

  // Remove torrent
  removeTorrent(torrentId) {
    if (this.activeTorrents.has(torrentId)) {
      const torrentInfo = this.activeTorrents.get(torrentId);
      
      // Destroy PeerFlix engine
      if (torrentInfo.engine) {
        try {
          torrentInfo.engine.destroy();
          console.log(`PeerFlix engine destroyed for torrent: ${torrentInfo.name} (${torrentId})`);
        } catch (err) {
          console.error('Error destroying PeerFlix engine:', err);
        }
      }
      
      this.activeTorrents.delete(torrentId);
      return true;
    }
    return false;
  }

  // Get clean torrent data (without circular references)
  getCleanTorrentData(torrent) {
    return {
      id: torrent.id,
      magnetURI: torrent.magnetURI,
      name: torrent.name,
      status: torrent.status,
      progress: torrent.progress,
      downloadSpeed: torrent.downloadSpeed,
      files: torrent.files,
      addedAt: torrent.addedAt,
      lastStreamedAt: torrent.lastStreamedAt,
      serverPort: torrent.serverPort,
      error: torrent.error || null
    };
  }

  // Get debug info
  getDebugInfo() {
    const torrents = Array.from(this.activeTorrents.values()).map(t => ({
      id: t.id,
      name: t.name,
      status: t.status,
      progress: t.progress,
      filesCount: t.files.length,
      error: t.error || null,
      serverPort: t.serverPort || null,
      addedAt: t.addedAt,
      lastStreamedAt: t.lastStreamedAt
    }));
    
    return {
      activeTorrents: torrents,
      totalTorrents: this.activeTorrents.size
    };
  }

  // Shutdown all torrents
  shutdown() {
    console.log('Shutting down TorrentService...');
    this.activeTorrents.forEach((torrent) => {
      if (torrent.engine) {
        torrent.engine.destroy();
      }
    });
    this.activeTorrents.clear();
  }
}

export default TorrentService;
