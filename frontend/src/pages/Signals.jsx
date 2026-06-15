import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  actionSignal,
  createWatch,
  createWatchesBulk,
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

const SOURCE_BADGES = {
  usaspending: { label: 'USASpending', cls: 'bg-indigo-100 text-indigo-700' },
  irs_bmf: { label: 'IRS BMF', cls: 'bg-teal-100 text-teal-700' },
};

function SourceBadge({ source }) {
  // No source = a watch-sourced signal.
  const meta = SOURCE_BADGES[source] || { label: 'Watch', cls: 'bg-slate-100 text-slate-500' };
  return (
    <span
      data-testid="signal-source-badge"
      className={`px-2 py-0.5 rounded-full text-xs font-semibold ${meta.cls}`}
    >
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
          <SourceBadge source={signal.source} />
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

const WATCH_TYPE_META = {
  job_change: {
    label: 'Job change',
    desc: "Alerts when this person's job title changes. Matched by EMAIL — that's the one required field.",
    needsEmail: true,
  },
  funding: {
    label: 'Funding',
    desc: 'Alerts when a company announces a new funding round or stage. Needs a COMPANY name. Supports pasting multiple companies.',
    needsCompany: true,
    bulk: true,
  },
  hiring: {
    label: 'Hiring',
    desc: 'Alerts when a company starts hiring growth roles (sales, engineering, ops leadership). Needs a COMPANY name. Supports pasting multiple companies.',
    needsCompany: true,
    bulk: true,
  },
  custom: {
    label: 'Everything',
    desc: 'Runs every check it can: job change (needs email), funding + hiring (need company). Give at least one of email / company.',
    needsEither: true,
  },
};

function NewWatchForm({ onDone }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    watch_type: 'funding', company: '', person_name: '', email: '',
    frequency: 'daily',
  });
  const [bulkMode, setBulkMode] = useState(false);
  const [bulkCompanies, setBulkCompanies] = useState('');

  const meta = WATCH_TYPE_META[form.watch_type];
  const bulkAvailable = !!meta.bulk;
  const bulkList = bulkCompanies.split('\n').map((x) => x.trim()).filter(Boolean);

  const canSave = (() => {
    if (bulkMode && bulkAvailable) return bulkList.length > 0;
    if (meta.needsEmail) return !!form.email.trim();
    if (meta.needsCompany) return !!form.company.trim();
    if (meta.needsEither) return !!(form.email.trim() || form.company.trim());
    return false;
  })();

  const onSuccess = (msg) => {
    queryClient.invalidateQueries({ queryKey: ['signal-watches'] });
    toast.success(msg);
    onDone();
  };

  const createMut = useMutation({
    mutationFn: () => createWatch({
      watch_type: form.watch_type,
      frequency: form.frequency,
      company: form.company.trim() || null,
      person_name: form.person_name.trim() || null,
      email: form.email.trim() || null,
    }),
    onSuccess: () => onSuccess('Watch created — first check runs within a minute'),
    onError: (err) => toast.error(err?.response?.data?.detail || 'Failed to create watch'),
  });

  const bulkMut = useMutation({
    mutationFn: () => createWatchesBulk({
      watch_type: form.watch_type,
      frequency: form.frequency,
      companies: bulkList,
    }),
    onSuccess: (res) => onSuccess(
      `${res.created} watch${res.created === 1 ? '' : 'es'} created`
      + (res.skipped_duplicate ? ` · ${res.skipped_duplicate} already watched` : ''),
    ),
    onError: (err) => toast.error(err?.response?.data?.detail || 'Bulk create failed'),
  });

  const pending = createMut.isPending || bulkMut.isPending;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <div data-testid="new-watch-form" className="bg-slate-50 border border-slate-200 rounded-xl p-4 space-y-3 mb-4">
      {/* Type picker with descriptions */}
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(WATCH_TYPE_META).map(([value, m]) => (
          <button
            key={value}
            type="button"
            data-testid={`watch-type-${value}`}
            aria-pressed={form.watch_type === value}
            onClick={() => { setForm({ ...form, watch_type: value }); setBulkMode(false); }}
            className={`px-3 py-1.5 text-sm font-medium rounded-lg border ${
              form.watch_type === value
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-100'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      <p data-testid="watch-type-desc" className="text-xs text-slate-500 m-0">{meta.desc}</p>

      {bulkAvailable && (
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            data-testid="bulk-mode-toggle"
            checked={bulkMode}
            onChange={(e) => setBulkMode(e.target.checked)}
          />
          Multiple companies (one watch each)
        </label>
      )}

      {bulkMode && bulkAvailable ? (
        <div>
          <textarea
            data-testid="bulk-companies-input"
            rows={5}
            value={bulkCompanies}
            onChange={(e) => setBulkCompanies(e.target.value)}
            placeholder={'Acme Corp\nBeta Inc\nGamma LLC'}
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm bg-white font-mono"
          />
          <p className="text-xs text-slate-400 m-0 mt-1">
            One company per line · {bulkList.length} compan{bulkList.length === 1 ? 'y' : 'ies'} —
            companies already watched for this signal are skipped.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          {(meta.needsEmail || meta.needsEither) && (
            <>
              <input
                data-testid="watch-person"
                placeholder="Person name (optional)"
                value={form.person_name}
                onChange={set('person_name')}
                className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white"
              />
              <input
                data-testid="watch-email"
                placeholder={meta.needsEmail ? 'Email (required)' : 'Email'}
                value={form.email}
                onChange={set('email')}
                className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white"
              />
            </>
          )}
          <input
            data-testid="watch-company"
            placeholder={meta.needsCompany ? 'Company (required)' : 'Company'}
            value={form.company}
            onChange={set('company')}
            className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white"
          />
        </div>
      )}

      <div className="flex items-center gap-3">
        <label className="text-xs text-slate-600">
          Check
          <select
            value={form.frequency}
            onChange={set('frequency')}
            className="ml-2 border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white"
          >
            <option value="every_6h">Every 6h</option>
            <option value="every_12h">Every 12h</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="manual">Manual only</option>
          </select>
        </label>
        <span className="text-xs text-slate-400">
          Already in your pipeline? Track a lead from its record on the Leads page instead.
        </span>
      </div>

      <div className="flex justify-end gap-2">
        <button type="button" onClick={onDone} className="px-3 py-1.5 text-sm border border-slate-300 rounded-lg text-slate-700">Cancel</button>
        <button
          type="button"
          data-testid="save-watch-btn"
          onClick={() => (bulkMode && bulkAvailable ? bulkMut.mutate() : createMut.mutate())}
          disabled={pending || !canSave}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg disabled:opacity-50"
        >
          {pending ? 'Saving…' : bulkMode && bulkAvailable
            ? `Create ${bulkList.length || ''} watch${bulkList.length === 1 ? '' : 'es'}`
            : 'Create watch'}
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
          No watches yet. Create one to monitor a person's job changes (by
          email) or a company's funding/hiring — or paste a whole list of
          companies with the "Multiple companies" option.
        </p>
      )}
      <div className="space-y-2">
        {watches.map((w) => (
          <div key={w.id} data-testid={`watch-row-${w.id}`} className="bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <TypeBadge type={w.watch_type} />
                <span className="font-medium text-slate-800 truncate">
                  {w.person_name || w.company || w.email
                    || (w.lead_id ? 'Tracked lead' : w.opportunity_id ? 'Tracked deal' : 'Tracked record')}
                </span>
                {w.person_name && w.company && (
                  <span className="text-sm text-slate-500">· {w.company}</span>
                )}
                {(w.lead_id || w.opportunity_id) && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-indigo-100 text-indigo-700">
                    {w.lead_id ? 'CRM lead' : 'deal'}
                  </span>
                )}
                {w.status === 'paused' && (
                  <span className="text-xs text-amber-600">paused</span>
                )}
              </div>
              <p className="text-xs text-slate-400 m-0 mt-0.5" data-testid={`watch-baseline-${w.id}`}>
                {w.frequency} · last run: {w.last_run_at ? new Date(w.last_run_at).toLocaleString() : 'never (first check within a minute)'}
                {w.last_run_status ? ` (${w.last_run_status})` : ''}
                {w.last_seen?.job_title && ` · baseline title: ${w.last_seen.job_title}`}
                {w.last_seen?.funding_stage && ` · baseline stage: ${w.last_seen.funding_stage}`}
                {(w.last_seen?.hiring_roles || []).length > 0
                  && ` · tracking ${w.last_seen.hiring_roles.length} open role${w.last_seen.hiring_roles.length === 1 ? '' : 's'}`}
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
  const [sourceFilter, setSourceFilter] = useState('');

  const { data } = useQuery({
    queryKey: ['prospect-signals', statusFilter, sourceFilter],
    queryFn: () => listSignals({
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(sourceFilter ? { source: sourceFilter } : {}),
    }),
  });
  const items = data?.items || [];

  return (
    <div data-testid="signals-page" className="p-8 max-w-4xl">
      <h1 className="text-2xl font-bold text-slate-900 mb-1">Signals</h1>
      <p className="text-slate-500 text-sm mt-0 mb-4">
        Job changes, funding rounds, and hiring sprees on tracked prospects —
        reach out while the trigger is fresh.
      </p>

      <details data-testid="signals-help" className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 mb-4 text-sm text-blue-900">
        <summary className="font-medium cursor-pointer">How signals work</summary>
        <div className="mt-2 space-y-2 text-blue-900/90">
          <p className="m-0">
            A <strong>watch</strong> monitors one target on a schedule. When
            something changes, a <strong>signal</strong> lands in this feed —
            plus an alert, and a "Reach out" task when the target is a
            tracked lead or deal. Cold targets with an email get staged as a
            CRM lead. Nothing is ever emailed or added to a campaign
            automatically.
          </p>
          <ul className="m-0 pl-5">
            <li><strong>Job change</strong> — a person's title changed (needs their <em>email</em>; uses Apollo).</li>
            <li><strong>Funding</strong> — a company announced a new round/stage (needs the <em>company name</em>).</li>
            <li><strong>Hiring</strong> — a company is hiring growth roles (needs the <em>company name</em>).</li>
            <li><strong>Everything</strong> — all of the above, using whatever fields you give it.</li>
          </ul>
          <p className="m-0">
            Each watch tracks <strong>one</strong> company or person — to
            monitor a list of companies, use the "Multiple companies" option
            when creating a funding or hiring watch. The first check seeds a
            baseline; signals fire on <em>changes</em> after that, once each.
          </p>
        </div>
      </details>
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
          <div className="flex justify-end gap-2 mb-3">
            <select
              data-testid="signal-source-filter"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
              className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
            >
              <option value="">All sources</option>
              <option value="watch">Watches</option>
              <option value="usaspending">USASpending</option>
              <option value="irs_bmf">IRS BMF</option>
            </select>
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
