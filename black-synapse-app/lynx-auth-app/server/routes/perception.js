const express = require('express');
const axios = require('axios');
const multer = require('multer');
const FormData = require('form-data');
const requireAuth = require('../middleware/requireAuth');

const router = express.Router();
const PERCEPTION_URL = process.env.PERCEPTION_URL || 'http://localhost:8089';
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 10 * 1024 * 1024 } });

router.get('/faces', requireAuth, async (_req, res) => {
  try {
    const { data } = await axios.get(`${PERCEPTION_URL}/stream-faces`, { timeout: 3000 });
    res.json(data);
  } catch {
    res.status(503).json({ ok: false, faces: [], error: 'Perception service unavailable' });
  }
});

router.get('/health', requireAuth, async (_req, res) => {
  try {
    const { data } = await axios.get(`${PERCEPTION_URL}/health`, { timeout: 3000 });
    res.json(data);
  } catch {
    res.status(503).json({ ok: false, error: 'Perception service unavailable' });
  }
});

// Enroll one uploaded image for the logged-in user
router.post('/enroll', requireAuth, upload.single('image'), async (req, res) => {
  const name = req.session.username;
  if (!name) return res.status(400).json({ ok: false, message: 'No username in session' });
  if (!req.file) return res.status(400).json({ ok: false, message: 'No image provided' });

  try {
    const form = new FormData();
    form.append('name', name);
    form.append('image', req.file.buffer, { filename: req.file.originalname, contentType: req.file.mimetype });

    const { data } = await axios.post(`${PERCEPTION_URL}/enroll-direct`, form, {
      headers: form.getHeaders(),
      timeout: 15000,
    });
    res.json(data);
  } catch (err) {
    const msg = err.response?.data?.message || 'Perception service unavailable';
    res.status(err.response?.status || 503).json({ ok: false, message: msg });
  }
});

module.exports = router;
