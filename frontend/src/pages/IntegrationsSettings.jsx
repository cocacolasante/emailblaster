import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  deleteIntegration,
  getWebhookSecret,
  registerUnipileWebhooks,
  rotateWebhookSecret,
  saveIntegration,
  testIntegration,
} from '../api/integrations.js';
import { errorDetail } from '../api/auth.js';
import { useAuth } from '../hooks/useAuth.js';
import { INTEGRATIONS_KEY, useIntegrations } from '../hooks/useIntegrations.js';
import { useToast } from '../components/Toast.jsx';
import { Badge, Button, Card, Field, Input, Modal } from '../components/ui.jsx';
import { ErrorState, LoadingCards } from '../components/states.jsx';
import { formatDateTime } from '../utils/format.js';

const WEBHOOK_PROVIDERS = ['brevo', 'unipile'];

/** Replace one provider's row in the cached list with the server's response,
 *  so the card reflects a save/test immediately (then refetch to be sure). */
function useIntegrationCache() {
  const queryClient = useQueryClient();
  return {
    put(updated) {
      if (!updated?.provider) return;
      queryClient.setQueryData(INTEGRATIONS_KEY, (rows) => (
        Array.isArray(rows)
          ? rows.map((r) => (r.provider === updated.provider ? { ...r, ...updated } : r))
          : rows
      ));
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: INTEGRATIONS_KEY });
    },
  };
}

function StatusPill({ integration }) {
  if (!integration.configured) {
    return <Badge variant="neutral" data-testid="integration-status" data-status="missing">Not connected</Badge>;
  }
  if (integration.last_test_status === 'failed') {
    return <Badge variant="danger" data-testid="integration-status" data-status="failed">Test failed</Badge>;
  }
  return <Badge variant="success" data-testid="integration-status" data-status="ok">Connected ✓</Badge>;
}

function CopyButton({ value, label = 'Copy', testId }) {
  const toast = useToast();
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      toast.success('Copied to clipboard');
    } catch {
      toast.error('Copy failed — select the text and copy it manually');
    }
  }
  return (
    <Button type="button" variant="secondary" size="sm" onClick={copy} data-testid={testId}>
      {label}
    </Button>
  );
}

// --------------------------------------------------------------------------
// Edit form
// --------------------------------------------------------------------------

function initialValues(integration) {
  const vals = {};
  for (const f of integration.fields || []) {
    vals[f.key] = f.secret ? '' : (integration.values?.[f.key] ?? '');
  }
  return vals;
}

/** Build the PUT body.  Blank secrets are omitted — the server keeps the
 *  stored value — so re-saving a non-secret field never wipes a key. */
export function buildIntegrationPayload(fields, values) {
  const payload = {};
  for (const f of fields || []) {
    const raw = values[f.key] ?? '';
    const v = typeof raw === 'string' ? raw.trim() : raw;
    if (f.secret && v === '') continue;
    payload[f.key] = v;
  }
  return payload;
}

