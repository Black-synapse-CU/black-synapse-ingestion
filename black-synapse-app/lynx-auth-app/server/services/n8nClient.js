const axios = require('axios');

/**
 * Axios instance pre-configured for the n8n REST API.
 * n8n self-hosted exposes its API at /api/v1 and requires an API key header.
 * Enable it in n8n: Settings → API → Enable Public API → copy the key.
 */
const n8nAxios = axios.create({
  baseURL: `${process.env.N8N_BASE_URL || 'http://localhost:5678'}/api/v1`,
  headers: {
    'X-N8N-API-KEY': process.env.N8N_API_KEY,
    'Content-Type': 'application/json',
  },
});

/**
 * Create a new credential in n8n.
 *
 * @param {string} name  - Human-readable label, e.g. "Gmail – elyas"
 * @param {string} type  - n8n credential type ID, e.g. "gmailOAuth2Api"
 * @param {object} data  - Token/key fields expected by that credential type
 * @returns {object}     - n8n credential object including { id, name, type }
 */
async function createCredential(name, type, data) {
  const res = await n8nAxios.post('/credentials', { name, type, data });
  return res.data;
}

/**
 * Update an existing credential (e.g. when the user reconnects after token expiry).
 * n8n exposes PATCH /api/v1/credentials/:id (PUT is not supported and returns 405).
 */
async function updateCredential(credentialId, name, type, data) {
  const res = await n8nAxios.patch(`/credentials/${credentialId}`, { name, type, data });
  return res.data;
}

/**
 * Delete a credential from n8n (called on disconnect).
 */
async function deleteCredential(credentialId) {
  await n8nAxios.delete(`/credentials/${credentialId}`);
}

/**
 * List all credentials stored in n8n (for debugging / admin).
 */
async function listCredentials() {
  const res = await n8nAxios.get('/credentials');
  return res.data;
}

module.exports = { createCredential, updateCredential, deleteCredential, listCredentials };
