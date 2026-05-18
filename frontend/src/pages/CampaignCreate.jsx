import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { listAccounts } from '../api/connectedAccounts.js';
import { listLinkedInAccounts } from '../api/linkedinAccounts.js';
import { createCampaign, getPreview, getPreviewProgress } from '../api/campaigns.js';
import ConnectInboxModal from '../components/ConnectInboxModal.jsx';
import LeadUpload from '../components/LeadUpload.jsx';
import ScheduleConfig from '../components/ScheduleConfig.jsx';
import { EmbeddedSequenceBuilder } from './SequenceBuilder.jsx';

const TONES = ['Professional', 'Friendly', 'Direct', 'Conversational', 'Formal'];

const DEFAULT_FORM = {
  name: '',
  goal: '',
  tone: 'Professional',
  sender_name: '',
  sender_email: '',
  research_mode: 'fast',
  sample_count: 5,
  connected_account_id: '',
  linkedin_account_id: '',
  schedule_days: [0, 1, 2, 3, 4],
  schedule_time_start: '09:00',
  schedule_time_end: '17:00',
  schedule_timezone: 'UTC',
  max_per_hour: null,
  max_per_day: null,
  min_delay_seconds: 60,
  min_delay_unit: 'seconds',
};

function StatusBadge({ status }) {
  if (!status) return null;
  const classes = {
    untested: 'bg-yellow-100 text-yellow-700',
    ok: 'bg-emerald-100 text-emerald-700',
    failed: 'bg-red-100 text-red-600',
  };
  const labels = { untested: 'Untested', ok: 'Connected', failed: 'Failed' };
  return (
    <span
      data-testid="inbox-status"
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${classes[status] || classes.untested}`}
    >
      {labels[status] || status}
    </span>
  );
}

// --------------------------------------------------------------------------
// Step 1: campaign details
// --------------------------------------------------------------------------

function Step1({ form, setForm, onSubmit, submitting, error, accounts, linkedinAccounts, onConnectInbox }) {
  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  const selectedAccount = accounts.find((a) => a.id === form.connected_account_id);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="space-y-6"
    >
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <h2 className="text-base font-semibold text-slate-900 mb-4">Campaign details</h2>
        <div className="space-y-4">
          <div>
            <label htmlFor="campaign-name" className="block text-sm font-medium text-slate-700 mb-1">Name</label>
            <input
              id="campaign-name"
              required
              value={form.name}
              onChange={(e) => update('name', e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
          </div>

          <div>
            <label htmlFor="campaign-goal" className="block text-sm font-medium text-slate-700 mb-1">Goal</label>
            <textarea
              id="campaign-goal"
              required
              rows={3}
              placeholder="What is the goal of this campaign? E.g. Book a demo, announce a product, invite to event"
              value={form.goal}
              onChange={(e) => update('goal', e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white font-[inherit]"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="campaign-tone" className="block text-sm font-medium text-slate-700 mb-1">Tone</label>
              <select
                id="campaign-tone"
                value={form.tone}
                onChange={(e) => update('tone', e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              >
                {TONES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="campaign-sample-count" className="block text-sm font-medium text-slate-700 mb-1">Sample count</label>
              <input
                id="campaign-sample-count"
                type="number"
                min="1"
                required
                value={form.sample_count}
                onChange={(e) => update('sample_count', Number(e.target.value))}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="campaign-sender-name" className="block text-sm font-medium text-slate-700 mb-1">Sender name</label>
              <input
                id="campaign-sender-name"
                required
                value={form.sender_name}
                onChange={(e) => update('sender_name', e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              />
            </div>
            <div>
              <label htmlFor="campaign-sender-email" className="block text-sm font-medium text-slate-700 mb-1">Sender email</label>
              <input
                id="campaign-sender-email"
                type="email"
                required
                value={form.sender_email}
                onChange={(e) => update('sender_email', e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              />
            </div>
          </div>

          <div>
            <span className="block text-sm font-medium text-slate-700 mb-2">Research mode</span>
            <div role="radiogroup" className="flex gap-2">
              {['fast', 'deep'].map((mode) => (
                <button
                  key={mode}
                  type="button"
                  role="radio"
                  aria-checked={form.research_mode === mode}
                  onClick={() => update('research_mode', mode)}
                  className={`px-4 py-2 rounded-full text-sm border font-medium transition-colors ${
                    form.research_mode === mode
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'border-slate-300 text-slate-600 hover:border-blue-400 bg-white'
                  }`}
                >
                  {mode === 'fast' ? 'Fast (web only, ~10s/lead)' : 'Deep (+Apollo, ~45s/lead)'}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <h2 className="text-base font-semibold text-slate-900 mb-1">
          Reply tracking{' '}
          <span className="text-slate-400 font-normal text-sm">(optional)</span>
        </h2>
        <p className="text-sm text-slate-500 mb-4">
          Connect an inbox to automatically detect when leads reply to your emails.
        </p>

        {accounts.length === 0 ? (
          <div data-testid="no-inbox-prompt" className="p-4 bg-slate-50 border border-slate-200 rounded-lg">
            <p className="text-sm text-slate-600 m-0 mb-3">No inboxes connected yet.</p>
            <button
              type="button"
              onClick={onConnectInbox}
              className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors"
            >
              Connect an inbox
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <select
              aria-label="Reply tracking inbox"
              value={form.connected_account_id}
              onChange={(e) => update('connected_account_id', e.target.value)}
              className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            >
              <option value="">No reply tracking</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label} ({a.email_address})
                </option>
              ))}
            </select>
            {selectedAccount && <StatusBadge status={selectedAccount.last_test_status} />}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <h2 className="text-base font-semibold text-slate-900 mb-1">
          LinkedIn account{' '}
          <span className="text-slate-400 font-normal text-sm">(optional)</span>
        </h2>
        <p className="text-sm text-slate-500 mb-4">
          Required only if this campaign's sequence includes LinkedIn nodes.
        </p>
        {linkedinAccounts.length === 0 ? (
          <div className="p-4 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600">
            No LinkedIn accounts connected. Add one in{' '}
            <a className="underline text-blue-600" href="/settings">Settings → LinkedIn accounts</a>.
          </div>
        ) : (
          <select
            aria-label="LinkedIn account"
            value={form.linkedin_account_id}
            onChange={(e) => update('linkedin_account_id', e.target.value)}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
          >
            <option value="">No LinkedIn actions</option>
            {linkedinAccounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label} ({a.linkedin_email}) — {a.status}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
        <h2 className="text-base font-semibold text-slate-900 mb-4">Schedule</h2>
        <ScheduleConfig value={form} onChange={(next) => setForm(next)} />
      </div>

      {error && (
        <div data-testid="step1-error" className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800">{error}</div>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting}
          className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="step1-submit"
        >
          {submitting ? 'Creating…' : 'Next: build sequence →'}
        </button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------
// Step 3: progress poll + auto-redirect
// --------------------------------------------------------------------------

function Step3({ campaignId, onComplete }) {
  const { data: progress } = useQuery({
    queryKey: ['preview-progress', campaignId],
    queryFn: () => getPreviewProgress(campaignId),
    refetchInterval: 5000,
  });
  const { data: preview } = useQuery({
    queryKey: ['preview', campaignId],
    queryFn: () => getPreview(campaignId),
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (preview?.all_ready) onComplete();
  }, [preview, onComplete]);

  const total = progress?.total_leads ?? 0;
  const researched = progress?.researched ?? 0;
  const composed = progress?.composed ?? 0;
  const pct = total ? Math.min(100, Math.round((composed / total) * 100)) : 0;

  return (
    <div data-testid="step3" className="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center">
      <div className="w-12 h-12 mx-auto mb-4 bg-blue-50 rounded-full flex items-center justify-center">
        <svg className="w-6 h-6 text-blue-600 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      </div>
      <h2 className="text-lg font-semibold text-slate-900 mb-2">Researching and composing emails…</h2>
      <p className="text-sm text-slate-500 mb-4">
        {composed} of {total} composed · {researched} researched
      </p>
      <div className="h-3 bg-slate-200 rounded-full overflow-hidden mx-auto max-w-sm mb-4">
        <div
          data-testid="progress-bar"
          className="h-full bg-blue-600 rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-sm text-slate-400">
        This page will auto-advance once the sample emails are ready for review.
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------
// Container
// --------------------------------------------------------------------------

export default function CampaignCreate() {
  // Wizard state lives partly in the URL so an accidental F5 mid-flow
  // resumes where the user left off instead of restarting at step 1 and
  // creating a duplicate campaign on the next submit.
  //   /campaigns/new                    -> step 1, no campaign yet
  //   /campaigns/new?id=<uuid>&step=2   -> resume at step 2 on that campaign
  const [searchParams, setSearchParams] = useSearchParams();
  const urlId = searchParams.get('id');
  const urlStep = parseInt(searchParams.get('step') || '1', 10);

  // Initial step: clamp to a valid range, and don't allow step 1 when we
  // already have an id (the campaign was created — replaying step 1 would
  // make a duplicate).
  const initialStep = (() => {
    const s = Number.isFinite(urlStep) ? urlStep : 1;
    if (urlId && s < 2) return 2;
    return Math.min(Math.max(s, 1), 4);
  })();

  const [step, setStepRaw] = useState(initialStep);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [campaignId, setCampaignIdRaw] = useState(urlId || null);
  const [showInboxModal, setShowInboxModal] = useState(false);
  const [error, setError] = useState(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Wrapped setters that also update the URL search params so refresh
  // and the browser back/forward buttons just work.
  const setStep = useCallback((nextStep) => {
    setStepRaw(nextStep);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('step', String(nextStep));
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const setCampaignId = useCallback((nextId) => {
    setCampaignIdRaw(nextId);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (nextId) next.set('id', String(nextId));
      else next.delete('id');
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const { data: accounts = [] } = useQuery({
    queryKey: ['connected-accounts'],
    queryFn: listAccounts,
  });
  const { data: linkedinAccounts = [] } = useQuery({
    queryKey: ['linkedin-accounts'],
    queryFn: listLinkedInAccounts,
  });

  const createMutation = useMutation({
    mutationFn: createCampaign,
    onSuccess: (data) => {
      setCampaignId(data.id);
      setStep(2);  // → Sequence
    },
    onError: (err) => {
      const detail = err?.response?.data?.detail || err.message || 'Failed to create campaign';
      setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    },
  });

  function buildPayload() {
    return {
      ...form,
      connected_account_id: form.connected_account_id || null,
      linkedin_account_id: form.linkedin_account_id || null,
      max_per_hour: form.max_per_hour || null,
      max_per_day: form.max_per_day || null,
      schedule_time_start:
        form.schedule_time_start.length === 5
          ? `${form.schedule_time_start}:00`
          : form.schedule_time_start,
      schedule_time_end:
        form.schedule_time_end.length === 5
          ? `${form.schedule_time_end}:00`
          : form.schedule_time_end,
    };
  }

  function handleStep1Submit() {
    setError(null);
    createMutation.mutate(buildPayload());
  }

  function onSequenceDone() {
    setStep(3);  // → Upload leads
  }

  function onUploadComplete() {
    setStep(4);  // → Research
  }

  function onResearchComplete() {
    if (campaignId) navigate(`/campaigns/${campaignId}/preview`);
  }

  const STEP_LABELS = ['Details', 'Sequence', 'Upload leads', 'Research'];

  // The Sequence step needs the full viewport for the canvas; every other
  // step uses the standard narrow wizard width.
  const wide = step === 2;

  return (
    <div className={wide ? 'p-6 w-full' : 'p-8 max-w-2xl mx-auto'}>
      <h1 className="text-2xl font-bold text-slate-900 mb-6">New campaign</h1>

      {/* Step indicator */}
      <div role="list" aria-label="Steps" className="flex items-center gap-2 mb-8 flex-wrap">
        {STEP_LABELS.map((label, i) => {
          const idx = i + 1;
          const active = idx === step;
          const done = idx < step;
          return (
            <div key={label} className="flex items-center gap-2">
              <div
                data-testid={`step-${idx}-indicator`}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium ${
                  active
                    ? 'bg-blue-600 text-white'
                    : done
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-slate-100 text-slate-500'
                }`}
              >
                <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold ${
                  active ? 'bg-white/20' : done ? 'bg-emerald-600 text-white' : 'bg-slate-300 text-slate-600'
                }`}>
                  {done ? '✓' : idx}
                </span>
                {label}
              </div>
              {i < STEP_LABELS.length - 1 && (
                <div className="w-8 h-px bg-slate-300" />
              )}
            </div>
          );
        })}
      </div>

      {step === 1 && (
        <Step1
          form={form}
          setForm={setForm}
          onSubmit={handleStep1Submit}
          submitting={createMutation.isPending}
          error={error}
          accounts={accounts}
          linkedinAccounts={linkedinAccounts}
          onConnectInbox={() => setShowInboxModal(true)}
        />
      )}
      {step === 2 && campaignId && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <EmbeddedSequenceBuilder
            campaignId={campaignId}
            onContinue={onSequenceDone}
            onSkip={onSequenceDone}
          />
        </div>
      )}
      {step === 3 && campaignId && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
          <LeadUpload campaignId={campaignId} onComplete={onUploadComplete} />
        </div>
      )}
      {step === 4 && campaignId && (
        <Step3 campaignId={campaignId} onComplete={onResearchComplete} />
      )}

      {showInboxModal && (
        <ConnectInboxModal
          account={null}
          onClose={() => setShowInboxModal(false)}
          onSaved={(saved) => {
            queryClient.invalidateQueries({ queryKey: ['connected-accounts'] });
            // Auto-select the freshly created account.
            setForm((f) => ({ ...f, connected_account_id: saved.id }));
            setShowInboxModal(false);
          }}
        />
      )}
    </div>
  );
}
