import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getAuthConfig, login, errorDetail } from '../api/auth.js';
import { adoptSession } from '../hooks/useAuth.js';
import AuthLayout, { FormError } from '../components/AuthLayout.jsx';
import { Button, Field, Input } from '../components/ui.jsx';

/** Only allow same-app relative redirects (no `//evil.com`, no absolute URLs). */
export function safeNext(raw) {
  if (!raw || typeof raw !== 'string') return '/';
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.startsWith('/\\')) return '/';
  if (raw.startsWith('/login')) return '/';
  return raw;
}

export const AUTH_CONFIG_KEY = ['auth-config'];

export function useAuthConfig() {
  return useQuery({
    queryKey: AUTH_CONFIG_KEY,
    queryFn: getAuthConfig,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

function loginError(err) {
  const status = err?.response?.status;
  if (status === 401) return 'Invalid email or password.';
  if (status === 429) return 'Too many sign-in attempts. Please wait a few minutes and try again.';
  return errorDetail(err, 'Sign-in failed. Please try again.');
}

export default function Login() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const next = safeNext(params.get('next'));
  const { data: config } = useAuthConfig();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const mutation = useMutation({
    mutationFn: login,
    onSuccess: (me) => {
      adoptSession(queryClient, me);
      navigate(next, { replace: true });
    },
  });

  function onSubmit(e) {
    e.preventDefault();
    mutation.mutate({ email: email.trim(), password });
  }

  return (
    <AuthLayout
      testId="login-page"
      title="Sign in"
      subtitle="Welcome back — sign in to your workspace."
      footer={config?.allow_signup ? (
        <>
          New here?{' '}
          <Link to="/signup" className="font-medium text-brand-700 hover:text-brand-600">
            Create an account
          </Link>
        </>
      ) : null}
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <FormError>{mutation.isError ? loginError(mutation.error) : null}</FormError>
        <Field label="Email">
          {(p) => (
            <Input
              {...p}
              type="email"
              autoComplete="email"
              autoFocus
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="login-email"
            />
          )}
        </Field>
        <Field label="Password">
          {(p) => (
            <Input
              {...p}
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="login-password"
            />
          )}
        </Field>
        <div className="flex justify-end -mt-1">
          <Link to="/forgot-password" className="text-xs font-medium text-brand-700 hover:text-brand-600">
            Forgot password?
          </Link>
        </div>
        <Button
          type="submit"
          className="w-full"
          loading={mutation.isPending}
          disabled={!email || !password}
          data-testid="login-submit"
        >
          Sign in
        </Button>
      </form>
    </AuthLayout>
  );
}
