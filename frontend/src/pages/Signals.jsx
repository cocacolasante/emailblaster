import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  actionSignal,
  createWatch,
  deleteWatch,
  dismissSignal,
  listSignals,
  listWatches,
  runWatchNow,
  updateWatch,
} from '../api/signals.js';
import { useToast } from '../components/Toast.jsx';

const TYPE_BADGES = {
  job_change: { label: 'Job change', cls: 'bg-violet-100 text-violet-700' },
  funding: { label: 'Funding', cls: 'bg-emerald-100 text-emerald-700' },
  hiring: { label: 'Hiring', cls: 'bg-sky-100 text-sky-700' },
  custom: { label: 'Custom', cls: 'bg-slate-100 text-slate-600' },
};

function TypeBadge({ type }) {
  const meta = TYPE_BADGES[type] || TYPE_BADGES.custom;
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${meta.cls}`}>
      {meta.label}
    </span>
  );
}

function SignalCard({ signal }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['prospect-signals'] });

  const actionMut = useMutation({
    mutationFn: () => actionSignal(signal.id),
    onSuccess: () => { invalidate(); toast.success('Marked actioned'); },
  });
  const dismissMut = useMutation({
    mutationFn: () => dismissSignal(signal.id),
    onSuccess: () => { invalidate(); toast.success('Dismissed'); },
  });

  const detail = signal.detail || {};
  return (
    <div
      data-testid={`signal-card-${signal.id}`}
      className="bg-white border border-slate-200 rounded-xl p-4 flex items-start justify-between gap-3"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <TypeBadge type={signal.signal_type} />
          <span className="text-xs text-slate-400">
            {new Date(signal.detected_at).toLocaleString()}
          </span>
          {signal.status !== 'new' && (
            <span className="text-xs text-slate-400 italic">{signal.status}</span>
          )}
        </div>
        <p className="font-medium text-slate-800 m-0">{signal.summary}</p>
        <p className="text-xs text-slate-500 m-0 mt-1">
          {detail.old_title && `${detail.old_title} → ${detail.new_title}`}
          {detail.amount && ` · ${detail.amount}`}
          {(detail.roles || []).length > 0 && `Roles: ${detail.roles.join(', ')}`}
          {detail.source_url && (
            <>
              {' · '}
              <a href={detail.source_url} target="_blank" rel="noreferrer" className="text-blue-600">
                source ↗
              </a>
            </>
          )}
        </p>
      </div>
      {signal.status === 'new' && (
        <div className="flex gap-2 shrink-0">
          <button
            type="button"
            data-testid={`action-signal-${signal.id}`}
            onClick={() => actionMut.mutate()}
            className="text-sm px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700"
          >
            Actioned
          </button>
          <button
            type="button"
            data-testid={`dismiss-signal-${signal.id}`}
            onClick={() => dismissMut.mutate()}
            className="text-sm px-3 py-1.5 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

function NewWatchForm({ onDone }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    watch_type: 'job_change', company: '', person_name: '', email: '',
    frequency: 'daily',
  });

  const createMut = useMutation({
    mutationFn: () => createWatch({
      ...form,
      company: form.company || null,
      person_name: form.person_name || null,
      email: form.email || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['signal-watches'] });
      toast.success('Watch created');
      onDone();
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed to create watch'),
  });

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  return (
    <div data-testid="new-watch-form" className="bg-slate-50 border border-slate-200 rounded-xl p-4 space-y-3 mb-4">
      <div className="grid grid-cols-2 gap-3">
        <label className="text-xs text-slate-600">
          Type
          <select data-testid="watch-type" value={form.watch_type} onChange={set('watch_type')} className="mt-1 w-full border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white">
            <option value="job_change">Job change</option>
            <option value="funding">Funding</option>
            <option value="hiring">Hiring</option>
            <option value="custom">Custom (all)</option>
          </select>
        </label>
        <label className="text-xs text-slate-600">
          Frequency
          <select value={form.frequency} onChange={set('frequency')} className="mt-1 w-full border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white">
            <option value="every_6h">Every 6h</option>
            <option value="every_12h">Every 12h</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="manual">Manual</option>
          </select>
        </label>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <input data-testid="watch-company" placeholder="Company" value={form.company} onChange={set('company')} className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white" />
        <input data-testid="watch-person" placeholder="Person name" value={form.person_name} onChange={set('person_name')} className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white" />
        <input data-testid="watch-email" placeholder="Email (for job-change + lead staging)" value={form.email} onChange={set('email')} className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white" />
      </div>
      <p className="text-xs text-slate-400 m-0">
        Detected signals create tasks, alerts, and (for cold targets with an
        email) a CRM lead — never a campaign send. Adding to a campaign stays
        a manual step.
      </p>
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onDone} className="px-3 py-1.5 text-sm border border-slate-300 rounded-lg text-slate-700">Cancel</button>
        <button
          type="button"
          data-testid="save-watch-btn"
          onClick={() => createMut.mutate()}
          disabled={createMut.isPending || (!form.company && !form.person_name)}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50"
        >
          {createMut.isPending ? 'Saving…' : 'Create watch'}
        </button>
      </div>
    </div>
  );
}

function WatchesTab() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const { data: watches = [] } = useQuery({
    queryKey: ['signal-watches'],
    queryFn: listWatches,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['signal-watches'] });

  const runMut = useMutation({
    mutationFn: runWatchNow,
    onSuccess: () => toast.success('Check enqueued'),
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed'),
  });
  const pauseMut = useMutation({
    mutationFn: ({ id, status }) => updateWatch(id, { status }),
    onSuccess: invalidate,
  });
  const deleteMut = useMutation({
    mutationFn: deleteWatch,
    onSuccess: () => { invalidate(); toast.success('Watch deleted'); },
  });

  return (
    <div>
      <div className="flex justify-end mb-3">
        <button
          type="button"
          data-testid="new-watch-btn"
          onClick={() => setShowForm((v) => !v)}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg"
        >
          {showForm ? 'Cancel' : '+ New watch'}
        </button>
      </div>
      {showForm && <NewWatchForm onDone={() => setShowForm(false)} />}
      {watches.length === 0 && !showForm && (
        <p data-testid="watches-empty" className="text-sm text-slate-400 text-center py-8">
          No watches yet. Track a lead, deal, or cold target for job-change,
          funding, and hiring signals.
        </p>
      )}
      <div className="space-y-2">
        {watches.map((w) => (
          <div key={w.id} data-testid={`watch-row-${w.id}`} className="bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <TypeBadge type={w.watch_type} />
                <span className="font-medium text-slate-800 truncate">
                  {w.person_name || w.company || w.email || 'Tracked record'}
                </span>
                {w.status === 'paused' && (
                  <span className="text-xs text-amber-600">paused</span>
                )}
              </div>
              <p className="text-xs text-slate-400 m-0 mt-0.5">
                {w.frequency} · last run: {w.last_run_at ? new Date(w.last_run_at).toLocaleString() : 'never'}
                {w.last_run_status ? ` (${w.last_run_status})` : ''}
              </p>
            </div>
            <div className="flex gap-2 shrink-0 text-sm">
              <button type="button" onClick={() => runMut.mutate(w.id)} className="text-blue-600 hover:text-blue-800">Run now</button>
              <button
                type="button"
                onClick={() => pauseMut.mutate({ id: w.id, status: w.status === 'active' ? 'paused' : 'active' })}
                className="text-slate-500 hover:text-slate-700"
              >
                {w.status === 'active' ? 'Pause' : 'Resume'}
              </button>
              <button type="button" onClick={() => deleteMut.mutate(w.id)} className="text-red-500 hover:text-red-700">Delete</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Signals() {
  const [tab, setTab] = useState('feed');
  const [statusFilter, setStatusFilter] = useState('new');

  const { data } = useQuery({
    queryKey: ['prospect-signals', statusFilter],
    queryFn: () => listSignals(statusFilter ? { status: statusFilter } : {}),
  });
  const items = data?.items || [];

  return (
    <div data-testid="signals-page" className="p-8 max-w-4xl">
      <h1 className="text-2xl font-bold text-slate-900 mb-1">Signals</h1>
      <p className="text-slate-500 text-sm mt-0 mb-4">
        Job changes, funding rounds, and hiring sprees on tracked prospects —
        reach out while the trigger is fresh.
      </p>
      <div role="tablist" className="flex border-b border-slate-200 mb-4">
        {[['feed', 'Feed'], ['watches', 'Watches']].map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px bg-transparent cursor-pointer ${
              tab === key ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'feed' && (
        <div>
          <div className="flex justify-end mb-3">
            <select
              data-testid="signal-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
            >
              <option value="new">New</option>
              <option value="actioned">Actioned</option>
              <option value="dismissed">Dismissed</option>
              <option value="">All</option>
            </select>
          </div>
          {items.length === 0 && (
            <p data-testid="signals-empty" className="text-sm text-slate-400 text-center py-8">
              No signals here. Watches run on their schedule and detected
              events land in this feed.
            </p>
          )}
          <div className="space-y-2">
            {items.map((s) => <SignalCard key={s.id} signal={s} />)}
          </div>
        </div>
      )}
      {tab === 'watches' && <WatchesTab />}
    </div>
  );
}
