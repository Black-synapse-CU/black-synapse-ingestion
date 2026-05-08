import { useState, useRef, useEffect, useCallback } from 'react';

const base = import.meta.env.VITE_API_URL || '';

const STEPS = [
  { id: 'center',     label: 'Look straight ahead',     hint: 'Keep your face centered' },
  { id: 'left',       label: 'Turn slightly left',       hint: 'Slowly rotate your head left' },
  { id: 'right',      label: 'Turn slightly right',      hint: 'Slowly rotate your head right' },
  { id: 'up',         label: 'Tilt slightly up',         hint: 'Raise your chin a little' },
  { id: 'down',       label: 'Tilt slightly down',       hint: 'Lower your chin a little' },
];

const COUNTDOWN_SEC = 3;

export default function FaceEnroll({ user }) {
  const [phase, setPhase] = useState('idle'); // idle | capturing | submitting | done | error
  const [stepIdx, setStepIdx] = useState(0);
  const [countdown, setCountdown] = useState(COUNTDOWN_SEC);
  const [captures, setCaptures] = useState([]); // [Blob]
  const [result, setResult] = useState(null); // { added, totalEmbeddings }
  const [errMsg, setErrMsg] = useState('');
  const [detectedFaces, setDetectedFaces] = useState([]); // who the robot sees right now
  const pollRef = useRef(null);

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const timerRef = useRef(null);

  const stopCamera = useCallback(() => {
    clearInterval(timerRef.current);
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
  }, []);

  useEffect(() => {
    async function pollFaces() {
      try {
        const res = await fetch(`${base}/api/perception/faces`, { credentials: 'include' });
        if (res.ok) {
          const data = await res.json();
          setDetectedFaces(data.faces || []);
        }
      } catch { /* perception offline, just show nothing */ }
    }
    pollFaces();
    pollRef.current = setInterval(pollFaces, 3000);
    return () => {
      clearInterval(pollRef.current);
      stopCamera();
    };
  }, [stopCamera]);

  async function startEnrollment() {
    setErrMsg('');
    setCaptures([]);
    setStepIdx(0);
    setCountdown(COUNTDOWN_SEC);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 } });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setPhase('capturing');
      startCountdown(0, []);
    } catch {
      setErrMsg('Camera access denied. Please allow camera permissions.');
      setPhase('error');
    }
  }

  function startCountdown(currentStep, currentCaptures) {
    let count = COUNTDOWN_SEC;
    setCountdown(count);
    timerRef.current = setInterval(() => {
      count--;
      setCountdown(count);
      if (count <= 0) {
        clearInterval(timerRef.current);
        captureFrame(currentStep, currentCaptures);
      }
    }, 1000);
  }

  function captureFrame(currentStep, currentCaptures) {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);

    canvas.toBlob(blob => {
      if (!blob) return;
      const newCaptures = [...currentCaptures, blob];
      setCaptures(newCaptures);

      const nextStep = currentStep + 1;
      if (nextStep < STEPS.length) {
        setStepIdx(nextStep);
        startCountdown(nextStep, newCaptures);
      } else {
        setPhase('submitting');
        stopCamera();
        submitCaptures(newCaptures);
      }
    }, 'image/jpeg', 0.92);
  }

  async function submitCaptures(blobs) {
    let added = 0;
    let lastTotal = null;

    await Promise.allSettled(blobs.map(async (blob, i) => {
      const form = new FormData();
      form.append('image', blob, `pose_${i}.jpg`);
      const res = await fetch(`${base}/api/perception/enroll`, {
        method: 'POST',
        credentials: 'include',
        body: form,
      });
      const data = await res.json();
      if (data.ok) {
        added++;
        if (data.total_embeddings != null) lastTotal = data.total_embeddings;
      }
    }));

    if (added === 0) {
      setErrMsg('Perception service is offline or no face was detected. Start perception and try again.');
      setPhase('error');
    } else {
      setResult({ added, totalEmbeddings: lastTotal });
      setPhase('done');
    }
  }

  function reset() {
    stopCamera();
    setPhase('idle');
    setStepIdx(0);
    setCaptures([]);
    setResult(null);
    setErrMsg('');
  }

  const progress = phase === 'capturing' ? stepIdx / STEPS.length
    : phase === 'submitting' || phase === 'done' ? 1 : 0;

  return (
    <section style={s.section}>
      <div style={s.header}>
        <div>
          <h2 style={s.title}>Face Enrollment</h2>
          <p style={s.subtitle}>
            Capture multiple angles so Desky can recognize you in different conditions.
          </p>
        </div>
        <div style={s.userPill}>
          <PersonIcon />
          {user?.username}
        </div>
      </div>

      {/* Who the robot sees right now */}
      {detectedFaces.length > 0 && (
        <div style={s.detectedBar}>
          <EyeIcon />
          <span style={s.detectedLabel}>Desky sees:</span>
          {detectedFaces.map((f, i) => (
            <span key={i} style={s.facePill}>
              {f.name || 'Unknown'}
              {f.confidence != null && <span style={s.conf}>{Math.round(f.confidence * 100)}%</span>}
            </span>
          ))}
        </div>
      )}

      {/* Camera viewport */}
      <div style={s.viewport}>
        <video ref={videoRef} style={{ ...s.video, display: phase === 'capturing' ? 'block' : 'none' }} muted playsInline />
        <canvas ref={canvasRef} style={{ display: 'none' }} />

        {/* Oval guide overlay */}
        {phase === 'capturing' && (
          <div style={s.overlay}>
            <svg style={s.ovalSvg} viewBox="0 0 300 380" fill="none">
              <defs>
                <mask id="cutout">
                  <rect width="300" height="380" fill="white" />
                  <ellipse cx="150" cy="185" rx="105" ry="135" fill="black" />
                </mask>
              </defs>
              <rect width="300" height="380" fill="rgba(10,11,15,0.55)" mask="url(#cutout)" />
              <ellipse cx="150" cy="185" rx="105" ry="135"
                stroke={countdown === 0 ? '#3fb950' : '#7c3aed'}
                strokeWidth="2.5"
                strokeDasharray={countdown === 0 ? 'none' : '8 4'}
              />
            </svg>

            {/* Countdown ring */}
            <div style={s.countdownWrap}>
              <svg width="64" height="64" viewBox="0 0 64 64">
                <circle cx="32" cy="32" r="28" fill="none" stroke="#21262d" strokeWidth="4" />
                <circle cx="32" cy="32" r="28" fill="none" stroke="#7c3aed" strokeWidth="4"
                  strokeDasharray={`${(1 - countdown / COUNTDOWN_SEC) * 175.9} 175.9`}
                  strokeLinecap="round"
                  transform="rotate(-90 32 32)"
                  style={{ transition: 'stroke-dasharray 0.9s linear' }}
                />
                <text x="32" y="38" textAnchor="middle" fill="#f0f6fc" fontSize="22" fontWeight="700">{countdown}</text>
              </svg>
            </div>
          </div>
        )}

        {/* Idle / done / submitting placeholders */}
        {phase === 'idle' && (
          <div style={s.placeholder}>
            <CameraIcon />
            <span style={s.placeholderText}>Camera will activate when you start</span>
          </div>
        )}
        {phase === 'submitting' && (
          <div style={s.placeholder}>
            <Spinner large />
            <span style={s.placeholderText}>Saving your face data…</span>
          </div>
        )}
        {phase === 'done' && (
          <div style={s.placeholder}>
            <CheckCircle />
            <span style={{ ...s.placeholderText, color: '#3fb950' }}>Enrollment complete</span>
            {result?.totalEmbeddings != null && (
              <span style={s.embedBadge}>{result.totalEmbeddings} total samples stored</span>
            )}
          </div>
        )}
        {phase === 'error' && (
          <div style={s.placeholder}>
            <XCircle />
            <span style={{ ...s.placeholderText, color: '#f85149' }}>{errMsg}</span>
          </div>
        )}
      </div>

      {/* Step indicators */}
      <div style={s.steps}>
        {STEPS.map((step, i) => {
          const done = i < stepIdx || phase === 'done' || phase === 'submitting';
          const active = i === stepIdx && phase === 'capturing';
          return (
            <div key={step.id} style={s.stepRow}>
              <div style={{ ...s.stepDot, background: done ? '#3fb950' : active ? '#7c3aed' : '#21262d', boxShadow: active ? '0 0 0 3px rgba(124,58,237,0.2)' : 'none' }}>
                {done ? <MiniCheck /> : <span style={{ fontSize: 11, color: active ? '#fff' : '#484f58', fontWeight: 600 }}>{i + 1}</span>}
              </div>
              <div>
                <div style={{ ...s.stepLabel, color: done ? '#3fb950' : active ? '#f0f6fc' : '#484f58' }}>{step.label}</div>
                {active && <div style={s.stepHint}>{step.hint}</div>}
              </div>
            </div>
          );
        })}
      </div>

      {/* Progress bar */}
      {(phase === 'capturing' || phase === 'submitting') && (
        <div style={s.progressTrack}>
          <div style={{ ...s.progressFill, width: `${progress * 100}%` }} />
        </div>
      )}

      {/* CTA */}
      <div style={s.actions}>
        {(phase === 'idle' || phase === 'error') && (
          <button style={s.btn} onClick={startEnrollment}>
            <CameraIcon small /> Start enrollment
          </button>
        )}
        {phase === 'done' && (
          <button style={s.btnGhost} onClick={reset}>Enroll again</button>
        )}
      </div>
    </section>
  );
}

function EyeIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>;
}
function PersonIcon() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}><circle cx="12" cy="8" r="5"/><path d="M3 21a9 9 0 0 1 18 0"/></svg>;
}
function CameraIcon({ small }) {
  const size = small ? 14 : 32;
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={small ? 'currentColor' : '#30363d'} strokeWidth={small ? 2 : 1.5} strokeLinecap="round" strokeLinejoin="round" style={small ? { marginRight: 7 } : {}}><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>;
}
function CheckCircle() {
  return <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#3fb950" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="9 12 11 14 15 10"/></svg>;
}
function XCircle() {
  return <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#f85149" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>;
}
function MiniCheck() {
  return <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>;
}
function Spinner({ large }) {
  const size = large ? 36 : 13;
  return <span style={{ width: size, height: size, border: `${large ? 3 : 2}px solid rgba(255,255,255,0.1)`, borderTop: `${large ? 3 : 2}px solid #7c3aed`, borderRadius: '50%', display: 'inline-block', animation: 'spin 0.7s linear infinite', marginBottom: large ? 12 : 0, marginRight: large ? 0 : 8 }} />;
}

const s = {
  section: { marginTop: 40, borderTop: '1px solid #21262d', paddingTop: 32 },
  header: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 24 },
  title: { fontSize: 18, fontWeight: 600, color: '#f0f6fc', marginBottom: 6, letterSpacing: '-0.3px' },
  subtitle: { fontSize: 13, color: '#484f58', lineHeight: 1.6, margin: 0, maxWidth: 440 },
  userPill: { display: 'flex', alignItems: 'center', background: '#111318', border: '1px solid #21262d', borderRadius: 20, padding: '6px 14px', fontSize: 13, fontWeight: 500, color: '#f0f6fc', flexShrink: 0 },
  viewport: { position: 'relative', width: '100%', aspectRatio: '4/3', maxHeight: 380, background: '#0d1017', border: '1px solid #21262d', borderRadius: 12, overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  video: { width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)' },
  overlay: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' },
  ovalSvg: { position: 'absolute', inset: 0, width: '100%', height: '100%' },
  countdownWrap: { position: 'absolute', bottom: 20, right: 20 },
  placeholder: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 },
  placeholderText: { fontSize: 13, color: '#484f58', marginTop: 4 },
  embedBadge: { fontSize: 11, color: '#7c3aed', background: 'rgba(124,58,237,0.1)', border: '1px solid rgba(124,58,237,0.2)', borderRadius: 10, padding: '3px 10px', fontWeight: 500, marginTop: 4 },
  steps: { display: 'flex', flexDirection: 'column', gap: 12, marginTop: 20 },
  stepRow: { display: 'flex', alignItems: 'flex-start', gap: 12 },
  stepDot: { width: 26, height: 26, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, transition: 'background 0.2s, box-shadow 0.2s' },
  stepLabel: { fontSize: 13, fontWeight: 500, transition: 'color 0.2s', lineHeight: '26px' },
  stepHint: { fontSize: 11, color: '#484f58', marginTop: 2 },
  progressTrack: { height: 3, background: '#21262d', borderRadius: 2, marginTop: 20, overflow: 'hidden' },
  progressFill: { height: '100%', background: '#7c3aed', borderRadius: 2, transition: 'width 0.4s ease' },
  actions: { display: 'flex', justifyContent: 'flex-end', marginTop: 20 },
  btn: { display: 'flex', alignItems: 'center', background: '#7c3aed', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 20px', fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  btnGhost: { background: 'transparent', color: '#484f58', border: '1px solid #21262d', borderRadius: 8, padding: '10px 16px', fontSize: 13, cursor: 'pointer' },
  detectedBar: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, background: '#111318', border: '1px solid #21262d', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#8b949e', marginBottom: 16 },
  detectedLabel: { fontWeight: 500, color: '#484f58', marginRight: 4 },
  facePill: { display: 'inline-flex', alignItems: 'center', gap: 5, background: 'rgba(124,58,237,0.1)', border: '1px solid rgba(124,58,237,0.2)', borderRadius: 10, padding: '2px 10px', fontSize: 12, fontWeight: 500, color: '#c084fc' },
  conf: { fontSize: 10, color: '#7c3aed', fontWeight: 400 },
};
