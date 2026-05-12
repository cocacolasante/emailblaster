import { useEffect, useRef, useState } from 'react';

const QUALITY_BADGE = {
  rich:    { bg: '#e6f7ed', fg: '#1b5e20', label: 'Rich research' },
  partial: { bg: '#fff3cd', fg: '#7a5a00', label: 'Partial research' },
  low:     { bg: '#e5e7eb', fg: '#555',    label: 'Generic' },
};

/**
 * One reviewable sample email.
 *  - Local state for subject/body
 *  - Auto-saves on blur via onSave({composed_subject?, composed_body?})
 *  - Per-card approve/reject toggle via onApprove(true|false)
 */
export default function EmailPreviewCard({ sample, remainingSamples, onSave, onApprove }) {
  const [subject, setSubject] = useState(sample.composed_subject ?? '');
  const [body, setBody] = useState(sample.composed_body ?? '');
  const [showResearch, setShowResearch] = useState(false);
  const [saving, setSaving] = useState(false);

  // Sync local state when the parent provides an updated sample.
  const lastSampleId = useRef(sample.lead_id);
  useEffect(() => {
    if (lastSampleId.current !== sample.lead_id) {
      lastSampleId.current = sample.lead_id;
    }
    setSubject(sample.composed_subject ?? '');
    setBody(sample.composed_body ?? '');
  }, [sample.lead_id, sample.composed_subject, sample.composed_body]);

  const dirty =
    subject !== (sample.composed_subject ?? '') ||
    body !== (sample.composed_body ?? '');

  async function handleBlur() {
    if (!dirty) return;
    setSaving(true);
    try {
      const payload = {};
      if (subject !== (sample.composed_subject ?? '')) payload.composed_subject = subject;
      if (body !== (sample.composed_body ?? '')) payload.composed_body = body;
      await onSave(sample.lead_id, payload);
    } finally {
      setSaving(false);
    }
  }

  const quality = QUALITY_BADGE[sample.research_quality] || QUALITY_BADGE.low;

  return (
    <div data-testid="email-preview-card" data-lead-id={sample.lead_id} style={cardStyle}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <h3 style={{ margin: 0 }}>
            {sample.first_name} {sample.last_name}
          </h3>
          <p style={{ margin: '4px 0 0', color: '#666', fontSize: 13 }}>
            {sample.job_title ? `${sample.job_title} at ` : ''}{sample.company || sample.email}
          </p>
        </div>
        <span
          data-testid="quality-badge"
          data-quality={sample.research_quality}
          style={{
            padding: '4px 10px',
            borderRadius: 12,
            background: quality.bg,
            color: quality.fg,
            fontSize: 12,
            whiteSpace: 'nowrap',
          }}
        >
          {quality.label}
        </span>
      </header>

      <button
        type="button"
        onClick={() => setShowResearch((v) => !v)}
        style={{ ...linkBtn, marginTop: 8 }}
        aria-expanded={showResearch}
      >
        {showResearch ? '▾ Hide research' : '▸ Show research'}
      </button>
      {showResearch && (
        <div data-testid="research-panel" style={{ background: '#f9fafb', padding: 12, borderRadius: 6, marginTop: 8, fontSize: 13 }}>
          {sample.research_summary || 'No research findings.'}
        </div>
      )}

      <label style={labelStyle}>
        Subject
        <input
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          onBlur={handleBlur}
          style={inputStyle}
        />
      </label>

      <label style={labelStyle}>
        Body
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          onBlur={handleBlur}
          rows={8}
          style={{ ...inputStyle, fontFamily: 'inherit', resize: 'vertical' }}
        />
      </label>

      {dirty && remainingSamples > 0 && (
        <p data-testid="dirty-hint" style={{ fontSize: 12, color: '#7a5a00', margin: '4px 0' }}>
          Your edits will improve the remaining {remainingSamples} {remainingSamples === 1 ? 'email' : 'emails'}.
        </p>
      )}

      {saving && <p style={{ fontSize: 12, color: '#888' }}>Saving…</p>}

      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button
          type="button"
          onClick={() => onApprove(sample.lead_id, true)}
          style={sample.sample_approved === true ? activeBtn : btnStyle}
          data-testid="approve-button"
          aria-pressed={sample.sample_approved === true}
        >
          ✓ Approve
        </button>
        <button
          type="button"
          onClick={() => onApprove(sample.lead_id, false)}
          style={sample.sample_approved === false ? activeRejectBtn : btnStyle}
          data-testid="reject-button"
          aria-pressed={sample.sample_approved === false}
        >
          ✗ Reject
        </button>
      </div>
    </div>
  );
}

const cardStyle = {
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 16,
  background: 'white',
  marginBottom: 16,
};

const labelStyle = {
  display: 'block',
  marginTop: 12,
  fontSize: 13,
  fontWeight: 500,
  color: '#333',
};

const inputStyle = {
  display: 'block',
  width: '100%',
  padding: '8px 10px',
  border: '1px solid #ccc',
  borderRadius: 4,
  fontSize: 14,
  marginTop: 4,
  boxSizing: 'border-box',
};

const linkBtn = {
  background: 'none',
  border: 'none',
  color: '#2563eb',
  cursor: 'pointer',
  fontSize: 13,
  padding: 0,
};

const btnStyle = {
  padding: '6px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 13,
};

const activeBtn = {
  ...btnStyle,
  background: '#22c55e',
  color: 'white',
  border: '1px solid #22c55e',
};

const activeRejectBtn = {
  ...btnStyle,
  background: '#dc2626',
  color: 'white',
  border: '1px solid #dc2626',
};
