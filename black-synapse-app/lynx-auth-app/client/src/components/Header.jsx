import { logout } from '../api';

function LynxMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="36" height="36" rx="9" fill="#7c3aed" />
      <circle cx="10" cy="18" r="3" fill="white" opacity="0.9" />
      <circle cx="26" cy="10" r="3" fill="white" opacity="0.9" />
      <circle cx="26" cy="26" r="3" fill="white" opacity="0.9" />
      <line x1="13" y1="18" x2="23" y2="11" stroke="white" strokeWidth="1.5" strokeOpacity="0.5" />
      <line x1="13" y1="18" x2="23" y2="25" stroke="white" strokeWidth="1.5" strokeOpacity="0.5" />
      <line x1="26" y1="13" x2="26" y2="23" stroke="white" strokeWidth="1.5" strokeOpacity="0.5" />
    </svg>
  );
}

export default function Header({ user, onLogout }) {
  async function handleLogout() {
    await logout();
    onLogout();
  }

  return (
    <header style={s.header}>
      <div style={s.brand}>
        <LynxMark />
        <span style={s.name}>Lynx</span>
        <span style={s.divider} />
        <span style={s.section}>Connected Services</span>
      </div>
      <div style={s.right}>
        <div style={s.userPill}>
          <div style={s.avatar}>{user.username.charAt(0).toUpperCase()}</div>
          <span style={s.username}>{user.username}</span>
        </div>
        <button style={s.logoutBtn} onClick={handleLogout}>
          Sign out
        </button>
      </div>
    </header>
  );
}

const s = {
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 28px',
    height: 58,
    background: '#111318',
    borderBottom: '1px solid #21262d',
    position: 'sticky',
    top: 0,
    zIndex: 10,
  },
  brand: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  name: {
    fontWeight: 700,
    fontSize: 16,
    color: '#f0f6fc',
    letterSpacing: '-0.2px',
  },
  divider: {
    width: 1,
    height: 16,
    background: '#21262d',
    display: 'inline-block',
  },
  section: {
    color: '#484f58',
    fontSize: 13,
    fontWeight: 500,
  },
  right: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  userPill: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    background: '#161b22',
    border: '1px solid #21262d',
    borderRadius: 20,
    padding: '4px 12px 4px 4px',
  },
  avatar: {
    width: 24,
    height: 24,
    borderRadius: '50%',
    background: '#7c3aed',
    color: '#fff',
    fontSize: 11,
    fontWeight: 700,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  username: {
    fontSize: 13,
    fontWeight: 500,
    color: '#8b949e',
  },
  logoutBtn: {
    background: 'transparent',
    border: '1px solid #21262d',
    borderRadius: 7,
    color: '#8b949e',
    padding: '5px 12px',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'border-color 0.15s, color 0.15s',
  },
};
