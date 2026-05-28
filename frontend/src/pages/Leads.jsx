import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getLeadDetail,
  listAllLeads,
  listCampaigns,
  updateLeadEmail,
} from '../api/campaigns.js';
import { useToast } from '../components/Toast.jsx';

const STATUS_CLASSES = {
  pending: 'bg-slate-100 text-slate-700',
  scheduled: 'bg-amber-100 text-amber-700',
  sent: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
};

function StatusPill({ value }) {
  if (!value) return <span className="text-slate-400">—</span>;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${STATUS_CLASSES[value] || 'bg-slate-100 text-slate-700'}`}>
      {value}
    </span>
  );
}

export default function Leads() {
  const [page, setPage] = useState(1);
  const [campaignId, setCampaignId] = useState('');
  const [search, setSearch] = useState('');
  const [hasNotes, setHasNotes] = useState(false);
  const [selected, setSelected] = useState(null);

  const { data: campaigns = [] } = useQuery({
    queryKey: ['campaigns'],
    queryFn: listCampaigns,
  });

  const params = { page, page_size: 50 };
  if (campaignId) params.campaign_id = campaignId;
  if (search.trim()) params.search = search.trim();
  if (hasNotes) params.has_notes = true;

  const { data: leadsPage, isLoading } = useQuery({
    queryKey: ['all-leads', params],
    queryFn: () => listAllLeads(params),
    keepPreviousData: true,
  });

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-900 mb-1">Leads</h1>
      <p className="text-sm text-slate-500 mb-5">
        Every lead across every campaign. Click a row to view the composed email
        and add CRM notes.
      </p>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 mb-4 flex flex-wrap items-center gap-3">
        <input
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          placeholder="Search email, name, company…"
          aria-label="Search leads"
          className="flex-1 min-w-[200px] px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <select
          value={campaignId}
          onChange={(e) => { setCampaignId(e.target.value); setPage(1); }}
          aria-label="Filter by campaign"
          className="px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
        >
          <option value="">All campaigns</option>
          {campaigns.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={hasNotes}
            onChange={(e) => { setHasNotes(e.target.checked); setPage(1); }}
          />
          Has notes
        </label>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <table className="w-full text-sm" data-testid="leads-table">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-500 uppercase">
              <th className="px-4 py-3 text-left">Name</th>
              <th className="px-4 py-3 text-left">Email</th>
              <th className="px-4 py-3 text-left">Company</th>
              <th className="px-4 py-3 text-left">Campaign</th>
              <th className="px-4 py-3 text-left">Send</th>
              <th className="px-4 py-3 text-left">Notes</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-500">Loading…</td></tr>
            ) : (leadsPage?.items?.length ?? 0) === 0 ? (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-500">No leads.</td></tr>
            ) : (
              leadsPage.items.map((l) => (
                <tr
                  key={l.id}
                  onClick={() => setSelected(l)}
                  data-testid={`lead-row-${l.id}`}
                  className="border-b border-slate-100 hover:bg-slate-50 cursor-pointer"
                >
                  <td className="px-4 py-3 text-slate-700">
                    {l.first_name || l.last_name
                      ? `${l.first_name || ''} ${l.last_name || ''}`.trim()
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-slate-700">{l.email}</td>
                  <td className="px-4 py-3 text-slate-700">{l.company || '—'}</td>
                  <td className="px-4 py-3 text-slate-700">{l.campaign_name || '—'}</td>
                  <td className="px-4 py-3"><StatusPill value={l.send_status} /></td>
                  <td className="px-4 py-3 text-slate-600 max-w-[260px] truncate" title={l.notes || ''}>
                    {l.has_notes ? l.notes : <span className="text-slate-400">—</span>}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {leadsPage && leadsPage.total_pages > 1 && (
        <div className="flex justify-between items-center mt-4 text-sm text-slate-600">
          <div>
            Page {leadsPage.page} of {leadsPage.total_pages} · {leadsPage.total} total
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="px-3 py-1.5 border border-slate-300 rounded-lg disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page >= (leadsPage?.total_pages ?? 1)}
              onClick={() => setPage((p) => p + 1)}
              className="px-3 py-1.5 border border-slate-300 rounded-lg disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {selected && (
        <LeadCrmModal lead={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}


function LeadCrmModal({ lead, onClose }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { data: detail } = useQuery({
    queryKey: ['lead-detail', lead.campaign_id, lead.id],
    queryFn: () => getLeadDetail(lead.campaign_id, lead.id),
  });
  const [notes, setNotes] = useState('');
  const [hydrated, setHydrated] = useState(false);
  if (detail && !hydrated) {
    setNotes(detail.notes || '');
    setHydrated(true);
  }
  const dirty = hydrated && notes !== (detail?.notes || '');

  const saveMutation = useMutation({
    mutationFn: () => updateLeadEmail(lead.campaign_id, lead.id, { notes }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['lead-detail', lead.campaign_id, lead.id] });
      queryClient.invalidateQueries({ queryKey: ['all-leads'] });
      toast.success('Notes saved');
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed to save notes'),
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl p-6 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        data-testid="lead-crm-modal"
      >
        <div className="flex justify-between items-start mb-4">
          <div>
            <h2 className="m-0 text-lg font-semibold text-slate-900">
              {lead.first_name || lead.last_name
                ? `${lead.first_name || ''} ${lead.last_name || ''}`.trim()
                : lead.email}
            </h2>
            <div className="text-sm text-slate-500 mt-0.5">
              {lead.email}{lead.company ? ` · ${lead.company}` : ''}
              {lead.campaign_name && <> · <span className="text-slate-400">campaign</span> {lead.campaign_name}</>}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 text-xl bg-transparent border-none cursor-pointer p-1"
            aria-label="Close"
          >×</button>
        </div>

        {/* Composed email preview */}
        {detail?.composed_subject || detail?.composed_body ? (
          <div className="border border-slate-200 rounded-lg overflow-hidden mb-4">
            <div className="bg-slate-50 border-b border-slate-200 px-4 py-2.5">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide mr-2">Subject</span>
              <span className="text-sm text-slate-900 font-medium">{detail.composed_subject || '—'}</span>
            </div>
            <div className="p-4">
              <pre className="text-sm text-slate-800 whitespace-pre-wrap font-sans leading-relaxed m-0">
                {detail.composed_body || '—'}
              </pre>
            </div>
          </div>
        ) : (
          <div className="text-sm text-slate-500 italic py-2 mb-4">No email composed yet.</div>
        )}

        {/* Notes */}
        <div className="space-y-2">
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Notes
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={6}
            placeholder="Met at Lattice summit; warm intro from Sara…"
            data-testid="lead-notes-textarea"
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 font-[inherit]"
          />
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => saveMutation.mutate()}
              disabled={!dirty || saveMutation.isPending}
              data-testid="save-notes-btn"
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {saveMutation.isPending ? 'Saving…' : 'Save notes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
