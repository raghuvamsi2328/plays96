import express from 'express';
import cors from 'cors';
import morgan from 'morgan';
import path from 'path';
import { fileURLToPath } from 'url';

// Services
import TorrentService from './services/TorrentService.js';
import StreamingService from './services/StreamingService.js';

// Routes
import systemRoutes from './routes/system.js';
import torrentRoutes from './routes/torrents.js';
import streamingRoutes from './routes/streaming.js';

// Get __dirname equivalent for ES modules
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

class TorrentStreamingApp {
  constructor() {
    this.app = express();
    this.PORT = process.env.PORT || 6991;
    
    // Initialize services
    this.torrentService = new TorrentService();
    this.streamingService = new StreamingService();
    
    this.setupGlobalErrorHandlers();
    this.setupMiddleware();
    this.setupRoutes();
    this.setupErrorHandling();
  }

  // Setup global error handlers
  setupGlobalErrorHandlers() {
    process.on('uncaughtException', (err) => {
      console.error('Uncaught Exception:', err);
      console.error('Stack:', err.stack);
    });

    process.on('unhandledRejection', (reason, promise) => {
      console.error('Unhandled Rejection at:', promise, 'reason:', reason);
    });
  }

  // Setup middleware
  setupMiddleware() {
    this.app.use(cors());
    this.app.use(morgan('combined'));
    this.app.use(express.json());

    // Serve static files (for test interface)
    this.app.use('/public', express.static(path.join(__dirname, '../public')));

    // Inject services into requests
    this.app.use((req, res, next) => {
      req.torrentService = this.torrentService;
      req.streamingService = this.streamingService;
      next();
    });
  }

  // Setup routes
  setupRoutes() {
    // System routes (health, debug)
    this.app.use('/', systemRoutes);
    
    // Torrent management routes
    this.app.use('/', torrentRoutes);
    
    // Streaming routes
    this.app.use('/', streamingRoutes);
  }

  // Setup error handling middleware
  setupErrorHandling() {
    this.app.use((error, req, res, next) => {
      console.error('Unhandled error:', error);
      res.status(500).json({ error: 'Internal server error' });
    });
  }

  // Setup graceful shutdown handlers
  setupShutdownHandlers() {
    const shutdown = () => {
      console.log('Shutting down gracefully...');
      this.torrentService.shutdown();
      process.exit(0);
    };

    process.on('SIGTERM', shutdown);
    process.on('SIGINT', shutdown);
  }

  // Start the server
  start() {
    this.setupShutdownHandlers();
    
    this.app.listen(this.PORT, '0.0.0.0', () => {
      console.log(`🚀 Torrent Streaming Server running on port ${this.PORT}`);
      console.log(`📊 Health check: http://localhost:${this.PORT}/health`);
      console.log(`🔍 Debug info: http://localhost:${this.PORT}/debug`);
      console.log(`🎬 Test interface: http://localhost:${this.PORT}/public/test.html`);
      
      // Heartbeat to keep process alive and log status
      setInterval(() => {
        const activeTorrents = this.torrentService.getAllTorrents().length;
        console.log(`[${new Date().toISOString()}] Server alive - Active torrents: ${activeTorrents}`);
      }, 30000); // Every 30 seconds
    });
  }
}

export default TorrentStreamingApp;
