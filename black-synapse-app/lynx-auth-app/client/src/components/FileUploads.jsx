import { useState, useEffect, useCallback, useRef } from 'react';
import { getUploads, uploadFiles, deleteUpload } from '../api';

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function UploadIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
      <polyline points="17 8 12 3 7 8"/>
      <line x1="12" y1="3" x2="12" y2="15"/>
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
    </svg>
  );
}

export default function FileUploads({ onToast }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const base = import.meta.env.VITE_API_URL || '';

  const load = useCallback(async () => {
    try {
      const data = await getUploads();
      setItems(Array.isArray(data) ? data : []);
    } catch {
      onToast?.('error', 'Could not load your files');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [onToast]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleFiles(fileList) {
    const files = Array.from(fileList || []).filter(Boolean);
    if (files.length === 0) return;
    setUploading(true);
    try {
      await uploadFiles(files);
      onToast?.('success', files.length === 1 ? `"${files[0].name}" uploaded` : `${files.length} files uploaded`);
      await load();
    } catch (err) {
      const msg = err.response?.data?.error || err.message || 'Upload failed';
      onToast?.('error', msg);
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id, name) {
    if (!window.confirm(`Remove "${name}"?`)) return;
    try {
      await deleteUpload(id);
      onToast?.('success', 'File removed');
      setItems((prev) => prev.filter((x) => x.id !== id));
    } catch {
      onToast?.('error', 'Could not delete file');
    }
  }

  return (
    <section style={s.section}>
      <div style={s.head}>
        <div>
          <h2 style={s.title}>File uploads</h2>
          <p style={s.blurb}>PDFs are forwarded to the ingestion worker and embedded into Qdrant.</p>
        </div>
      </div>

      <div
        style={{
          ...s.dropzone,
          borderColor: dragOver ? '#7c3aed' : '#21262d',
          background: dragOver ? 'rgba(124, 58, 237, 0.06)' : '#0d0f14',
          color: dragOver ? '#7c3aed' : '#484f58',
        }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
        onClick={() => !uploading && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click(); } }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf"
          style={{ display: 'none' }}
          onChange={(e) => { handleFiles(e.target.files); e.target.value = ''; }}
        />
        <div style={{ ...s.dropIconWrap, color: dragOver ? '#7c3aed' : '#484f58' }}>
          {uploading ? <Spinner /> : <UploadIcon />}
        </div>
        <p style={s.dropTitle}>{uploading ? 'Uploading…' : 'Drop PDFs here or click to browse'}</p>
        <p style={s.dropHint}>Up to 10 files · 25 MB each</p>
      </div>

      {loading ? (
        <div style={s.loadingRow}>
          <Spinner small />
          <span style={{ color: '#484f58', fontSize: 13 }}>Loading files…</span>
        </div>
      ) : items.length === 0 ? (
        <p style={s.empty}>No files uploaded yet.</p>
      ) : (
        <ul style={s.list}>
          {items.map((f) => (
            <li key={f.id} style={s.row}>
              <div style={s.fileIcon}>
                <FileIcon />
              </div>
              <div style={s.rowMain}>
                <span style={s.fileName} title={f.originalFilename}>{f.originalFilename}</span>
                <span style={s.rowMeta}>
                  {formatSize(f.sizeBytes)}
                  {f.createdAt ? ` · ${new Date(f.createdAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}` : ''}
                </span>
              </div>
              <div style={s.rowActions}>
                <a href={`${base}/api/uploads/${f.id}/file`} download={f.originalFilename} style={s.downloadBtn}>
                  Download
                </a>
                <button type="button" style={s.removeBtn} onClick={() => handleDelete(f.id, f.originalFilename)}>
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Spinner({ small }) {
  const size = small ? 14 : 20;
  return (
    <div style={{
      width: size, height: size,
      border: `2px solid #21262d`,
      borderTop: `2px solid #7c3aed`,
      borderRadius: '50%',
      animation: 'spin 0.7s linear infinite',
    }} />
  );
}

const s = {
  section: {
    marginTop: 48,
    paddingTop: 32,
    borderTop: '1px solid #21262d',
  },
  head: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 20,
  },
  title: {
    fontSize: 18,
    fontWeight: 700,
    color: '#f0f6fc',
    marginBottom: 4,
    letterSpacing: '-0.2px',
  },
  blurb: {
    fontSize: 13,
    color: '#484f58',
    lineHeight: 1.5,
    margin: 0,
  },
  dropzone: {
    border: '1.5px dashed',
    borderRadius: 12,
    padding: '32px 20px',
    textAlign: 'center',
    cursor: 'pointer',
    transition: 'border-color 0.15s, background 0.15s, color 0.15s',
    userSelect: 'none',
  },
  dropIconWrap: {
    display: 'flex',
    justifyContent: 'center',
    marginBottom: 10,
    transition: 'color 0.15s',
  },
  dropTitle: {
    color: '#8b949e',
    fontSize: 14,
    fontWeight: 600,
    margin: '0 0 4px',
  },
  dropHint: {
    color: '#484f58',
    fontSize: 12,
    margin: 0,
  },
  loadingRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginTop: 24,
  },
  empty: {
    color: '#484f58',
    fontSize: 13,
    marginTop: 24,
    fontStyle: 'italic',
  },
  list: {
    listStyle: 'none',
    margin: '20px 0 0',
    padding: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    flexWrap: 'wrap',
    background: '#0d0f14',
    border: '1px solid #21262d',
    borderRadius: 10,
    padding: '12px 14px',
  },
  fileIcon: {
    color: '#484f58',
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
  },
  rowMain: {
    minWidth: 0,
    flex: 1,
  },
  fileName: {
    display: 'block',
    color: '#f0f6fc',
    fontSize: 13,
    fontWeight: 500,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  rowMeta: {
    display: 'block',
    fontSize: 11,
    color: '#484f58',
    marginTop: 2,
    fontWeight: 500,
  },
  rowActions: {
    display: 'flex',
    gap: 8,
    flexShrink: 0,
  },
  downloadBtn: {
    fontSize: 12,
    fontWeight: 500,
    color: '#8b949e',
    textDecoration: 'none',
    padding: '5px 10px',
    borderRadius: 6,
    border: '1px solid #21262d',
    background: 'transparent',
    transition: 'border-color 0.15s',
  },
  removeBtn: {
    fontSize: 12,
    fontWeight: 500,
    color: '#f85149',
    padding: '5px 10px',
    borderRadius: 6,
    border: '1px solid rgba(248, 81, 73, 0.25)',
    background: 'transparent',
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
};
