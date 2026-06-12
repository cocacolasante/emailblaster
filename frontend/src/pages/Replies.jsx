import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listReplies } from '../api/agent.js';
import { convertLead } from '../api/crm.js';
import { useToast } from '../components/Toast.jsx';

const SENTIMENT_STYLES = {
  positive: 'bg-emerald-100 text-emerald-700',
  neutral: 'bg-slate-100 text-slate-600',
  negative: 'bg-red-100 text-red-700',
};

function SentimentBadge({ sentiment }) {
  const style = SENTIMENT_STYLES[sentiment] || SENTIMENT_STYLES.neutral;
  return (
    <span
      data-testid="sentiment-badge"
      className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${style}`}
    >
      {sentiment || 'unclassified'}
    </span>
  );
}

function ReplyRow({ item }) {
  const [showDraft, setShowDraft] = useState(false);
  const toast = useToast();
  const queryClient = useQueryClient();

  const convertMutation = useMutation({
    mutationFn: () => convertLead(item.lead_id),
    onSuccess: () => {
      toast.success('Lead converted to opportunity');
      queryClient.invalidateQueries({ queryKey: ['agent-replies'] });
      queryClient.invalidateQueries({ queryKey: ['crm-opportunities'] });
    },
    onError: (err) => {
      toast.error(err?.response?.data?.detail || 'Convert failed');
    },
  });

  const copyDraft = async () => {
    try {
      await navigator.clipboard.writeText(item.draft_body);
      toast.success('Draft copied to clipboard');
    } catch {
      toast.error('Copy failed');
    }
  };

  return (
    <div
      data-testid={`reply-row-${item.activity_id}`}
      className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-2"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <SentimentBadge sentiment={item.sentiment} />
            <span className="font-semibold text-slate-800 truncate">
              {item.lead_name || item.lead_email || 'Unknown sender'}
            </span>
            {item.lead_company && (
              <span className="text-sm text-slate-500">· {item.lead_company}</span>
            )}
            {item.converted && (
              <span
                data-testid="converted-pill"
                className="px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700"
              >
                Converted
              </span>
            )}
          </div>
          <p className="text-sm text-slate-600 mt-1 mb-0 truncate">{item.subject}</p>
          {item.body_preview && (
            <p className="text-sm text-slate-500 mt-1 mb-0 line-clamp-2">{item.body_preview}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <span className="text-xs text-slate-400">
            {new Date(item.occurred_at).toLocaleString()}
          </span>
          <div className="flex gap-2">
            {item.draft_body && (
              <button
                type="button"
                data-testid={`view-draft-btn-${item.activity_id}`}
                onClick={() => setShowDraft((s) => !s)}
                className="text-sm px-3 py-1.5 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50"
              >
                {showDraft ? 'Hide draft' : 'View draft'}
              </button>
            )}
            {item.convert_eligible && (
              <button
                type="button"
                data-testid={`convert-btn-${item.activity_id}`}
                onClick={() => convertMutation.mutate()}
                disabled={convertMutation.isPending}
                className="text-sm px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {convertMutation.isPending ? 'Converting…' : 'Convert'}
              </button>
            )}
          </div>
        </div>
      </div>
      {showDraft && item.draft_body && (
        <div
          data-testid={`draft-panel-${item.activity_id}`}
          className="bg-slate-50 border border-slate-200 rounded-lg p-3"
        >
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
              Suggested reply (AI draft — never sent automatically)
            </span>
            <button
              type="button"
              onClick={copyDraft}
              className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
            >
              Copy
            </button>
          </div>
          <p className="text-sm text-slate-700 whitespace-pre-wrap mb-0">{item.draft_body}</p>
        </div>
      )}
    </div>
  );
}

export default function Replies() {
  const [sentiment, setSentiment] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['agent-replies', sentiment],
    queryFn: () => listReplies(sentiment ? { sentiment } : {}),
  });

  const items = data?.items || [];

  return (
    <div data-testid="replies-page" className="p-8 max-w-4xl">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-900 m-0">Replies</h1>
        <select
          data-testid="sentiment-filter"
          value={sentiment}
          onChange={(e) => setSentiment(e.target.value)}
          className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          <option value="">All sentiments</option>
          <option value="positive">Positive</option>
          <option value="neutral">Neutral</option>
          <option value="negative">Negative</option>
        </select>
      </div>
      <p className="text-slate-500 text-sm mt-0 mb-6">
        Inbound replies the agent classified — triage, convert, and respond.
        The agent never replies to prospects or converts leads on its own.
      </p>

      {isLoading && <p className="text-slate-400">Loading…</p>}
      {!isLoading && items.length === 0 && (
        <div
          data-testid="replies-empty"
          className="bg-white border border-dashed border-slate-300 rounded-xl p-10 text-center text-slate-400"
        >
          No replies yet. When a prospect answers one of your campaigns, the
          agent classifies it and it lands here.
        </div>
      )}
      <div className="flex flex-col gap-3">
        {items.map((item) => (
          <ReplyRow key={item.activity_id} item={item} />
        ))}
      </div>
      {(data?.total ?? 0) > items.length && (
        <p className="text-xs text-slate-400 mt-4">
          Showing {items.length} of {data.total}.
        </p>
      )}
      <p className="text-xs text-slate-400 mt-6">
        Looking for what the agent did and why? Every autonomous action is
        audited — see the <Link to="/settings" className="text-indigo-600">Agent panel in Settings</Link>.
      </p>
    </div>
  );
}
