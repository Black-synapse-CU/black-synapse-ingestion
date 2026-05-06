import { useState } from 'react';
import { disconnectService, getOAuthUrl } from '../api';

function GoogleIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
  );
}

function MicrosoftIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M11.4 2H2v9.4h9.4V2z" fill="#F25022"/>
      <path d="M22 2h-9.4v9.4H22V2z" fill="#7FBA00"/>
      <path d="M11.4 12.6H2V22h9.4v-9.4z" fill="#00A4EF"/>
      <path d="M22 12.6h-9.4V22H22v-9.4z" fill="#FFB900"/>
    </svg>
  );
}

function DiscordIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="#5865F2">
      <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057c.002.022.015.043.032.054a19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
    </svg>
  );
}

function NotionIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" fill="#f0f6fc">
      <path d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952L12.21 19s0 .84-1.168.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.28-.887.747-.933zM1.936 1.035l13.31-.98c1.634-.14 2.055-.047 3.082.7l4.249 2.986c.7.513.934.653.934 1.213v16.378c0 1.026-.373 1.634-1.68 1.726l-15.458.934c-.98.047-1.448-.093-1.962-.747l-3.129-4.06c-.56-.747-.793-1.306-.793-1.96V2.667c0-.839.374-1.54 1.447-1.632z"/>
    </svg>
  );
}

const SERVICE_META = {
  google: {
    label: 'Google',
    description: 'Gmail · Calendar · Drive',
    Icon: GoogleIcon,
    color: '#4285f4',
    bg: 'rgba(66, 133, 244, 0.08)',
  },
  microsoft: {
    label: 'Microsoft',
    description: 'Outlook · Teams · OneDrive',
    Icon: MicrosoftIcon,
    color: '#00a4ef',
    bg: 'rgba(0, 164, 239, 0.08)',
  },
  discord: {
    label: 'Discord',
    description: 'Messaging · Guilds',
    Icon: DiscordIcon,
    color: '#5865f2',
    bg: 'rgba(88, 101, 242, 0.08)',
  },
  notion: {
    label: 'Notion',
    description: 'Pages · Databases',
    Icon: NotionIcon,
    color: '#f0f6fc',
    bg: 'rgba(240, 246, 252, 0.06)',
  },
};

export default function ServiceCard({ service, connection, onDisconnect }) {
  const meta = SERVICE_META[service] || { label: service, description: '', Icon: () => null, color: '#8b949e', bg: 'rgba(139,148,158,0.08)' };
  const { Icon } = meta;
  const isConnected = Boolean(connection);
  const inN8n = Boolean(connection?.n8n_credential_id);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState('');

  async function handleDisconnect() {
    setDisconnecting(true);
    setError('');
    try {
      await disconnectService(service);
      onDisconnect(service);
    } catch (err) {
      setError(err.response?.data?.error || 'Disconnect failed');
    } finally {
      setDisconnecting(false);
    }
  }

  function handleConnect() {
    window.location.href = getOAuthUrl(service);
  }

  const borderColor = isConnected ? meta.color + '40' : '#21262d';

  return (
    <div style={{ ...s.card, borderColor }}>
      <div style={s.top}>
        <div style={{ ...s.iconWrap, background: meta.bg, border: `1px solid ${meta.color}25` }}>
          <Icon />
        </div>
        <div style={s.info}>
          <div style={s.label}>{meta.label}</div>
          <div style={s.description}>{meta.description}</div>
        </div>
        <StatusBadge connected={isConnected} inN8n={inN8n} />
      </div>

      {isConnected && connection.connected_at && (
        <p style={s.meta}>
          Connected {new Date(connection.connected_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
        </p>
      )}

      {isConnected && !inN8n && (
        <div style={s.warnBox}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 1 }}>
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <span>
            {meta.label} is linked but no n8n credential was saved. Check{' '}
            <code style={s.code}>N8N_API_KEY</code> and Reconnect.
          </span>
        </div>
      )}

      {error && <p style={s.error}>{error}</p>}

      {isConnected ? (
        <div style={s.actions}>
          <button style={s.reconnectBtn} onClick={handleConnect}>
            Reconnect
          </button>
          <button style={s.disconnectBtn} onClick={handleDisconnect} disabled={disconnecting}>
            {disconnecting ? 'Removing…' : 'Disconnect'}
          </button>
        </div>
      ) : (
        <button
          style={{ ...s.connectBtn, background: meta.color }}
          onClick={handleConnect}
        >
          Connect {meta.label}
        </button>
      )}
    </div>
  );
}

function StatusBadge({ connected, inN8n }) {
  if (!connected) {
    return (
      <span style={{ ...s.badge, background: '#161b22', color: '#484f58', border: '1px solid #21262d' }}>
        Not connected
      </span>
    );
  }
  if (inN8n) {
    return (
      <span style={{ ...s.badge, background: 'rgba(63, 185, 80, 0.1)', color: '#3fb950', border: '1px solid rgba(63,185,80,0.25)' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#3fb950', display: 'inline-block' }} />
        Active
      </span>
    );
  }
  return (
    <span style={{ ...s.badge, background: 'rgba(210, 153, 34, 0.1)', color: '#d29922', border: '1px solid rgba(210,153,34,0.25)' }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#d29922', display: 'inline-block' }} />
      Local only
    </span>
  );
}

const s = {
  card: {
    background: '#111318',
    border: '1px solid',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
    transition: 'border-color 0.2s',
  },
  top: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  iconWrap: {
    width: 44,
    height: 44,
    borderRadius: 10,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  info: {
    flex: 1,
    minWidth: 0,
  },
  label: {
    fontWeight: 600,
    fontSize: 15,
    color: '#f0f6fc',
    letterSpacing: '-0.1px',
  },
  description: {
    fontSize: 12,
    color: '#484f58',
    marginTop: 2,
  },
  badge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 5,
    fontSize: 11,
    fontWeight: 600,
    padding: '3px 9px',
    borderRadius: 20,
    whiteSpace: 'nowrap',
    letterSpacing: '0.02em',
  },
  meta: {
    fontSize: 12,
    color: '#484f58',
    fontWeight: 500,
  },
  warnBox: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 8,
    fontSize: 12,
    color: '#d29922',
    lineHeight: 1.55,
    padding: '10px 12px',
    background: 'rgba(210, 153, 34, 0.06)',
    border: '1px solid rgba(210, 153, 34, 0.2)',
    borderRadius: 8,
  },
  code: {
    fontSize: 11,
    fontFamily: 'ui-monospace, monospace',
    background: '#0a0b0f',
    padding: '1px 5px',
    borderRadius: 4,
    color: '#8b949e',
  },
  error: {
    fontSize: 12,
    color: '#f85149',
  },
  actions: {
    display: 'flex',
    gap: 8,
  },
  connectBtn: {
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '9px 16px',
    fontWeight: 600,
    fontSize: 13,
    cursor: 'pointer',
    width: '100%',
    transition: 'opacity 0.15s',
  },
  reconnectBtn: {
    flex: 1,
    background: 'transparent',
    border: '1px solid #21262d',
    borderRadius: 8,
    color: '#8b949e',
    padding: '8px 12px',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
  disconnectBtn: {
    flex: 1,
    background: 'transparent',
    border: '1px solid rgba(248, 81, 73, 0.3)',
    borderRadius: 8,
    color: '#f85149',
    padding: '8px 12px',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
};
