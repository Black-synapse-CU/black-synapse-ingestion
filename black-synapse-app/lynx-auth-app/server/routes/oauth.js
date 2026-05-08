const express = require('express');
const axios = require('axios');
const requireAuth = require('../middleware/requireAuth');
const providers = require('../services/oauthProviders');
const { getDb } = require('../services/db');
const n8n = require('../services/n8nClient');

const router = express.Router();
const CLIENT_URL = process.env.CLIENT_URL || 'http://localhost:3000';

/**
 * Build the provider authorization URL.
 */
function buildAuthUrl(providerName, state) {
  const cfg = providers[providerName];

  const params = new URLSearchParams({
    client_id: cfg.clientId,
    redirect_uri: cfg.callbackUrl,
    response_type: 'code',
    state,
    ...(cfg.scopes.length > 0 ? { scope: cfg.scopes.join(' ') } : {}),
    ...cfg.extraAuthParams,
  });

  return `${cfg.authUrl}?${params.toString()}`;
}

/**
 * Exchange authorization code for tokens.
 */
async function exchangeCode(providerName, code) {
  const cfg = providers[providerName];

  const body = {
    grant_type: 'authorization_code',
    code,
    redirect_uri: cfg.callbackUrl,
    client_id: cfg.clientId,
    client_secret: cfg.clientSecret,
  };

  if (cfg.useBasicAuth) {
    const credentials = Buffer.from(`${cfg.clientId}:${cfg.clientSecret}`).toString('base64');

    const res = await axios.post(cfg.tokenUrl, body, {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Basic ${credentials}`,
        'Notion-Version': '2022-06-28',
      },
    });

    return res.data;
  }

  const res = await axios.post(
    cfg.tokenUrl,
    new URLSearchParams(body).toString(),
    {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    }
  );

  return res.data;
}

const PROVIDER_LABEL = {
  google: 'Google',
  microsoft: 'Microsoft',
  discord: 'Discord',
  notion: 'Notion',
};

const PROVIDERS = ['google', 'microsoft', 'discord', 'notion'];

PROVIDERS.forEach((providerName) => {
  router.get(`/${providerName}`, requireAuth, (req, res) => {
    const cfg = providers[providerName];
    if (!cfg?.clientId?.trim() || !cfg?.clientSecret?.trim()) {
      return res.redirect(
        `${CLIENT_URL}?oauth_error=not_configured&service=${encodeURIComponent(providerName)}`
      );
    }

    const state = Buffer.from(
      JSON.stringify({
        userId: req.session.userId,
        provider: providerName,
      })
    ).toString('base64');

    req.session.oauthState = state;
    res.redirect(buildAuthUrl(providerName, state));
  });

  router.get(`/${providerName}/callback`, async (req, res) => {
    const { code, state, error } = req.query;

    if (error) {
      console.warn(`OAuth denied for ${providerName}:`, error);
      return res.redirect(
        `${CLIENT_URL}?oauth_error=${encodeURIComponent(error)}&service=${providerName}`
      );
    }

    let stateData;
    try {
      stateData = JSON.parse(Buffer.from(state, 'base64').toString('utf8'));
    } catch {
      return res.redirect(`${CLIENT_URL}?oauth_error=invalid_state&service=${providerName}`);
    }

    if (!stateData.userId || stateData.provider !== providerName) {
      return res.redirect(`${CLIENT_URL}?oauth_error=state_mismatch&service=${providerName}`);
    }

    try {
      const tokens = await exchangeCode(providerName, code);
      const db = getDb();
      const cfg = providers[providerName];
      const credData = cfg.buildCredentialData(tokens);

      const existing = db
        .prepare(
          'SELECT n8n_credential_id FROM connections WHERE user_id = ? AND service = ?'
        )
        .get(stateData.userId, providerName);

      const userRow = db
        .prepare('SELECT username FROM users WHERE id = ?')
        .get(stateData.userId);
      const label = PROVIDER_LABEL[providerName] || providerName;
      const credName = `Lynx · ${label} · ${userRow?.username || stateData.userId}`;

      let n8nCredentialId = existing?.n8n_credential_id || null;

      if (!process.env.N8N_API_KEY?.trim()) {
        db.prepare(`
          INSERT INTO connections (user_id, service, access_token, refresh_token, n8n_credential_id)
          VALUES (?, ?, ?, ?, ?)
          ON CONFLICT(user_id, service) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            connected_at = CURRENT_TIMESTAMP,
            n8n_credential_id = COALESCE(connections.n8n_credential_id, excluded.n8n_credential_id)
        `).run(
          stateData.userId,
          providerName,
          tokens.access_token || null,
          tokens.refresh_token || null,
          n8nCredentialId
        );
        return res.redirect(
          `${CLIENT_URL}?oauth_error=n8n_not_configured&service=${providerName}`
        );
      }

      try {
        if (n8nCredentialId) {
          try {
            await n8n.updateCredential(
              n8nCredentialId,
              credName,
              cfg.n8nCredentialType,
              credData
            );
          } catch (updateErr) {
            const st = updateErr.response?.status;
            const msg = updateErr.response?.data?.message;
            const missingInN8n =
              st === 404 ||
              (typeof msg === 'string' &&
                msg.toLowerCase().includes('credential not found'));
            if (!missingInN8n) throw updateErr;
            console.warn(
              `n8n credential ${n8nCredentialId} missing (${st}); creating a new one for ${providerName}`
            );
            const created = await n8n.createCredential(
              credName,
              cfg.n8nCredentialType,
              credData
            );
            n8nCredentialId = created?.id ?? created?.data?.id;
            if (!n8nCredentialId) {
              console.error('n8n createCredential returned no id:', created);
              throw new Error('n8n did not return a credential id');
            }
          }
        } else {
          const created = await n8n.createCredential(
            credName,
            cfg.n8nCredentialType,
            credData
          );
          n8nCredentialId = created?.id ?? created?.data?.id;
          if (!n8nCredentialId) {
            console.error('n8n createCredential returned no id:', created);
            throw new Error('n8n did not return a credential id');
          }
        }
      } catch (n8nErr) {
        const httpStatus = n8nErr.response?.status;
        const n8nBody = n8nErr.response?.data;
        console.error(
          `n8n credential provision failed for ${providerName}:`,
          n8nBody || n8nErr.message
        );
        const hint =
          typeof n8nBody?.message === 'string'
            ? n8nBody.message.slice(0, 200)
            : n8nErr.code === 'ECONNREFUSED'
              ? 'Cannot reach n8n (connection refused). Check N8N_BASE_URL and that the container publishes a port to the host.'
              : '';
        const q = new URLSearchParams({
          oauth_error: 'n8n_provision_failed',
          service: providerName,
        });
        if (httpStatus) q.set('n8n_http', String(httpStatus));
        if (hint) q.set('n8n_hint', hint);
        db.prepare(`
          INSERT INTO connections (user_id, service, access_token, refresh_token, n8n_credential_id)
          VALUES (?, ?, ?, ?, ?)
          ON CONFLICT(user_id, service) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            connected_at = CURRENT_TIMESTAMP,
            n8n_credential_id = COALESCE(connections.n8n_credential_id, excluded.n8n_credential_id)
        `).run(
          stateData.userId,
          providerName,
          tokens.access_token || null,
          tokens.refresh_token || null,
          existing?.n8n_credential_id || null
        );
        return res.redirect(`${CLIENT_URL}?${q.toString()}`);
      }

      db.prepare(`
        INSERT INTO connections (user_id, service, access_token, refresh_token, n8n_credential_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, service) DO UPDATE SET
          access_token = excluded.access_token,
          refresh_token = excluded.refresh_token,
          connected_at = CURRENT_TIMESTAMP,
          n8n_credential_id = excluded.n8n_credential_id
      `).run(
        stateData.userId,
        providerName,
        tokens.access_token || null,
        tokens.refresh_token || null,
        n8nCredentialId
      );

      return res.redirect(`${CLIENT_URL}?oauth_success=true&service=${providerName}`);
    } catch (err) {
      console.error(
        `Token exchange failed for ${providerName}:`,
        err.response?.data || err.message
      );
      return res.redirect(
        `${CLIENT_URL}?oauth_error=token_exchange_failed&service=${providerName}`
      );
    }
  });
});

module.exports = router;