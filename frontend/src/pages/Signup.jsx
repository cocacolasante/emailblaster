import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { register, errorDetail } from '../api/auth.js';
import { adoptSession } from '../hooks/useAuth.js';
import AuthLayout, { FormError } from '../components/AuthLayout.jsx';
import { Button, Field, Input } from '../components/ui.jsx';
import { useAuthConfig } from './Login.jsx';

export const MIN_PASSWORD = 8;

function signupError(err) {
  const status = err?.response?.status;
  if (status === 409) return 'An account with this email already exists. Try signing in instead.';
  if (status === 403) return 'Signup is currently invite-only.';
  return errorDetail(err, 'Could not create your account. Please try again.');
}

const signInFooter = (
  <>
    Already have an account?{' '}
    <Link to="/login" className="font-medium text-brand-700 hover:text-brand-600">Sign in</Link>
  </>
);

export default function Signup() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useAuthConfig();
  const [form, setForm] = useState({ name: '', workspace_name: '', email: '', password: '' });
  const [localError, setLocalError] = useState(null);

  const mutation = useMutation({
    mutationFn: register,
    onSuccess: (me) => {
      adoptSession(queryClient, me);
      navigate('/', { replace: true });
    },
  });

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  function onSubmit(e) {
    e.preventDefault();
    setLocalError(null);
    if (form.password.length < MIN_PASSWORD) {
      setLocalError(`Password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    const payload = { email: form.email.trim(), password: form.password };
    if (form.name.trim()) payload.name = form.name.trim();
    if (form.workspace_name.trim()) payload.workspace_name = form.workspace_name.trim();
    mutation.mutate(payload);
  }

  if (isLoading) {
    return <AuthLayout testId="signup-page" title="Create your account" subtitle="Loading…" />;
  }

  if (!config?.allow_signup) {
    return (
      <AuthLayout testId="signup-page" title="Create your account" footer={signInFooter}>
        <p className="text-sm text-slate-600 m-0" data-testid="signup-disabled">
          Signup is currently invite-only. Ask a workspace admin to send you an invite link.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      testId="signup-page"
      title="Create your account"
      subtitle="Start a new workspace for your team."
      footer={signInFooter}
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <FormError>{localError || (mutation.isError ? signupError(mutation.error) : null)}</FormError>
        <Field label="Your name" optional>
          {(p) => <Input {...p} autoComplete="name" value={form.name} onChange={set('name')} data-testid="signup-name" />}
        </Field>
        <Field label="Workspace name" optional hint="Usually your company or team name.">
          {(p) => <Input {...p} autoComplete="organization" value={form.workspace_name} onChange={set('workspace_name')} data-testid="signup-workspace" />}
        </Field>
        <Field label="Email" required>
          {(p) => <Input {...p} type="email" autoComplete="email" value={form.email} onChange={set('email')} data-testid="signup-email" />}
        </Field>
        <Field label="Password" required hint={`At least ${MIN_PASSWORD} characters.`}>
          {(p) => <Input {...p} type="password" autoComplete="new-password" value={form.password} onChange={set('password')} data-testid="signup-password" />}
        </Field>
        <Button
          type="submit"
          className="w-full"
          loading={mutation.isPending}
          disabled={!form.email || !form.password}
          data-testid="signup-submit"
        >
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
