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
      <div className="flex gap-3 mb-4 items-center">
        <input
          type="search"
          placeholder="Search by email or name"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          aria-label="Search leads"
          className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
        />
        <select
          aria-label="Filter by send status"
          value={sendStatus}
          onChange={(e) => { setSendStatus(e.target.value); setPage(1); }}
          className="px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
        >
          {SEND_STATUSES.map((s) => (
            <option key={s} value={s}>{s ? s : 'All statuses'}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={exportCsv}
          disabled={!data?.items?.length}
          className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Export CSV
        </button>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Name</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Email</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Company</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Send status</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Replied</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider bg-slate-50 border-b border-slate-200">Created</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} className="px-4 py-3 text-slate-700 border-b border-slate-100 text-center">Loading…</td></tr>
            ) : data?.items?.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-3 text-slate-500 border-b border-slate-100 text-center">No leads.</td></tr>
            ) : (
              data?.items?.map((lead) => (
                <tr key={lead.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 text-slate-700 border-b border-slate-100">
                    {lead.first_name || lead.last_name
                      ? `${lead.first_name || ''} ${lead.last_name || ''}`.trim()
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-700 border-b border-slate-100">{lead.email}</td>
                  <td className="px-4 py-3 text-slate-700 border-b border-slate-100">{lead.company || '—'}</td>
                  <td className="px-4 py-3 text-slate-700 border-b border-slate-100">
                    <span data-testid="row-send-status">{lead.send_status}</span>
                  </td>
                  <td
                    className="px-4 py-3 text-slate-700 border-b border-slate-100"
                    title={replyTrackingEnabled ? '' : 'Connect an inbox to track replies'}
                  >
                    {replyTrackingEnabled ? '—' : '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-700 border-b border-slate-100">
                    {lead.created_at ? new Date(lead.created_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex justify-between items-center mt-4">
          <span className="text-sm text-slate-500">
            Page {page} of {totalPages} · {total} leads total
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Previous page"
            >
              ← Prev
            </button>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
