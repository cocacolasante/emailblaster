import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updateMe, errorDetail } from '../api/auth.js';
import { AUTH_ME_KEY, useAuth } from '../hooks/useAuth.js';
import { useToast } from '../components/Toast.jsx';
import { Button, Card, Field, Input, Toggle } from '../components/ui.jsx';
import { MIN_PASSWORD } from './Signup.jsx';

function InlineError({ children, testId }) {
  if (!children) return null;
  return <p role="alert" data-testid={testId} className="text-xs text-danger-600 mt-2 mb-0">{children}</p>;
}

/** PATCH /auth/me and write the fresh MeResponse into the shared cache. */
function useUpdateMe(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateMe,
    ...options,
    onSuccess: (me, vars, ctx) => {
      queryClient.setQueryData(AUTH_ME_KEY, me);
      options.onSuccess?.(me, vars, ctx);
    },
  });
}

function NameCard() {
  const { user } = useAuth();
  const toast = useToast();
  const [name, setName] = useState(user?.name || '');
  useEffect(() => { setName(user?.name || ''); }, [user?.name]);
  const mutation = useUpdateMe({ onSuccess: () => toast.success('Profile updated') });
  const dirty = name.trim() !== (user?.name || '');

  return (
    <Card className="p-5" data-testid="profile-name-card">
      <h2 className="text-base font-semibold text-slate-900 mt-0 mb-3">Profile</h2>
      <form className="flex items-end gap-3" onSubmit={(e) => { e.preventDefault(); if (dirty) mutation.mutate({ name: name.trim() }); }}>
        <Field label="Name" className="flex-1">
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} data-testid="profile-name" />}
        </Field>
        <Button type="submit" disabled={!dirty} loading={mutation.isPending} data-testid="profile-name-save">Save</Button>
      </form>
      <p className="text-xs text-slate-500 mt-3 mb-0">Signed in as <span className="font-medium text-slate-700">{user?.email}</span></p>
      <InlineError testId="profile-name-error">{mutation.isError ? errorDetail(mutation.error) : null}</InlineError>
    </Card>
  );
}

function PasswordCard() {
  const toast = useToast();
  const [form, setForm] = useState({ current: '', next: '', confirm: '' });
  const [localError, setLocalError] = useState(null);
  const mutation = useUpdateMe({
    onSuccess: () => {
      setForm({ current: '', next: '', confirm: '' });
      toast.success('Password changed');
    },
  });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  function onSubmit(e) {
    e.preventDefault();
    setLocalError(null);
    mutation.reset();
    if (form.next.length < MIN_PASSWORD) {
      setLocalError(`New password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    if (form.next !== form.confirm) {
      setLocalError("New passwords don't match.");
      return;
    }
    mutation.mutate({ current_password: form.current, new_password: form.next });
  }

  return (
    <Card className="p-5" data-testid="profile-password-card">
      <h2 className="text-base font-semibold text-slate-900 mt-0 mb-3">Change password</h2>
      <form className="space-y-3 max-w-sm" onSubmit={onSubmit} noValidate>
        <Field label="Current password">
          {(p) => <Input {...p} type="password" autoComplete="current-password" value={form.current} onChange={set('current')} data-testid="profile-current-password" />}
        </Field>
        <Field label="New password" hint={`At least ${MIN_PASSWORD} characters.`}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.next} onChange={set('next')} data-testid="profile-new-password" />}
        </Field>
        <Field label="Confirm new password">
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} data-testid="profile-confirm-password" />}
        </Field>
        <Button type="submit" loading={mutation.isPending}
          disabled={!form.current || !form.next || !form.confirm} data-testid="profile-password-save">
          Change password
        </Button>
      </form>
      <InlineError testId="profile-password-error">
        {localError || (mutation.isError ? errorDetail(mutation.error) : null)}
      </InlineError>
    </Card>
  );
}

function NotificationsCard() {
  const { me } = useAuth();
  const mutation = useUpdateMe();
  const rows = [
    {
      key: 'notify_email_enabled',
      label: 'Email alerts',
      hint: 'Get an email when something needs your attention (replies, failures, tasks assigned to you).',
    },
    {
      key: 'digest_enabled',
      label: 'Daily digest',
      hint: 'A once-a-day summary of campaign activity in this workspace.',
    },
  ];

  return (
    <Card className="p-5" data-testid="profile-notifications-card">
      <h2 className="text-base font-semibold text-slate-900 mt-0 mb-3">Notifications</h2>
      <div className="space-y-4">
        {rows.map((r) => (
          <div key={r.key} className="flex items-start justify-between gap-4">
            <div>
              <div className="text-sm font-medium text-slate-800">{r.label}</div>
              <div className="text-xs text-slate-500">{r.hint}</div>
            </div>
            <Toggle
              label={r.label}
              checked={!!me?.[r.key]}
              disabled={mutation.isPending}
              onChange={(v) => mutation.mutate({ [r.key]: v })}
              data-testid={`profile-toggle-${r.key}`}
            />
          </div>
        ))}
      </div>
      <InlineError testId="profile-notifications-error">{mutation.isError ? errorDetail(mutation.error) : null}</InlineError>
    </Card>
  );
}

export default function ProfileTab() {
  return (
    <div className="space-y-5" data-testid="profile-tab">
      <NameCard />
      <PasswordCard />
      <NotificationsCard />
    </div>
  );
}
