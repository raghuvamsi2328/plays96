import express from 'express';

const router = express.Router();

// Add torrent endpoint
router.post('/add-torrent', async (req, res) => {
  try {
    const { magnetURI, name } = req.body;
    
    if (!magnetURI) {
      return res.status(400).json({ error: 'Magnet URI is required' });
    }

    console.log('Adding torrent:', magnetURI.slice(0, 100) + '...');

    const result = await req.torrentService.addTorrent(magnetURI, name);
    res.json(result);

  } catch (error) {
    console.error('Error in add-torrent endpoint:', error);
    res.status(500).json({ error: 'Failed to add torrent', details: error.message });
  }
});

// Get torrent status
router.get('/torrent/:id', (req, res) => {
  const torrentId = req.params.id;
  const torrent = req.torrentService.getTorrent(torrentId);
  
  if (!torrent) {
    return res.status(404).json({ error: 'Torrent not found' });
  }
  
  res.json(torrent);
});

// List all torrents
router.get('/torrents', (req, res) => {
  const torrents = req.torrentService.getAllTorrents();
  res.json(torrents);
});

// Remove torrent
router.delete('/torrent/:id', (req, res) => {
  const torrentId = req.params.id;
  
  if (req.torrentService.removeTorrent(torrentId)) {
    res.json({ message: 'Torrent removed' });
  } else {
    res.status(404).json({ error: 'Torrent not found' });
  }
});

export default router;
