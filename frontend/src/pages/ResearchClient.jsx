import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { researchClient } from '../api/researchClient.js';

const SENDER_NAME_KEY = 'researchClient.senderName';

const DEFAULT_LIMIT = {
  linkedin_dm: 300,
  email: 600,
};

const TONE_PRESETS = ['professional', 'warm', 'direct', 'casual'];


function CharCountBadge({ count, limit }) {
  const over = count > limit;
  return (
    <span
      data-testid="char-count"
      className={`text-xs font-medium ${over ? 'text-red-600' : 'text-slate-500'}`}
    >
      {count} / {limit} chars
    </span>
  );
}


function CopyButton({ text, label = 'Copy' }) {
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  async function onCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Browser denied clipboard access; nothing graceful to do.
    }
  }
  return (
    <button
      type="button"
      onClick={onCopy}
      className="px-3 py-1.5 text-sm font-medium bg-slate-100 hover:bg-slate-200 rounded-md text-slate-700"
    >
      {copied ? 'Copied!' : label}
    </button>
  );
}


function ResultPanel({ result, outputKind, charLimit }) {
  if (!result) return null;
  const { profile, research, subject, body, char_count, duration_ms } = result;
  const fullText = subject ? `Subject: ${subject}\n\n${body}` : body;

  return (
    <div data-testid="result-panel" className="space-y-5">
      <div className="bg-slate-50 border border-slate-200 rounded-lg p-4">
        <div className="flex items-baseline justify-between mb-2">
          <h3 className="text-base font-semibold text-slate-800">
            {profile.first_name} {profile.last_name || ''}
          </h3>
          <span
            className={`text-xs px-2 py-0.5 rounded-full font-medium ${
              profile.quality === 'rich'
                ? 'bg-emerald-100 text-emerald-700'
                : profile.quality === 'partial'
                ? 'bg-amber-100 text-amber-700'
                : 'bg-slate-200 text-slate-600'
            }`}
          >
            {profile.quality} research
          </span>
        </div>
        <p className="text-sm text-slate-600">
          {[profile.job_title, profile.company].filter(Boolean).join(' · ') || '—'}
        </p>
        {profile.headline && (
          <p className="text-xs text-slate-500 mt-1">{profile.headline}</p>
        )}
        {!profile.found && (
          <p className="text-xs text-amber-700 mt-2">
            Web research didn't return high-confidence identity signals. The
            generated message uses the URL slug as a name guess and skips
            personalization.
          </p>
        )}
      </div>

      {subject && (
        <div>
          <label className="block text-xs font-semibold text-slate-600 mb-1">
            {outputKind === 'email' ? 'Subject' : 'Subject (LinkedIn thread topic)'}
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              readOnly
              value={subject}
              data-testid="result-subject"
              className="flex-1 px-3 py-2 border border-slate-300 rounded-md text-sm bg-white"
            />
            <CopyButton text={subject} />
          </div>
        </div>
      )}

      <div>
        <div className="flex items-baseline justify-between mb-1">
          <label className="block text-xs font-semibold text-slate-600">
            {outputKind === 'email' ? 'Body' : 'Message'}
          </label>
          <CharCountBadge count={char_count} limit={charLimit} />
        </div>
        <textarea
          readOnly
          value={body}
          rows={outputKind === 'email' ? 10 : 6}
          data-testid="result-body"
          className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm bg-white font-mono leading-relaxed"
        />
        <div className="mt-2 flex items-center gap-2">
          <CopyButton text={body} label="Copy message" />
          <CopyButton text={fullText} label="Copy with subject" />
          <span className="text-xs text-slate-400 ml-auto">
            generated in {(duration_ms / 1000).toFixed(1)}s
          </span>
        </div>
      </div>

      <details className="text-sm text-slate-600">
        <summary className="cursor-pointer text-xs font-semibold text-slate-500 uppercase tracking-wider">
          Research details
        </summary>
        <div className="mt-3 space-y-2 pl-2 border-l-2 border-slate-200">
          {research.person_news?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-slate-500">Person news</div>
              <ul className="list-disc list-inside text-xs">
                {research.person_news.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </div>
          )}
          {research.company_news?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-slate-500">Company news</div>
              <ul className="list-disc list-inside text-xs">
                {research.company_news.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </div>
          )}
          {research.company_description && (
            <div>
              <div className="text-xs font-semibold text-slate-500">Company</div>
              <p className="text-xs">{research.company_description}</p>
            </div>
          )}
          {research.industry && (
            <p className="text-xs"><span className="font-semibold">Industry:</span> {research.industry}</p>
          )}
          {research.company_website && (
            <p className="text-xs"><span className="font-semibold">Website:</span> {research.company_website}</p>
          )}
        </div>
      </details>
    </div>
  );
}


