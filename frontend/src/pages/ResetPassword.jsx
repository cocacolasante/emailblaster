import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { resetPassword, errorDetail } from '../api/auth.js';
import AuthLayout, { FormError } from '../components/AuthLayout.jsx';
import { Button, Field, Input } from '../components/ui.jsx';
import { MIN_PASSWORD } from './Signup.jsx';

const linkCls = 'font-medium text-brand-700 hover:text-brand-600';

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [localError, setLocalError] = useState(null);

  const mutation = useMutation({ mutationFn: ({ t, p }) => resetPassword(t, p) });

  function onSubmit(e) {
    e.preventDefault();
    setLocalError(null);
    if (password.length < MIN_PASSWORD) {
      setLocalError(`Password must be at least ${MIN_PASSWORD} characters.`);
      return;
    }
    if (password !== confirm) {
      setLocalError("Passwords don't match.");
      return;
    }
    mutation.mutate({ t: token, p: password });
  }

  if (!token) {
    return (
      <AuthLayout testId="reset-page" title="Invalid reset link">
        <p className="text-sm text-slate-600 m-0">
          This link is missing its reset token.{' '}
          <Link to="/forgot-password" className={linkCls}>Request a new link</Link>.
        </p>
      </AuthLayout>
    );
  }

  if (mutation.isSuccess) {
    return (
      <AuthLayout testId="reset-page" title="Password updated">
        <p className="text-sm text-slate-600 mt-0 mb-4" data-testid="reset-done">
          Your password has been changed. You can now sign in with it.
        </p>
        <Link to="/login" className={linkCls}>Sign in</Link>
      </AuthLayout>
    );
  }

  const serverError = mutation.isError
    ? (mutation.error?.response?.status === 400
      ? (
        <>
          This reset link is invalid or has expired.{' '}
          <Link to="/forgot-password" className="underline font-medium">Request a new one</Link>.
        </>
      )
      : errorDetail(mutation.error))
    : null;

  return (
    <AuthLayout
      testId="reset-page"
      title="Choose a new password"
      footer={<Link to="/login" className={linkCls}>Back to sign in</Link>}
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <FormError>{localError || serverError}</FormError>
        <Field label="New password" hint={`At least ${MIN_PASSWORD} characters.`}>
          {(p) => (
            <Input {...p} type="password" autoComplete="new-password" autoFocus value={password}
              onChange={(e) => setPassword(e.target.value)} data-testid="reset-password" />
          )}
        </Field>
        <Field label="Confirm new password">
          {(p) => (
            <Input {...p} type="password" autoComplete="new-password" value={confirm}
              onChange={(e) => setConfirm(e.target.value)} data-testid="reset-confirm" />
          )}
        </Field>
        <Button type="submit" className="w-full" loading={mutation.isPending}
          disabled={!password || !confirm} data-testid="reset-submit">
          Update password
        </Button>
      </form>
    </AuthLayout>
  );
}
