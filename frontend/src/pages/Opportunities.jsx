import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createOpportunity,
  getPipelineSummary,
  listOpportunities,
} from '../api/crm.js';
import { useToast } from '../components/Toast.jsx';

// Pipeline order — matches the backend OpportunityStage enum.
export const STAGES = [
  { value: 'prospecting', label: 'Prospecting' },
  { value: 'qualification', label: 'Qualification' },
  { value: 'proposal', label: 'Proposal' },
  { value: 'negotiation', label: 'Negotiation' },
  { value: 'closed_won', label: 'Closed won' },
  { value: 'closed_lost', label: 'Closed lost' },
];

const STAGE_HEADER_CLASS = {
  prospecting: 'border-sky-300 text-sky-700',
  qualification: 'border-amber-300 text-amber-700',
  proposal: 'border-orange-300 text-orange-700',
  negotiation: 'border-violet-300 text-violet-700',
  closed_won: 'border-emerald-300 text-emerald-700',
  closed_lost: 'border-slate-300 text-slate-500',
};

export function fmtAmount(n) {
  if (n == null) return '—';
  return new Intl.NumberFormat(undefined, {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  }).format(n);
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString();
}


export default function Opportunities() {
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [showClosed, setShowClosed] = useState(false);

  const { data: page, isLoading } = useQuery({
    queryKey: ['crm-opportunities'],
    queryFn: () => listOpportunities({ page_size: 500 }),
  });
  const { data: pipeline } = useQuery({
    queryKey: ['crm-pipeline'],
    queryFn: getPipelineSummary,
  });

  const opps = page?.items ?? [];
  const byStage = {};
  for (const stage of STAGES) byStage[stage.value] = [];
  for (const o of opps) (byStage[o.stage] ||= []).push(o);

  const pipelineByStage = {};
  for (const p of pipeline || []) pipelineByStage[p.stage] = p;

  const visibleStages = showClosed
    ? STAGES
    : STAGES.filter((s) => !s.value.startsWith('closed_'));

  return (
    <div className="p-6 max-w-[110rem] mx-auto">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-900 m-0">Opportunities</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={showClosed}
              onChange={(e) => setShowClosed(e.target.checked)}
              data-testid="show-closed-toggle"
            />
            Show closed
          </label>
          <button
            type="button"
            onClick={() => setCreating(true)}
            data-testid="new-opportunity-btn"
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg"
          >
            + New opportunity
          </button>
        </div>
      </div>
      <p className="text-sm text-slate-500 mb-5">
        Your deal pipeline. Convert leads from the Leads page, or create deals
        from scratch. Click a card for details + activity log.
      </p>

      {isLoading ? (
        <div className="text-center text-slate-500 py-12">Loading…</div>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4" data-testid="pipeline-board">
          {visibleStages.map((stage) => {
            const cards = byStage[stage.value] || [];
            const summary = pipelineByStage[stage.value];
            return (
              <div
                key={stage.value}
                data-testid={`stage-column-${stage.value}`}
                className="flex-1 min-w-[230px] bg-slate-50 rounded-xl border border-slate-200 p-2.5"
              >
                <div className={`flex items-baseline justify-between px-1.5 pb-2 mb-2 border-b-2 ${STAGE_HEADER_CLASS[stage.value]}`}>
                  <span className="text-sm font-semibold">{stage.label}</span>
                  <span className="text-xs text-slate-400">
                    {summary ? `${summary.count} · ${fmtAmount(summary.total_amount)}` : cards.length}
                  </span>
                </div>
                <div className="space-y-2">
                  {cards.length === 0 ? (
                    <div className="text-xs text-slate-400 italic px-1.5 py-3 text-center">empty</div>
                  ) : cards.map((o) => (
                    <button
                      key={o.id}
                      type="button"
                      onClick={() => navigate(`/opportunities/${o.id}`)}
                      data-testid={`opp-card-${o.id}`}
                      className="w-full text-left bg-white rounded-lg border border-slate-200 shadow-sm p-3 hover:border-blue-300 hover:shadow"
                    >
                      <div className="text-sm font-medium text-slate-900 truncate">{o.name}</div>
                      <div className="text-xs text-slate-500 mt-0.5 truncate">
                        {o.company || [o.first_name, o.last_name].filter(Boolean).join(' ') || o.email || '—'}
                      </div>
                      <div className="flex items-center justify-between mt-1.5 text-xs">
                        <span className="font-semibold text-slate-700">{fmtAmount(o.amount)}</span>
                        <span className="text-slate-400">
                          {o.close_date ? fmtDate(o.close_date) : ''}
                        </span>
                      </div>
                      {o.open_task_count > 0 && (
                        <div className="mt-1 text-xs text-amber-600">
                          ☑️ {o.open_task_count} open task{o.open_task_count === 1 ? '' : 's'}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {creating && <NewOpportunityModal onClose={() => setCreating(false)} />}
    </div>
  );
}


function NewOpportunityModal({ onClose }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    name: '', amount: '', close_date: '', company: '',
    first_name: '', last_name: '', email: '',
  });
  const update = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const createMut = useMutation({
    mutationFn: () => createOpportunity({
      name: form.name.trim(),
      amount: form.amount === '' ? null : Number(form.amount),
      close_date: form.close_date || null,
      company: form.company.trim() || null,
      first_name: form.first_name.trim() || null,
      last_name: form.last_name.trim() || null,
      email: form.email.trim() || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm-opportunities'] });
      queryClient.invalidateQueries({ queryKey: ['crm-pipeline'] });
      toast.success('Opportunity created');
      onClose();
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed to create'),
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6"
        onClick={(e) => e.stopPropagation()}
        data-testid="new-opportunity-modal"
      >
        <div className="flex justify-between items-center mb-4">
          <h2 className="m-0 text-lg font-semibold text-slate-900">New opportunity</h2>
          <button type="button" onClick={onClose} aria-label="Close"
            className="text-slate-400 hover:text-slate-600 text-xl bg-transparent border-none cursor-pointer p-1">×</button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-semibold text-slate-600 mb-1">Deal name *</label>
            <input type="text" value={form.name} onChange={(e) => update('name', e.target.value)}
              data-testid="new-opp-name"
              placeholder="Acme — managed IT contract"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">Amount ($)</label>
              <input type="number" min="0" value={form.amount}
                onChange={(e) => update('amount', e.target.value)}
                data-testid="new-opp-amount"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">Expected close</label>
              <input type="date" value={form.close_date}
                onChange={(e) => update('close_date', e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">Company</label>
              <input type="text" value={form.company} onChange={(e) => update('company', e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">Contact email</label>
              <input type="text" value={form.email} onChange={(e) => update('email', e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose}
              className="px-3 py-1.5 text-sm border border-slate-300 text-slate-700 rounded-lg hover:bg-slate-50">
              Cancel
            </button>
            <button
              type="button"
              onClick={() => createMut.mutate()}
              disabled={!form.name.trim() || createMut.isPending}
              data-testid="new-opp-save"
              className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg"
            >
              {createMut.isPending ? 'Creating…' : 'Create'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
