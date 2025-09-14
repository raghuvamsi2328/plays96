import express from 'express';

const router = express.Router();

// Health check endpoint
router.get('/health', (req, res) => {
  res.json({ 
    status: 'OK', 
    timestamp: new Date().toISOString(),
    activeTorrents: req.torrentService.getAllTorrents().length,
    memory: process.memoryUsage(),
    uptime: process.uptime()
  });
});

// Debug endpoint
router.get('/debug', (req, res) => {
  res.json(req.torrentService.getDebugInfo());
});

export default router;
