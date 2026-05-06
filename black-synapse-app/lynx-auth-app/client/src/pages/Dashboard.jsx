import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getConnections } from '../api';
import Header from '../components/Header';
import ServiceCard from '../components/ServiceCard';
import FileUploads from '../components/FileUploads';
import FaceEnroll from '../components/FaceEnroll';

const SERVICES = ['google', 'microsoft', 'discord', 'notion'];

export default function Dashboard({ user, onLogout }) {
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const connectionMap = connections.reduce((acc, c) => {
    acc[c.service] = c;
    return acc;
  }, {});

  const fetchConnections = useCallback(async () => {
    try {
      const data = await getConnections();
      setConnections(data);
    } catch {
      // leave empty
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConnections();
  }, [fetchConnections]);

  useEffect(() => {
    const success = searchParams.get('oauth_success');
    const error = searchParams.get('oauth_error');
    const service = searchParams.get('service');

    if (success === 'true' && service) {
      showToast('success', `${capitalize(service)} connected successfully`);
      fetchConnections();
    } else if (error && service) {
      const http = searchParams.get('n8n_http');
      const hint = searchParams.get('n8n_hint');
      let msg = `Failed to connect ${capitalize(service)}`;
      if (http) msg += ` (HTTP ${http})`;
      if (hint) msg += ` — ${hint}`;
      showToast('error', msg);
    }

    if (success || error) setSearchParams({});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function showToast(type, message) {
    setToast({ type, message });
    setTimeout(() => setToast(null), 4500);
  }

  function handleDisconnect(service) {
    setConnections((prev) => prev.filter((c) => c.service !== service));
    showToast('success', `${capitalize(service)} disconnected`);
  }

  const connectedCount = connections.filter((c) => c.n8n_credential_id).length;

  return (
    <div style={s.page}>
      <Header user={user} onLogout={onLogout} />

      <main style={s.main}>
        <div style={s.hero}>
          <div style={s.heroLeft}>
            <h1 style={s.title}>Connected Services</h1>
            <p style={s.subtitle}>
              Manage the accounts Lynx can access on your behalf. Credentials are provisioned
              directly into n8n and available to any workflow immediately.
            </p>
          </div>
          {!loading && (
            <div style={s.statPill}>
              <span style={s.statNum}>{connectedCount}</span>
              <span style={s.statLabel}>of {SERVICES.length} active</span>
            </div>
          )}
        </div>

        {toast && <Toast toast={toast} />}

        {loading ? (
          <div style={s.loadingRow}>
            <div style={s.spinner} />
            <span style={{ color: '#484f58', fontSize: 13 }}>Loading connections…</span>
          </div>
        ) : (
          <div style={s.grid}>
            {SERVICES.map((service) => (
              <ServiceCard
                key={service}
                service={service}
                connection={connectionMap[service] || null}
                onDisconnect={handleDisconnect}
              />
            ))}
          </div>
        )}

        <FileUploads onToast={showToast} />
        <FaceEnroll user={user} />

        <p style={s.footer}>
          Credentials are encrypted at rest in n8n. Disconnecting removes them from n8n immediately.
        </p>
      </main>
    </div>
  );
}

function Toast({ toast }) {
  const isSuccess = toast.type === 'success';
  return (
    <div style={{
      ...s.toast,
      background: isSuccess ? 'rgba(63, 185, 80, 0.08)' : 'rgba(248, 81, 73, 0.08)',
      border: `1px solid ${isSuccess ? 'rgba(63,185,80,0.25)' : 'rgba(248,81,73,0.25)'}`,
      color: isSuccess ? '#3fb950' : '#f85149',
      animation: 'fadeUp 0.2s ease',
    }}>
      {isSuccess ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      )}
      {toast.message}
    </div>
  );
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

const s = {
  page: {
    minHeight: '100vh',
    background: '#0a0b0f',
  },
  main: {
    maxWidth: 880,
    margin: '0 auto',
    padding: '40px 24px 80px',
    animation: 'fadeIn 0.2s ease',
  },
  hero: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 24,
    marginBottom: 28,
  },
  heroLeft: {
    flex: 1,
  },
  title: {
    fontSize: 24,
    fontWeight: 700,
    color: '#f0f6fc',
    letterSpacing: '-0.4px',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 14,
    color: '#484f58',
    lineHeight: 1.65,
    maxWidth: 520,
    margin: 0,
  },
  statPill: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    background: '#111318',
    border: '1px solid #21262d',
    borderRadius: 12,
    padding: '12px 20px',
    flexShrink: 0,
  },
  statNum: {
    fontSize: 22,
    fontWeight: 700,
    color: '#f0f6fc',
    lineHeight: 1,
  },
  statLabel: {
    fontSize: 11,
    fontWeight: 500,
    color: '#484f58',
    marginTop: 4,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
  },
  toast: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    borderRadius: 8,
    padding: '10px 14px',
    fontSize: 13,
    fontWeight: 500,
    marginBottom: 20,
  },
  loadingRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginTop: 40,
  },
  spinner: {
    width: 16,
    height: 16,
    border: '2px solid #21262d',
    borderTop: '2px solid #7c3aed',
    borderRadius: '50%',
    animation: 'spin 0.7s linear infinite',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
    gap: 12,
  },
  footer: {
    marginTop: 40,
    fontSize: 12,
    color: '#30363d',
    borderTop: '1px solid #21262d',
    paddingTop: 20,
    fontWeight: 500,
  },
};
