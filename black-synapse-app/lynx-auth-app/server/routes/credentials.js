const express = require('express');
const requireAuth = require('../middleware/requireAuth');
const n8n = require('../services/n8nClient');
const { getDb } = require('../services/db');

const router = express.Router();

// ── GET /api/credentials ──────────────────────────────────────────────────────
// Returns all services the logged-in user has connected.
router.get('/', requireAuth, (req, res) => {
  const db = getDb();
  const connections = db.prepare(`
    SELECT service, n8n_credential_id, connected_at
    FROM connections
    WHERE user_id = ?
    ORDER BY connected_at DESC
  `).all(req.session.userId);

  res.json(connections);
});

// ── DELETE /api/credentials/:service ─────────────────────────────────────────
// Disconnects a service: removes from n8n and deletes the local record.
router.delete('/:service', requireAuth, async (req, res) => {
  const { service } = req.params;
  const db = getDb();

  const connection = db.prepare(
    'SELECT * FROM connections WHERE user_id = ? AND service = ?'
  ).get(req.session.userId, service);

  if (!connection) {
    return res.status(404).json({ error: `No connection found for service: ${service}` });
  }

  try {
    if (connection.n8n_credential_id) {
      await n8n.deleteCredential(connection.n8n_credential_id);
    }
  } catch (err) {
    if (err.response?.status !== 404) {
      console.error(`Failed to disconnect ${service}:`, err.response?.data || err.message);
      return res.status(500).json({ error: 'Failed to disconnect service. Check n8n API connectivity.' });
    }
    // 404 means it's already gone from n8n — proceed with local cleanup
  }

  db.prepare('DELETE FROM connections WHERE user_id = ? AND service = ?')
    .run(req.session.userId, service);

  res.json({ success: true, service });
});

module.exports = router;