export default function ResearchClient() {
  const [linkedinUrl, setLinkedinUrl] = useState('');
  const [goal, setGoal] = useState('');
  const [tone, setTone] = useState('professional');
  const [senderName, setSenderName] = useState('');
  const [researchMode, setResearchMode] = useState('fast');
  const [outputKind, setOutputKind] = useState('linkedin_dm');
  const [charLimit, setCharLimit] = useState(DEFAULT_LIMIT.linkedin_dm);
  const [touchedLimit, setTouchedLimit] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  // Restore sender name from prior session.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(SENDER_NAME_KEY);
      if (saved) setSenderName(saved);
    } catch { /* noop */ }
  }, []);

  // Auto-pick a sensible char limit when the output kind changes, unless
  // the user has explicitly overridden it.
  useEffect(() => {
    if (!touchedLimit) {
      setCharLimit(DEFAULT_LIMIT[outputKind]);
    }
  }, [outputKind, touchedLimit]);

  const mutation = useMutation({
    mutationFn: researchClient,
    onSuccess: () => {
      try {
        if (senderName) localStorage.setItem(SENDER_NAME_KEY, senderName);
      } catch { /* noop */ }
    },
  });

  // Drive the elapsed-time counter while the request is in flight.
  useEffect(() => {
    if (!mutation.isPending) {
      setElapsed(0);
      return undefined;
    }
    const started = Date.now();
    const tick = setInterval(() => setElapsed((Date.now() - started) / 1000), 250);
    return () => clearInterval(tick);
  }, [mutation.isPending]);

  const expectedSeconds = researchMode === 'deep' ? 45 : 10;
  const error = mutation.error;
  const errorMessage = useMemo(() => {
    if (!error) return null;
    const detail = error?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    return String(error.message || 'Request failed');
  }, [error]);

  function onSubmit(e) {
    e.preventDefault();
    if (!linkedinUrl.trim() || !goal.trim()) return;
    mutation.mutate({
      linkedin_url: linkedinUrl.trim(),
      goal: goal.trim(),
      tone: tone.trim() || 'professional',
      sender_name: senderName.trim(),
      research_mode: researchMode,
      output_kind: outputKind,
      char_limit: Number(charLimit) || DEFAULT_LIMIT[outputKind],
    });
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Research a client</h1>
        <p className="text-sm text-slate-500 mt-1">
          One-off outreach: paste a LinkedIn URL, describe the goal, and get a
          personalized message you can send manually. No ghost-view
          notifications — research uses public web search only.
        </p>
      </header>

      <form onSubmit={onSubmit} className="space-y-4 bg-white p-6 rounded-lg border border-slate-200">
        <div>
          <label className="block text-sm font-semibold text-slate-700 mb-1">
            LinkedIn URL <span className="text-red-500">*</span>
          </label>
          <input
            type="url"
            placeholder="https://www.linkedin.com/in/their-slug/"
            value={linkedinUrl}
            onChange={(e) => setLinkedinUrl(e.target.value)}
            required
            data-testid="input-linkedin-url"
            className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm"
          />
        </div>

        <div>
          <label className="block text-sm font-semibold text-slate-700 mb-1">
            Goal of outreach <span className="text-red-500">*</span>
          </label>
          <textarea
            placeholder="e.g. Book a 15-min intro call about scaling onboarding for B2B SaaS"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            required
            rows={3}
            data-testid="input-goal"
            className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">
              Research depth
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setResearchMode('fast')}
                data-testid="mode-fast"
                aria-pressed={researchMode === 'fast'}
                className={`flex-1 px-3 py-2 text-sm rounded-md border ${
                  researchMode === 'fast'
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                Quick (~10s)
              </button>
              <button
                type="button"
                onClick={() => setResearchMode('deep')}
                data-testid="mode-deep"
                aria-pressed={researchMode === 'deep'}
                className={`flex-1 px-3 py-2 text-sm rounded-md border ${
                  researchMode === 'deep'
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                Deep (~45s)
              </button>
            </div>
          </div>

          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">
              Output
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setOutputKind('linkedin_dm')}
                data-testid="output-dm"
                aria-pressed={outputKind === 'linkedin_dm'}
                className={`flex-1 px-3 py-2 text-sm rounded-md border ${
                  outputKind === 'linkedin_dm'
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                LinkedIn DM
              </button>
              <button
                type="button"
                onClick={() => setOutputKind('email')}
                data-testid="output-email"
                aria-pressed={outputKind === 'email'}
                className={`flex-1 px-3 py-2 text-sm rounded-md border ${
                  outputKind === 'email'
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                Email
              </button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">
              Character limit
            </label>
            <input
              type="number"
              min={50}
              max={5000}
              value={charLimit}
              onChange={(e) => {
                setTouchedLimit(true);
                setCharLimit(e.target.value);
              }}
              data-testid="input-char-limit"
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm"
            />
            <p className="text-xs text-slate-400 mt-1">
              Default {DEFAULT_LIMIT[outputKind]} for {outputKind === 'email' ? 'email' : 'DM'}
            </p>
          </div>
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">
              Tone
            </label>
            <input
              type="text"
              value={tone}
              onChange={(e) => setTone(e.target.value)}
              list="tone-presets"
              data-testid="input-tone"
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm"
            />
            <datalist id="tone-presets">
              {TONE_PRESETS.map((t) => <option key={t} value={t} />)}
            </datalist>
          </div>
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">
              Sender name
            </label>
            <input
              type="text"
              value={senderName}
              onChange={(e) => setSenderName(e.target.value)}
              placeholder="Your name (optional)"
              data-testid="input-sender-name"
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm"
            />
          </div>
        </div>

        <div className="flex items-center justify-between pt-2">
          {mutation.isPending ? (
            <p className="text-sm text-slate-600">
              Researching… <span className="font-mono">{elapsed.toFixed(1)}s</span>
              <span className="text-slate-400"> / ~{expectedSeconds}s</span>
            </p>
          ) : <span />}
          <button
            type="submit"
            disabled={mutation.isPending || !linkedinUrl.trim() || !goal.trim()}
            data-testid="submit-research"
            className="px-5 py-2 bg-slate-900 text-white text-sm font-semibold rounded-md hover:bg-slate-800 disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {mutation.isPending ? 'Working…' : 'Generate outreach'}
          </button>
        </div>

        {errorMessage && !mutation.isPending && (
          <p data-testid="error-message" className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-3">
            {errorMessage}
          </p>
        )}
      </form>

      {mutation.data && (
        <div className="mt-8 bg-white p-6 rounded-lg border border-slate-200">
          <ResultPanel
            result={mutation.data}
            outputKind={outputKind}
            charLimit={Number(charLimit) || DEFAULT_LIMIT[outputKind]}
          />
        </div>
      )}
    </div>
  );
}
