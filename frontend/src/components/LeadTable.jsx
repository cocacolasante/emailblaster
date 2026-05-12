import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listCampaignLeads } from '../api/campaigns.js';

const SEND_STATUSES = ['', 'pending', 'scheduled', 'sent', 'failed'];

function toCsv(rows) {
  const headers = ['email', 'first_name', 'last_name', 'company', 'job_title', 'send_status', 'created_at'];
  const lines = [headers.join(',')];
  for (const r of rows) {
    const cells = headers.map((h) => {
      const v = r[h] == null ? '' : String(r[h]);
      // RFC 4180 escaping
      return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
    });
    lines.push(cells.join(','));
  }
  return lines.join('\n');
}

export default function LeadTable({ campaignId, replyTrackingEnabled }) {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [search, setSearch] = useState('');
  const [sendStatus, setSendStatus] = useState('');

  const queryParams = {
    page,
    page_size: pageSize,
    ...(search ? { search } : {}),
    ...(sendStatus ? { send_status: sendStatus } : {}),
  };
  const { data, isLoading } = useQuery({
    queryKey: ['campaign-leads', campaignId, queryParams],
    queryFn: () => listCampaignLeads(campaignId, queryParams),
    keepPreviousData: true,
  });

  function exportCsv() {
    if (!data?.items) return;
    const csv = toCsv(data.items);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `campaign-${campaignId}-leads.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const total = data?.total ?? 0;
  const totalPages = data?.total_pages ?? 0;

  return (
    <div data-testid="lead-table">
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <input
          type="search"
          placeholder="Search by email or name"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          aria-label="Search leads"
          style={{ ...inputStyle, flex: 1 }}
        />
        <select
          aria-label="Filter by send status"
          value={sendStatus}
          onChange={(e) => { setSendStatus(e.target.value); setPage(1); }}
          style={inputStyle}
        >
          {SEND_STATUSES.map((s) => (
            <option key={s} value={s}>{s ? s : 'All statuses'}</option>
          ))}
        </select>
        <button type="button" onClick={exportCsv} style={btnStyle} disabled={!data?.items?.length}>
          Export CSV
        </button>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={thStyle}>Name</th>
            <th style={thStyle}>Email</th>
            <th style={thStyle}>Company</th>
            <th style={thStyle}>Send status</th>
            <th style={thStyle}>Replied</th>
            <th style={thStyle}>Created</th>
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            <tr><td colSpan={6} style={tdStyle}>Loading…</td></tr>
          ) : data?.items?.length === 0 ? (
            <tr><td colSpan={6} style={{ ...tdStyle, color: '#666', textAlign: 'center' }}>No leads.</td></tr>
          ) : (
            data?.items?.map((lead) => (
              <tr key={lead.id}>
                <td style={tdStyle}>
                  {lead.first_name || lead.last_name
                    ? `${lead.first_name || ''} ${lead.last_name || ''}`.trim()
                    : '—'}
                </td>
                <td style={tdStyle}>{lead.email}</td>
                <td style={tdStyle}>{lead.company || '—'}</td>
                <td style={tdStyle}>
                  <span data-testid="row-send-status">{lead.send_status}</span>
                </td>
                <td
                  style={tdStyle}
                  title={replyTrackingEnabled ? '' : 'Connect an inbox to track replies'}
                >
                  {replyTrackingEnabled ? '—' : '—'}
                </td>
                <td style={tdStyle}>
                  {lead.created_at ? new Date(lead.created_at).toLocaleDateString() : '—'}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      {totalPages > 1 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 }}>
          <span style={{ fontSize: 13, color: '#666' }}>
            Page {page} of {totalPages} · {total} leads total
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              style={btnStyle}
              aria-label="Previous page"
            >
              ← Prev
            </button>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              style={btnStyle}
              aria-label="Next page"
            >
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const inputStyle = {
  padding: '8px 10px',
  border: '1px solid #ccc',
  borderRadius: 4,
  fontSize: 14,
};

const btnStyle = {
  padding: '8px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 14,
};

const thStyle = {
  textAlign: 'left',
  padding: '10px 12px',
  borderBottom: '2px solid #e5e7eb',
  fontSize: 12,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
  color: '#666',
  background: '#f9fafb',
};

const tdStyle = {
  padding: '10px 12px',
  borderBottom: '1px solid #eee',
  fontSize: 14,
};
