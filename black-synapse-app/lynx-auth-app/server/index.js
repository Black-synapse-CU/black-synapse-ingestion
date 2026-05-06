const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '.env') });
const express = require('express');
const session = require('express-session');
const cors = require('cors');
const { initDb } = require('./services/db');

const authRoutes = require('./routes/auth');
const oauthRoutes = require('./routes/oauth');
const credentialsRoutes = require('./routes/credentials');
const emailRoutes = require('./routes/email');
const uploadsRoutes = require('./routes/uploads');
const perceptionRoutes = require('./routes/perception');

const app = express();
const PORT = process.env.PORT || 4000;

app.use(cors({
  origin: process.env.CLIENT_URL || 'http://localhost:3000',
  credentials: true,
}));

app.use(express.json());

app.use(session({
  secret: process.env.SESSION_SECRET || 'change-this-in-production',
  resave: false,
  saveUninitialized: false,
  cookie: {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    maxAge: 24 * 60 * 60 * 1000,
  },
}));

app.use('/api/auth', authRoutes);
app.use('/api/oauth', oauthRoutes);
app.use('/api/credentials', credentialsRoutes);
app.use('/api/email', emailRoutes);
app.use('/api/uploads', uploadsRoutes);
app.use('/api/perception', perceptionRoutes);

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', service: 'lynx-auth-server' });
});

initDb();

app.listen(PORT, () => {
  console.log(`✅ Lynx Auth Server running at http://localhost:${PORT}`);
});