function IntegrationForm({ integration, onDone }) {
  const toast = useToast();
  const cache = useIntegrationCache();
  const [values, setValues] = useState(() => initialValues(integration));
  const [shown, setShown] = useState({});
  const [errors, setErrors] = useState({});
  const fields = integration.fields || [];

  const mutation = useMutation({
    mutationFn: (payload) => saveIntegration(integration.provider, payload),
    onSuccess: (updated) => {
      cache.put(updated);
      cache.invalidate();
      toast.success(`${integration.label} saved`);
      onDone();
    },
  });

  function submit(e) {
    e.preventDefault();
    const nextErrors = {};
    for (const f of fields) {
      if (!f.required) continue;
      const blank = String(values[f.key] ?? '').trim() === '';
      // A saved secret may be left blank (kept server-side).
      if (blank && !(f.secret && integration.configured)) nextErrors[f.key] = 'Required';
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    mutation.mutate(buildIntegrationPayload(fields, values));
  }

  const savedSecretPlaceholder = '•••• saved — leave blank to keep';

  return (
    <form
      onSubmit={submit}
      data-testid={`integration-form-${integration.provider}`}
      className="mt-4 pt-4 border-t border-slate-100 flex flex-col gap-3"
      noValidate
    >
      {fields.map((f) => (
        <Field
          key={f.key}
          label={f.label}
          required={f.required && !(f.secret && integration.configured)}
          hint={f.help || undefined}
          error={errors[f.key]}
        >
          {(props) => (
            <div className="flex gap-2">
              <Input
                {...props}
                data-testid={`integration-field-${f.key}`}
                type={f.secret && !shown[f.key] ? 'password' : 'text'}
                autoComplete={f.secret ? 'new-password' : 'off'}
                value={values[f.key] ?? ''}
                placeholder={f.secret && integration.configured ? savedSecretPlaceholder : (f.placeholder || '')}
                onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              />
              {f.secret && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="shrink-0"
                  aria-label={shown[f.key] ? `Hide ${f.label}` : `Show ${f.label}`}
                  data-testid={`integration-toggle-${f.key}`}
                  onClick={() => setShown((s) => ({ ...s, [f.key]: !s[f.key] }))}
                >
                  {shown[f.key] ? 'Hide' : 'Show'}
                </Button>
              )}
            </div>
          )}
        </Field>
      ))}
      {mutation.isError && (
        <p role="alert" data-testid="integration-save-error" className="text-xs text-danger-600 m-0">
          {errorDetail(mutation.error, 'Save failed')}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" loading={mutation.isPending} data-testid="integration-save">
          Save
        </Button>
        <Button type="button" variant="secondary" size="sm" onClick={onDone} data-testid="integration-cancel">
          Cancel
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------
// Webhook subsection (brevo / unipile)
// --------------------------------------------------------------------------

function WebhookSection({ integration, canEdit }) {
  const toast = useToast();
  const { provider, label } = integration;
  const [secret, setSecret] = useState(null);
  const [confirmRotate, setConfirmRotate] = useState(false);

  const revealMut = useMutation({
    mutationFn: () => getWebhookSecret(provider),
    onSuccess: (data) => setSecret(data?.webhook_secret || ''),
    onError: (err) => toast.error(errorDetail(err, 'Could not load the webhook secret')),
  });

  const rotateMut = useMutation({
    mutationFn: () => rotateWebhookSecret(provider),
    onSuccess: (data) => {
      setSecret(data?.webhook_secret || '');
      setConfirmRotate(false);
      toast.success('Webhook secret rotated — update it wherever the webhook is configured');
    },
    onError: (err) => {
      setConfirmRotate(false);
      toast.error(errorDetail(err, 'Rotate failed'));
    },
  });

  const registerMut = useMutation({
    mutationFn: registerUnipileWebhooks,
    onSuccess: (data) => {
      const reg = data?.registered || [];
      toast.success(`Registered ${reg.length} Unipile webhook${reg.length === 1 ? '' : 's'}${reg.length ? ` (${reg.join(', ')})` : ''}`);
    },
    onError: (err) => toast.error(errorDetail(err, 'Webhook registration failed')),
  });

  return (
    <div data-testid={`webhook-section-${provider}`} className="mt-4 pt-4 border-t border-slate-100">
      <h4 className="text-sm font-semibold text-slate-800 m-0">Webhook</h4>
      {provider === 'brevo' && (
        <p className="text-xs text-slate-500 mt-0.5 mb-0">
          Optional. Events are also polled every 10 minutes; the webhook just makes them real-time.
        </p>
      )}
      {provider === 'unipile' && (
        <p className="text-xs text-slate-500 mt-0.5 mb-0">
          Unipile posts replies, connection accepts and account status changes here.
        </p>
      )}

      <div className="mt-3 flex flex-col gap-2 text-xs">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-slate-500 w-20 shrink-0">URL</span>
          <code data-testid="webhook-url" className="font-mono text-slate-800 bg-slate-50 border border-slate-200 rounded px-2 py-1 break-all">
            {integration.webhook_url}
          </code>
          <CopyButton value={integration.webhook_url} testId="copy-webhook-url" />
        </div>
        {integration.webhook_header && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-500 w-20 shrink-0">Auth header</span>
            <code data-testid="webhook-header" className="font-mono text-slate-800">{integration.webhook_header}</code>
          </div>
        )}
        {secret !== null && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-500 w-20 shrink-0">Secret</span>
            <code data-testid="webhook-secret" className="font-mono text-slate-800 bg-slate-50 border border-slate-200 rounded px-2 py-1 break-all">
              {secret}
            </code>
            <CopyButton value={secret} testId="copy-webhook-secret" />
            <Button type="button" variant="ghost" size="sm" onClick={() => setSecret(null)}>Hide</Button>
          </div>
        )}
      </div>

      {canEdit && (
        <div className="mt-3 flex gap-2 flex-wrap">
          {secret === null && (
            <Button
              type="button" variant="secondary" size="sm"
              loading={revealMut.isPending}
              onClick={() => revealMut.mutate()}
              data-testid="reveal-webhook-secret"
            >
              Reveal secret
            </Button>
          )}
          <Button
            type="button" variant="secondary" size="sm"
            onClick={() => setConfirmRotate(true)}
            data-testid="rotate-webhook-secret"
          >
            Rotate secret
          </Button>
          {provider === 'unipile' && (
            <Button
              type="button" size="sm"
              loading={registerMut.isPending}
              onClick={() => registerMut.mutate()}
              data-testid="register-unipile-webhooks"
            >
              Register webhooks automatically
            </Button>
          )}
        </div>
      )}

      <Modal
        open={confirmRotate}
        onClose={() => setConfirmRotate(false)}
        title="Rotate webhook secret?"
        size="sm"
        testId="rotate-secret-modal"
        footer={(
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmRotate(false)}>Cancel</Button>
            <Button
              variant="danger" size="sm"
              loading={rotateMut.isPending}
              onClick={() => rotateMut.mutate()}
              data-testid="confirm-rotate-secret"
            >
              Rotate secret
            </Button>
          </>
        )}
      >
        <p className="text-sm text-slate-600 m-0">
          The current secret stops working immediately. Deliveries from {label} will be
          rejected until you update the <code>{integration.webhook_header || 'auth'}</code> header value
          {provider === 'unipile' ? ' — click "Register webhooks automatically" afterwards to do that for you.' : ' in its dashboard.'}
        </p>
      </Modal>
    </div>
  );
}

// --------------------------------------------------------------------------
// Provider card
// --------------------------------------------------------------------------

function IntegrationCard({ integration, canEdit }) {
  const toast = useToast();
  const cache = useIntegrationCache();
  const [editing, setEditing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [testError, setTestError] = useState(null);
  const { provider, label } = integration;

  const testMut = useMutation({
    mutationFn: () => testIntegration(provider),
    onMutate: () => setTestError(null),
    onSuccess: (updated) => {
      cache.put(updated);
      if (updated?.last_test_status === 'ok') toast.success(`${label} connection OK`);
    },
    // A 409 (not configured) or network failure — surface the detail inline.
    onError: (err) => setTestError(errorDetail(err, 'Test failed')),
    onSettled: () => cache.invalidate(),
  });

  const removeMut = useMutation({
    mutationFn: () => deleteIntegration(provider),
    onSuccess: () => {
      setConfirmRemove(false);
      setEditing(false);
      toast.success(`${label} removed`);
    },
    onError: (err) => {
      setConfirmRemove(false);
      toast.error(errorDetail(err, 'Remove failed'));
    },
    onSettled: () => cache.invalidate(),
  });

  const failed = integration.configured && integration.last_test_status === 'failed';
  const showWebhook = integration.configured
    && WEBHOOK_PROVIDERS.includes(provider)
    && !!integration.webhook_url;

  return (
    <Card className="p-5" data-testid={`integration-card-${provider}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-base font-semibold text-slate-900 m-0">{label}</h3>
            <StatusPill integration={integration} />
          </div>
          {integration.description && (
            <p className="text-xs text-slate-500 mt-0.5 mb-0">{integration.description}</p>
          )}
        </div>
        {integration.docs_url && (
          <a
            href={integration.docs_url}
            target="_blank"
            rel="noreferrer"
            className="text-xs font-medium text-brand-600 hover:text-brand-800 shrink-0"
          >
            Docs ↗
          </a>
        )}
      </div>

      {integration.configured && (
        <div className="mt-3 text-xs text-slate-500 flex flex-col gap-0.5">
          {integration.preview && (
            <div>
              Key: <span data-testid="integration-preview" className="font-mono text-slate-700">{integration.preview}</span>
            </div>
          )}
          <div>
            Last tested: <span className="text-slate-700">{integration.last_tested_at ? formatDateTime(integration.last_tested_at) : 'never'}</span>
          </div>
        </div>
      )}

      {testMut.isPending && (
        <p data-testid="integration-testing" className="text-xs text-slate-500 mt-2 mb-0">Testing connection…</p>
      )}
      {!testMut.isPending && (testError || (failed && integration.last_test_error)) && (
        <p role="alert" data-testid="integration-test-error" className="text-xs text-danger-600 mt-2 mb-0">
          {testError || integration.last_test_error}
        </p>
      )}
      {!testMut.isPending && testMut.isSuccess && integration.last_test_status === 'ok' && (
        <p data-testid="integration-test-ok" className="text-xs text-success-700 mt-2 mb-0">Connection OK</p>
      )}

      {canEdit && !editing && (
        <div className="mt-4 flex gap-2 flex-wrap">
          <Button
            size="sm"
            variant={integration.configured ? 'secondary' : 'primary'}
            onClick={() => setEditing(true)}
            data-testid={`integration-edit-${provider}`}
          >
            {integration.configured ? 'Edit' : 'Connect'}
          </Button>
          {integration.configured && (
            <>
              <Button
                size="sm" variant="secondary"
                loading={testMut.isPending}
                onClick={() => testMut.mutate()}
                data-testid={`integration-test-${provider}`}
              >
                Test connection
              </Button>
              <Button
                size="sm" variant="ghost"
                className="text-danger-600 hover:bg-danger-50"
                onClick={() => setConfirmRemove(true)}
                data-testid={`integration-remove-${provider}`}
              >
                Remove
              </Button>
            </>
          )}
        </div>
      )}

      {canEdit && editing && (
        <IntegrationForm integration={integration} onDone={() => setEditing(false)} />
      )}

      {showWebhook && <WebhookSection integration={integration} canEdit={canEdit} />}

      <Modal
        open={confirmRemove}
        onClose={() => setConfirmRemove(false)}
        title={`Remove ${label}?`}
        size="sm"
        testId="remove-integration-modal"
        footer={(
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmRemove(false)}>Cancel</Button>
            <Button
              variant="danger" size="sm"
              loading={removeMut.isPending}
              onClick={() => removeMut.mutate()}
              data-testid="confirm-remove-integration"
            >
              Remove
            </Button>
          </>
        )}
      >
        <p className="text-sm text-slate-600 m-0">
          The stored credentials are deleted. Features that use {label} stop working in this
          workspace until it's connected again.
        </p>
      </Modal>
    </Card>
  );
}

export default function IntegrationsTab() {
  const { isManager } = useAuth();
  const { integrations, isLoading, error, refetch } = useIntegrations();

  return (
    <div data-testid="integrations-tab">
      <h2 className="text-lg font-semibold text-slate-900 mb-1">Integrations</h2>
      <p className="text-sm text-slate-500 mt-0 mb-4">
        Each workspace uses its own API keys. Keys are encrypted and never shown again after saving.
      </p>
      {!isManager && (
        <p
          data-testid="integrations-readonly-note"
          className="mb-4 bg-slate-50 border border-slate-200 text-slate-600 text-sm rounded-lg px-4 py-3"
        >
          Only workspace owners and admins can change integrations.
        </p>
      )}
      {isLoading && <LoadingCards count={3} />}
      {error && (
        <ErrorState message={errorDetail(error, 'Failed to load integrations')} onRetry={refetch} />
      )}
      {!isLoading && !error && (
        <div className="flex flex-col gap-4">
          {integrations.map((i) => (
            <IntegrationCard key={i.provider} integration={i} canEdit={isManager} />
          ))}
        </div>
      )}
    </div>
  );
}
