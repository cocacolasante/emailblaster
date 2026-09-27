import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { acceptInvite, getInvite, errorDetail } from '../api/auth.js';
import { adoptSession } from '../hooks/useAuth.js';
import AuthLayout, { FormError } from '../components/AuthLayout.jsx';
import { Button, Field, Input } from '../components/ui.jsx';
import { MIN_PASSWORD } from './Signup.jsx';

const linkCls = 'font-medium text-brand-700 hover:text-brand-600';

const ROLE_LABEL = { owner: 'an owner', admin: 'an admin', member: 'a member' };

function InvalidInvite() {
  return (
    <AuthLayout
      testId="accept-invite-page"
      title="Invite not found"
      footer={<Link to="/login" className={linkCls}>Go to sign in</Link>}
    >
      <p className="text-sm text-slate-600 m-0" data-testid="invite-invalid">
        This invite link is invalid, has expired, or was already used. Ask a
        workspace admin to send you a new one.
      </p>
    </AuthLayout>
  );
}

export default function AcceptInvite() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [localError, setLocalError] = useState(null);

  const inviteQuery = useQuery({
    queryKey: ['invite-preview', token],
    queryFn: () => getInvite(token),
    enabled: !!token,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: acceptInvite,
    onSuccess: (me) => {
      adoptSession(queryClient, me);
      navigate('/', { replace: true });
    },
  });

  if (!token) return <InvalidInvite />;
  if (inviteQuery.isLoading) {
    return <AuthLayout testId="accept-invite-page" title="Loading invite…" />;
  }
  if (inviteQuery.isError) {
    if (inviteQuery.error?.response?.status === 404) return <InvalidInvite />;
    return (
      <AuthLayout testId="accept-invite-page" title="Couldn't load invite">
        <FormError>{errorDetail(inviteQuery.error)}</FormError>
        <Button variant="secondary" onClick={() => inviteQuery.refetch()}>Retry</Button>
      </AuthLayout>
    );
  }

  const invite = inviteQuery.data;
  const existing = !!invite.user_exists;

  function onSubmit(e) {
    e.preventDefault();
    setLocalError(null);
    if (!existing) {
      if (password.length < MIN_PASSWORD) {
        setLocalError(`Password must be at least ${MIN_PASSWORD} characters.`);
        return;
      }
      if (password !== confirm) {
        setLocalError("Passwords don't match.");
        return;
      }
    }
    const payload = { token, password };
    if (!existing && name.trim()) payload.name = name.trim();
    mutation.mutate(payload);
  }

  const serverError = mutation.isError
    ? (existing && mutation.error?.response?.status === 401
      ? 'Incorrect password for this account.'
      : errorDetail(mutation.error, 'Could not accept the invite.'))
    : null;

  return (
    <AuthLayout
      testId="accept-invite-page"
      title={`Join ${invite.workspace_name}`}
      subtitle={(
        <>
          You&apos;ve been invited to join as {ROLE_LABEL[invite.role] || invite.role}.
        </>
      )}
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <div className="text-xs text-slate-500">
          Invite for <span className="font-medium text-slate-800" data-testid="invite-email">{invite.email}</span>
        </div>
        <FormError>{localError || serverError}</FormError>
        {existing ? (
          <>
            <p className="text-sm text-slate-600 m-0" data-testid="invite-existing">
              Sign in with your existing password to accept.
            </p>
            <Field label="Password">
              {(p) => (
                <Input {...p} type="password" autoComplete="current-password" autoFocus value={password}
                  onChange={(e) => setPassword(e.target.value)} data-testid="invite-password" />
              )}
            </Field>
          </>
        ) : (
          <>
            <Field label="Your name" optional>
              {(p) => (
                <Input {...p} autoComplete="name" autoFocus value={name}
                  onChange={(e) => setName(e.target.value)} data-testid="invite-name" />
              )}
            </Field>
            <Field label="Create a password" hint={`At least ${MIN_PASSWORD} characters.`}>
              {(p) => (
                <Input {...p} type="password" autoComplete="new-password" value={password}
                  onChange={(e) => setPassword(e.target.value)} data-testid="invite-password" />
              )}
            </Field>
            <Field label="Confirm password">
              {(p) => (
                <Input {...p} type="password" autoComplete="new-password" value={confirm}
                  onChange={(e) => setConfirm(e.target.value)} data-testid="invite-confirm" />
              )}
            </Field>
          </>
        )}
        <Button type="submit" className="w-full" loading={mutation.isPending}
          disabled={!password || (!existing && !confirm)} data-testid="invite-submit">
          {existing ? 'Sign in & join' : 'Create account & join'}
        </Button>
      </form>
    </AuthLayout>
  );
}
