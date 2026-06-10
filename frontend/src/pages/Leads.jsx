import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getLeadById,
  ignoreLead,
  listAllLeads,
  listCampaigns,
  unignoreLead,
  updateLeadEmail,
} from '../api/campaigns.js';
import { useToast } from '../components/Toast.jsx';

const STATUS_CLASSES = {
  pending: 'bg-slate-100 text-slate-700',
  scheduled: 'bg-amber-100 text-amber-700',
  sent: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  // Terminal state for ignored / unsubscribed / bounced emails —
  // distinct from failed so the user can tell a deliberate ignore from
  // a delivery problem at a glance.
  suppressed: 'bg-red-50 text-red-600',
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


// ── Lead detail modal helpers ────────────────────────────────────────────

const HISTORY_STATUS_CLASS = {
  success: 'text-emerald-700',
  warn: 'text-amber-700',
  fail: 'text-red-700',
};

const LINKEDIN_CONN_LABEL = {
  unknown: 'Unknown',
  invited: 'Invited',
  connected: 'Connected',
  declined: 'Declined',
};

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}


function LeadCrmModal({ lead, onClose }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  // Single source of truth: the cross-campaign detail endpoint with the
  // embedded timeline + suppression state.  (A legacy per-campaign
  // fallback used to live here, but it double-fetched on every open and
  // its response lacked is_suppressed/history — wrong button states
  // whenever it won the race.  Frontend and backend deploy atomically,
  // so the fallback bought nothing.)
  const { data: view } = useQuery({
    queryKey: ['lead-detail-v2', lead.id],
    queryFn: () => getLeadById(lead.id),
  });

  const [notes, setNotes] = useState('');
  const [hydrated, setHydrated] = useState(false);
  if (view && !hydrated) {
    setNotes(view.notes || '');
    setHydrated(true);
  }
  const dirty = hydrated && notes !== (view?.notes || '');

  const saveMutation = useMutation({
    // updateLeadEmail still takes campaign_id (per-campaign route).  Use
    // the lead's own campaign_id so the call lands correctly.
    mutationFn: () => updateLeadEmail(lead.campaign_id, lead.id, { notes }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['lead-detail-v2', lead.id] });
      queryClient.invalidateQueries({ queryKey: ['all-leads'] });
      toast.success('Notes saved');
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed to save notes'),
  });

  const ignoreMutation = useMutation({
    mutationFn: () => ignoreLead(lead.id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['lead-detail-v2', lead.id] });
      queryClient.invalidateQueries({ queryKey: ['all-leads'] });
      const halted = data?.leads_halted ?? 0;
      const camps = (data?.campaigns_affected || []).length;
      if (data?.already_suppressed) {
        toast.success(
          halted > 0
            ? `Already suppressed — halted ${halted} drifted lead row${halted === 1 ? '' : 's'}.`
            : 'This lead was already suppressed.',
        );
      } else {
        toast.success(
          `Lead ignored — suppressed and halted ${halted} sequence${halted === 1 ? '' : 's'}` +
          (camps > 1 ? ` across ${camps} campaigns.` : '.'),
        );
      }
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed to ignore lead'),
  });

  const unignoreMutation = useMutation({
    mutationFn: () => unignoreLead(lead.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['lead-detail-v2', lead.id] });
      queryClient.invalidateQueries({ queryKey: ['all-leads'] });
      toast.success('Lead un-suppressed — future campaigns can contact them again.');
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed to un-ignore lead'),
  });

  const isSuppressed = view?.is_suppressed === true;
  const suppressionReason = view?.suppression_reason;

  // Derived display values (safe on partial detail loads).
  const fullName = (lead.first_name || lead.last_name)
    ? `${lead.first_name || ''} ${lead.last_name || ''}`.trim()
    : lead.email;
  const history = view?.history || [];
  const counts = view?.history_counts || {};
  const sigSummary = view?.research_summary || {};

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl p-6 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
        data-testid="lead-crm-modal"
      >
        {/* Header */}
        <div className="flex justify-between items-start mb-4">
          <div>
            <h2 className="m-0 text-lg font-semibold text-slate-900">{fullName}</h2>
            <div className="text-sm text-slate-500 mt-0.5">
              {lead.email}
              {(view?.job_title || lead.job_title) && (
                <> · {view?.job_title || lead.job_title}</>
              )}
              {(view?.company || lead.company) && (
                <> at {view?.company || lead.company}</>
              )}
              {lead.campaign_name && (
                <> · <span className="text-slate-400">campaign</span> {lead.campaign_name}</>
              )}
            </div>
            {/* Stat pills: quick at-a-glance roll-up */}
            <div className="mt-2 flex flex-wrap items-center gap-1.5" data-testid="lead-stat-pills">
              <StatusPill value={lead.send_status} />
              {isSuppressed && (
                <span
                  data-testid="lead-suppressed-badge"
                  className="px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-800"
                  title={
                    suppressionReason
                      ? `Suppressed (${suppressionReason}) — workspace-wide; future campaigns cannot contact this email.`
                      : 'Suppressed workspace-wide; future campaigns cannot contact this email.'
                  }
                >
                  🚫 Suppressed
                </span>
              )}
              {counts.opened > 0 && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700">
                  👀 {counts.opened} open{counts.opened === 1 ? '' : 's'}
                </span>
              )}
              {counts.clicked > 0 && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-50 text-emerald-700">
                  🖱️ {counts.clicked} click{counts.clicked === 1 ? '' : 's'}
                </span>
              )}
              {counts.replied > 0 && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-50 text-blue-700">
                  💬 {counts.replied} repl{counts.replied === 1 ? 'y' : 'ies'}
                </span>
              )}
              {(counts.hard_bounce || counts.soft_bounce) && (
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-red-50 text-red-700">
                  ⚠️ bounced
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* Ignore / Un-ignore — primary destructive action up top so
                the user finds it without scrolling.  Disabled while the
                detail is still loading so the user can't kick off the
                action before seeing the current suppression state. */}
            {isSuppressed ? (
              <button
                type="button"
                onClick={() => {
                  if (confirm(
                    'Remove this lead from the suppression list?  ' +
                    'Future campaigns will be able to contact this email again.  ' +
                    'This does NOT reactivate any already-halted sequence steps — ' +
                    "you'd need to re-enroll the lead via the Activity tab.",
                  )) unignoreMutation.mutate();
                }}
                disabled={!view || unignoreMutation.isPending}
                data-testid="lead-unignore-btn"
                className="px-3 py-1.5 text-xs font-medium border border-slate-300 text-slate-700 hover:bg-slate-50 rounded-lg disabled:opacity-50"
              >
                {unignoreMutation.isPending ? 'Un-ignoring…' : 'Un-ignore'}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  if (confirm(
                    `Ignore ${lead.email}?\n\n` +
                    "• They'll be added to the workspace suppression list.\n" +
                    '• Any active sequence steps for this email (across all campaigns) will be halted.\n' +
                    "• Future campaigns that include this email will skip them automatically.\n\n" +
                    'You can un-ignore later from this same modal.',
                  )) ignoreMutation.mutate();
                }}
                disabled={!view || ignoreMutation.isPending}
                data-testid="lead-ignore-btn"
                className="px-3 py-1.5 text-xs font-medium border border-red-300 text-red-700 hover:bg-red-50 rounded-lg disabled:opacity-50"
              >
                {ignoreMutation.isPending ? 'Ignoring…' : '🚫 Ignore lead'}
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="text-slate-400 hover:text-slate-600 text-xl bg-transparent border-none cursor-pointer p-1"
              aria-label="Close"
            >×</button>
          </div>
        </div>

        {/* Contact / outreach info card */}
        <div
          data-testid="lead-contact-section"
          className="bg-slate-50 border border-slate-200 rounded-lg p-3 mb-4 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs"
        >
          <InfoRow label="Email">
            <a href={`mailto:${lead.email}`} className="text-blue-600 hover:underline">{lead.email}</a>
          </InfoRow>
          {(view?.phone || null) && (
            <InfoRow label="Phone">
              <a href={`tel:${view.phone}`} className="text-blue-600 hover:underline">{view.phone}</a>
            </InfoRow>
          )}
          {view?.linkedin_url && (
            <InfoRow label="LinkedIn">
              <a
                href={view.linkedin_url}
                target="_blank" rel="noopener noreferrer"
                data-testid="lead-linkedin-link"
                className="text-blue-600 hover:underline"
              >
                Open profile ↗
              </a>
              {view?.linkedin_connection_status && view.linkedin_connection_status !== 'unknown' && (
                <span className="ml-2 text-slate-500">
                  ({LINKEDIN_CONN_LABEL[view.linkedin_connection_status] || view.linkedin_connection_status})
                </span>
              )}
            </InfoRow>
          )}
          {view?.company_website && (
            <InfoRow label="Website">
              <a
                href={
                  view.company_website.startsWith('http')
                    ? view.company_website
                    : `https://${view.company_website}`
                }
                target="_blank" rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                {view.company_website} ↗
              </a>
            </InfoRow>
          )}
          {sigSummary.industry && <InfoRow label="Industry">{sigSummary.industry}</InfoRow>}
          {sigSummary.size_hint && <InfoRow label="Size">{sigSummary.size_hint}</InfoRow>}
        </div>

        {/* Composed email preview */}
        {view?.composed_subject || view?.composed_body ? (
          <details className="border border-slate-200 rounded-lg overflow-hidden mb-4" open>
            <summary className="bg-slate-50 border-b border-slate-200 px-4 py-2.5 cursor-pointer text-xs font-semibold text-slate-500 uppercase tracking-wide">
              Composed email
            </summary>
            <div className="px-4 py-2.5 border-b border-slate-100">
              <span className="text-xs font-semibold text-slate-500 mr-2">Subject:</span>
              <span className="text-sm text-slate-900 font-medium">{view.composed_subject || '—'}</span>
            </div>
            <div className="p-4">
              <pre className="text-sm text-slate-800 whitespace-pre-wrap font-sans leading-relaxed m-0">
                {view.composed_body || '—'}
              </pre>
            </div>
          </details>
        ) : (
          <div className="text-sm text-slate-500 italic py-2 mb-4">No email composed yet.</div>
        )}

        {/* Activity timeline */}
        <details className="border border-slate-200 rounded-lg overflow-hidden mb-4" open>
          <summary className="bg-slate-50 border-b border-slate-200 px-4 py-2.5 cursor-pointer text-xs font-semibold text-slate-500 uppercase tracking-wide flex items-center justify-between">
            <span>Activity history</span>
            <span className="text-slate-400 normal-case font-normal">
              {history.length} event{history.length === 1 ? '' : 's'}
            </span>
          </summary>
          <div data-testid="lead-history-list" className="divide-y divide-slate-100">
            {history.length === 0 ? (
              <div className="p-4 text-sm text-slate-500 italic">
                No activity yet — this lead hasn't moved through any sequence step or
                received any opens/clicks.
              </div>
            ) : (
              history.map((h, i) => (
                <div key={i} className="flex items-start gap-3 px-4 py-2.5 text-sm">
                  <span className="text-lg leading-tight">{h.icon}</span>
                  <div className="flex-1 min-w-0">
                    <div className={`font-medium ${HISTORY_STATUS_CLASS[h.status] || 'text-slate-900'}`}>
                      {h.action}
                    </div>
                    {h.detail && (
                      <div className="text-xs text-slate-500 mt-0.5 break-words" title={h.detail}>
                        {h.detail.length > 200 ? `${h.detail.slice(0, 200)}…` : h.detail}
                      </div>
                    )}
                  </div>
                  <div className="text-xs text-slate-400 whitespace-nowrap">{fmtTime(h.at)}</div>
                </div>
              ))
            )}
          </div>
        </details>

        {/* Research details */}
        {(sigSummary.person_news?.length > 0
          || sigSummary.company_news?.length > 0
          || sigSummary.company_description) && (
          <details className="border border-slate-200 rounded-lg overflow-hidden mb-4">
            <summary className="bg-slate-50 border-b border-slate-200 px-4 py-2.5 cursor-pointer text-xs font-semibold text-slate-500 uppercase tracking-wide">
              Research findings
              {sigSummary.from_cache && (
                <span className="ml-2 text-amber-600 normal-case font-normal">(from cache)</span>
              )}
            </summary>
            <div className="p-4 space-y-3 text-sm">
              {sigSummary.company_description && (
                <div>
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                    Company
                  </div>
                  <p className="text-slate-700 m-0">{sigSummary.company_description}</p>
                </div>
              )}
              {sigSummary.person_news?.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                    Person news
                  </div>
                  <ul className="list-disc list-inside text-slate-700 m-0 space-y-0.5">
                    {sigSummary.person_news.map((n, i) => <li key={i}>{n}</li>)}
                  </ul>
                </div>
              )}
              {sigSummary.company_news?.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
                    Company news
                  </div>
                  <ul className="list-disc list-inside text-slate-700 m-0 space-y-0.5">
                    {sigSummary.company_news.map((n, i) => <li key={i}>{n}</li>)}
                  </ul>
                </div>
              )}
            </div>
          </details>
        )}

        {/* Notes */}
        <div className="space-y-2">
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Notes
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={5}
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


/** Small key/value row used in the contact-info card.  Renders nothing
 *  when ``children`` is empty so call-sites can guard with ``&&`` and
 *  still get clean layout. */
function InfoRow({ label, children }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-slate-500 w-20 shrink-0">{label}</span>
      <span className="text-slate-900 truncate">{children}</span>
    </div>
  );
}
