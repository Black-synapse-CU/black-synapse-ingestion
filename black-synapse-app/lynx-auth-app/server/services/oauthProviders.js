/**
 * OAuth provider configurations.
 *
 * Each provider defines:
 *  - OAuth URLs and scopes
 *  - The n8n credential type it maps to
 *  - buildCredentialData(tokens) → the data object n8n expects for that type
 *
 * To add a new provider: add a new entry following the same shape.
 */

const CALLBACK_BASE = process.env.CALLBACK_BASE_URL || 'http://localhost:4000';

/**
 * n8n Public API validates credential `data` with a JSON Schema built from non-hidden
 * fields only. OAuth2 types still expect these booleans/numbers, and the token blob
 * must live under oauthTokenData (ClientOAuth2 reads camelCase; Google returns snake_case).
 *
 * `serverUrl` is required by that schema when hidden `useDynamicClientRegistration` is omitted;
 * it is unused once `oauthTokenData` is set from our OAuth exchange.
 */
function n8nOAuth2TokenPayload(tokens) {
  const access = tokens.access_token;
  const refresh = tokens.refresh_token;
  return {
    serverUrl: '',
    sendAdditionalBodyProperties: false,
    // Satisfies n8n allOf branches that still require this key when sendAdditionalBodyProperties is false (type json, default '' in UI).
    additionalBodyProperties: '',
    // ignoreSSLIssues / tokenExpiredStatusCode are doNotInherit on oAuth2Api — omitted from Public API schema (400 additionalProperties).
    // n8n ClientOAuth2 + Public API schema: use camelCase only (duplicates broke validation).
    oauthTokenData: {
      accessToken: access,
      refreshToken: refresh,
      tokenType: tokens.token_type || 'Bearer',
      ...(tokens.expires_in != null && { expiresIn: tokens.expires_in }),
    },
  };
}

const providers = {
  // ─── Google (Gmail, Calendar, Drive) ────────────────────────────────────────
  google: {
    clientId:     process.env.GOOGLE_CLIENT_ID,
    clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    authUrl:      'https://accounts.google.com/o/oauth2/v2/auth',
    tokenUrl:     'https://oauth2.googleapis.com/token',
    scopes: [
      'https://mail.google.com/',
      'https://www.googleapis.com/auth/gmail.modify',
      'https://www.googleapis.com/auth/calendar',
      'https://www.googleapis.com/auth/drive',
      'openid',
      'email',
      'profile',
    ],
    // access_type=offline and prompt=consent force a refresh_token to be returned
    extraAuthParams: {
      access_type: 'offline',
      prompt: 'consent',
    },
    callbackUrl: `${CALLBACK_BASE}/api/oauth/google/callback`,
    // n8n credential `name` (see GmailOAuth2Api.credentials.ts) — not the old `gmailOAuth2Api` id
    n8nCredentialType: 'gmailOAuth2',
    buildCredentialData(tokens) {
      return {
        clientId: process.env.GOOGLE_CLIENT_ID,
        clientSecret: process.env.GOOGLE_CLIENT_SECRET,
        ...n8nOAuth2TokenPayload(tokens),
      };
    },
  },

  // ─── Microsoft (Outlook, Teams, OneDrive) ───────────────────────────────────
  microsoft: {
    clientId:     process.env.MICROSOFT_CLIENT_ID,
    clientSecret: process.env.MICROSOFT_CLIENT_SECRET,
    tenantId:     process.env.MICROSOFT_TENANT_ID || 'common',
    get authUrl() {
      return `https://login.microsoftonline.com/${this.tenantId}/oauth2/v2.0/authorize`;
    },
    get tokenUrl() {
      return `https://login.microsoftonline.com/${this.tenantId}/oauth2/v2.0/token`;
    },
    scopes: [
      'offline_access',
      'User.Read',
      'Mail.ReadWrite',
      'Mail.Send',
      'Calendars.ReadWrite',
    ],
    extraAuthParams: {},
    callbackUrl: `${CALLBACK_BASE}/api/oauth/microsoft/callback`,
    n8nCredentialType: 'microsoftOutlookOAuth2Api',
    buildCredentialData(tokens) {
      const tenant = process.env.MICROSOFT_TENANT_ID || 'common';
      return {
        clientId: process.env.MICROSOFT_CLIENT_ID,
        clientSecret: process.env.MICROSOFT_CLIENT_SECRET,
        authUrl: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/authorize`,
        accessTokenUrl: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`,
        graphApiBaseUrl: 'https://graph.microsoft.com',
        useShared: false,
        ...n8nOAuth2TokenPayload(tokens),
      };
    },
  },

  // ─── Discord ─────────────────────────────────────────────────────────────────
  discord: {
    clientId:     process.env.DISCORD_CLIENT_ID,
    clientSecret: process.env.DISCORD_CLIENT_SECRET,
    authUrl:      'https://discord.com/api/oauth2/authorize',
    tokenUrl:     'https://discord.com/api/oauth2/token',
    scopes:       ['identify', 'guilds', 'messages.read'],
    extraAuthParams: {},
    callbackUrl: `${CALLBACK_BASE}/api/oauth/discord/callback`,
    n8nCredentialType: 'discordOAuth2Api',
    buildCredentialData(tokens) {
      return {
        clientId: process.env.DISCORD_CLIENT_ID,
        clientSecret: process.env.DISCORD_CLIENT_SECRET,
        botToken: '',
        customScopes: false,
        ...n8nOAuth2TokenPayload(tokens),
      };
    },
  },

  // ─── Notion ──────────────────────────────────────────────────────────────────
  // Notion uses Basic Auth on the token exchange, not form body creds.
  // It returns a permanent access_token — no refresh token needed.
  notion: {
    clientId:     process.env.NOTION_CLIENT_ID,
    clientSecret: process.env.NOTION_CLIENT_SECRET,
    authUrl:      'https://api.notion.com/v1/oauth/authorize',
    tokenUrl:     'https://api.notion.com/v1/oauth/token',
    scopes:       [], // Notion doesn't use scopes in the URL
    extraAuthParams: {
      owner: 'user',
    },
    callbackUrl: `${CALLBACK_BASE}/api/oauth/notion/callback`,
    n8nCredentialType: 'notionApi',
    buildCredentialData(tokens) {
      return {
        apiKey: tokens.access_token, // Notion uses a permanent token stored as an API key
      };
    },
    // Flag: use Basic Auth on token exchange instead of body params
    useBasicAuth: true,
  },
};

module.exports = providers;
