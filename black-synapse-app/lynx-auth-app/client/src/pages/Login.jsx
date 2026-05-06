import { useState } from 'react';
import { login } from '../api';

function LynxLogo() {
  return (
    <svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="36" height="36" rx="10" fill="#7c3aed" />
      <circle cx="10" cy="18" r="3" fill="white" opacity="0.9" />
      <circle cx="26" cy="10" r="3" fill="white" opacity="0.9" />
      <circle cx="26" cy="26" r="3" fill="white" opacity="0.9" />
      <line x1="13" y1="18" x2="23" y2="11" stroke="white" strokeWidth="1.5" strokeOpacity="0.5" />
      <line x1="13" y1="18" x2="23" y2="25" stroke="white" strokeWidth="1.5" strokeOpacity="0.5" />
      <line x1="26" y1="13" x2="26" y2="23" stroke="white" strokeWidth="1.5" strokeOpacity="0.5" />
    </svg>
  );
}

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const user = await login(username, password);
      onLogin(user);
    } catch (err) {
      setError(err.response?.data?.error || 'Invalid credentials');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.brand}>
          <LynxLogo />
          <div>
            <div style={s.brandName}>Lynx</div>
            <div style={s.brandSub}>Black Synapse · Connected Services</div>
          </div>
        </div>

        <form onSubmit={handleSubmit} style={s.form}>
          <div style={s.field}>
            <label style={s.label}>Username</label>
            <input
              style={s.input}
              type="text"
              autoComplete="username"
              placeholder="your username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </div>

          <div style={s.field}>
            <label style={s.label}>Password</label>
            <input
              style={s.input}
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && (
            <div style={s.errorBox}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              {error}
            </div>
          )}

          <button style={{ ...s.button, opacity: loading ? 0.7 : 1 }} type="submit" disabled={loading}>
            {loading ? (
              <span style={s.btnInner}>
                <span style={s.btnSpinner} />
                Signing in…
              </span>
            ) : (
              <span style={s.btnInner}>
                Sign in
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" />
                </svg>
              </span>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}

const s = {
  page: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '100vh',
    background: '#0a0b0f',
    padding: '24px',
  },
  card: {
    background: '#111318',
    border: '1px solid #21262d',
    borderRadius: 16,
    padding: '36px 32px',
    width: '100%',
    maxWidth: 380,
    animation: 'fadeUp 0.25s ease',
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
    marginBottom: 36,
    paddingBottom: 28,
    borderBottom: '1px solid #21262d',
  },
  brandName: {
    fontSize: 20,
    fontWeight: 700,
    color: '#f0f6fc',
    letterSpacing: '-0.3px',
    lineHeight: 1.2,
  },
  brandSub: {
    fontSize: 12,
    color: '#484f58',
    marginTop: 2,
    letterSpacing: '0.01em',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  label: {
    fontSize: 13,
    fontWeight: 500,
    color: '#8b949e',
  },
  input: {
    background: '#0d0f14',
    border: '1px solid #21262d',
    borderRadius: 8,
    padding: '10px 12px',
    color: '#f0f6fc',
    fontSize: 14,
    outline: 'none',
    width: '100%',
    transition: 'border-color 0.15s',
  },
  errorBox: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    color: '#f85149',
    fontSize: 13,
    background: 'rgba(248, 81, 73, 0.08)',
    border: '1px solid rgba(248, 81, 73, 0.2)',
    borderRadius: 8,
    padding: '10px 12px',
  },
  button: {
    marginTop: 4,
    background: '#7c3aed',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: '11px 16px',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    width: '100%',
    transition: 'background 0.15s, opacity 0.15s',
  },
  btnInner: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  btnSpinner: {
    width: 14,
    height: 14,
    border: '2px solid rgba(255,255,255,0.3)',
    borderTop: '2px solid #fff',
    borderRadius: '50%',
    animation: 'spin 0.7s linear infinite',
    display: 'inline-block',
  },
};
