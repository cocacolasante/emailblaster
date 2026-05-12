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
      <h2 className="mt-0 text-xl font-bold text-slate-900 mb-4">Upload leads</h2>

      {!preview && (
        <div>
          <p className="text-slate-600 text-sm mb-6">
            Upload a CSV of your leads. We'll suggest column mappings on the next step.
          </p>
          <label className="inline-flex flex-col items-center justify-center w-full border-2 border-dashed border-slate-300 rounded-xl p-10 text-center hover:border-blue-400 cursor-pointer transition-colors">
            <svg className="w-10 h-10 text-slate-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
            <span className="text-sm font-medium text-blue-600">
              {loading ? 'Parsing…' : 'Choose CSV'}
            </span>
            <span className="text-xs text-slate-500 mt-1">or drag and drop a file</span>
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={handleFileSelect}
              disabled={loading}
              className="hidden"
              data-testid="file-input"
            />
          </label>
        </div>
      )}

      {preview && (
        <div>
          <p className="text-sm text-slate-700 mb-4">
            <strong className="font-semibold">{preview.total_rows} rows</strong> detected in <code className="bg-slate-100 px-1 py-0.5 rounded text-xs">{file?.name}</code>.
            Map each CSV column to a lead field below — <em>email is required</em>.
          </p>

          <div className="overflow-hidden rounded-xl border border-slate-200 mb-4">
            <table data-testid="mapping-table" className="w-full text-sm">
              <thead>
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">CSV column</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Map to</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Sample value</th>
                </tr>
              </thead>
              <tbody>
                {preview.columns.map((col) => (
                  <tr key={col} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-slate-700 border-b border-slate-100">
                      <code className="bg-slate-100 px-1 py-0.5 rounded text-xs">{col}</code>
                    </td>
                    <td className="px-4 py-3 text-slate-700 border-b border-slate-100">
                      <select
                        aria-label={`Map ${col}`}
                        value={mapping[col] || ''}
                        onChange={(e) => updateMapping(col, e.target.value)}
                        className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
                      >
                        {LEAD_FIELDS.map((f) => (
                          <option key={f.value} value={f.value}>
                            {f.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3 text-slate-500 border-b border-slate-100 text-xs">
                      {preview.preview_rows[0]?.[col] || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {error && (
            <div data-testid="upload-error" className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 mb-4">{error}</div>
          )}

          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={() => { setPreview(null); setFile(null); setError(null); }}
              className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors"
            >
              Choose different file
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={confirming}
              className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {confirming ? 'Importing…' : `Import ${preview.total_rows} leads`}
            </button>
          </div>
        </div>
      )}

      {!preview && error && (
        <div data-testid="upload-error" className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 mt-4">{error}</div>
      )}
    </div>
  );
}
