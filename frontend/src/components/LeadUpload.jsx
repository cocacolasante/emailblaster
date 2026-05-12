import { useState } from 'react';
import { uploadLeadsPreview, confirmLeadsUpload } from '../api/campaigns.js';

const LEAD_FIELDS = [
  { value: '', label: '— ignore —' },
  { value: 'email', label: 'email *' },
  { value: 'first_name', label: 'first_name' },
  { value: 'last_name', label: 'last_name' },
  { value: 'company', label: 'company' },
  { value: 'job_title', label: 'job_title' },
  { value: 'linkedin_url', label: 'linkedin_url' },
  { value: 'phone', label: 'phone' },
];

/**
 * Two-stage uploader.  After file selection, hits /upload to preview rows +
 * suggested mapping, lets the user adjust per-column mapping, then calls
 * /confirm-upload.
 */
export default function LeadUpload({ campaignId, onComplete }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [mapping, setMapping] = useState({});
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState(null);

  async function handleFileSelect(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setError(null);
    setLoading(true);
    try {
      const data = await uploadLeadsPreview(campaignId, f);
      setPreview(data);
      setMapping(data.suggested_mapping || {});
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Upload failed');
      setFile(null);
    } finally {
      setLoading(false);
    }
  }

  function updateMapping(col, field) {
    setMapping((m) => {
      const next = { ...m };
      if (field) next[col] = field;
      else delete next[col];
      return next;
    });
  }

  async function handleConfirm() {
    setError(null);
    const hasEmail = Object.values(mapping).includes('email');
    if (!hasEmail) {
      setError('You must map a CSV column to "email".');
      return;
    }
    setConfirming(true);
    try {
      const result = await confirmLeadsUpload(campaignId, file, mapping);
      onComplete(result);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Confirm failed');
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div data-testid="lead-upload">
      <h2 style={{ marginTop: 0 }}>Upload leads</h2>

      {!preview && (
        <div>
          <p style={{ color: '#555' }}>
            Upload a CSV of your leads. We'll suggest column mappings on the next step.
          </p>
          <label style={fileButtonStyle}>
            {loading ? 'Parsing…' : 'Choose CSV'}
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={handleFileSelect}
              disabled={loading}
              style={{ display: 'none' }}
              data-testid="file-input"
            />
          </label>
        </div>
      )}

      {preview && (
        <div>
          <p>
            <strong>{preview.total_rows} rows</strong> detected in <code>{file?.name}</code>.
            Map each CSV column to a lead field below — <em>email is required</em>.
          </p>

          <table data-testid="mapping-table" style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>CSV column</th>
                <th style={thStyle}>Map to</th>
                <th style={thStyle}>Sample value</th>
              </tr>
            </thead>
            <tbody>
              {preview.columns.map((col) => (
                <tr key={col}>
                  <td style={tdStyle}><code>{col}</code></td>
                  <td style={tdStyle}>
                    <select
                      aria-label={`Map ${col}`}
                      value={mapping[col] || ''}
                      onChange={(e) => updateMapping(col, e.target.value)}
                      style={selectStyle}
                    >
                      {LEAD_FIELDS.map((f) => (
                        <option key={f.value} value={f.value}>
                          {f.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td style={{ ...tdStyle, color: '#666', fontSize: 13 }}>
                    {preview.preview_rows[0]?.[col] || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {error && (
            <div data-testid="upload-error" style={errorStyle}>{error}</div>
          )}

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
            <button
              type="button"
              onClick={() => { setPreview(null); setFile(null); setError(null); }}
              style={btnStyle}
            >
              Choose different file
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={confirming}
              style={primaryBtn}
            >
              {confirming ? 'Importing…' : `Import ${preview.total_rows} leads`}
            </button>
          </div>
        </div>
      )}

      {!preview && error && (
        <div data-testid="upload-error" style={errorStyle}>{error}</div>
      )}
    </div>
  );
}

const fileButtonStyle = {
  display: 'inline-block',
  padding: '12px 24px',
  border: '2px dashed #2563eb',
  borderRadius: 6,
  color: '#2563eb',
  cursor: 'pointer',
  fontSize: 14,
};

const tableStyle = {
  width: '100%',
  borderCollapse: 'collapse',
  marginTop: 12,
};

const thStyle = {
  textAlign: 'left',
  padding: '8px 10px',
  borderBottom: '1px solid #ddd',
  fontSize: 13,
  background: '#f9fafb',
};

const tdStyle = {
  padding: '8px 10px',
  borderBottom: '1px solid #eee',
  fontSize: 14,
};

const selectStyle = {
  padding: '6px 8px',
  border: '1px solid #ccc',
  borderRadius: 4,
  fontSize: 13,
  width: '100%',
  boxSizing: 'border-box',
};

const btnStyle = {
  padding: '8px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 14,
};

const primaryBtn = {
  ...btnStyle,
  background: '#2563eb',
  color: 'white',
  border: '1px solid #2563eb',
};

const errorStyle = {
  color: '#b71c1c',
  background: '#fdecea',
  padding: 10,
  borderRadius: 4,
  marginTop: 12,
};
