import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { forgotPassword } from '../api/auth.js';
import AuthLayout from '../components/AuthLayout.jsx';
import { Button, Field, Input } from '../components/ui.jsx';

const backToSignIn = (
  <Link to="/login" className="font-medium text-brand-700 hover:text-brand-600">Back to sign in</Link>
);

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);

  // The endpoint always answers {ok:true} (no account enumeration); we show
  // the same confirmation even if the request itself fails.
  const mutation = useMutation({
    mutationFn: forgotPassword,
    onSettled: () => setSent(true),
  });

  function onSubmit(e) {
    e.preventDefault();
    mutation.mutate(email.trim());
  }

  if (sent) {
    return (
      <AuthLayout testId="forgot-page" title="Check your email" footer={backToSignIn}>
        <p className="text-sm text-slate-600 m-0" data-testid="forgot-sent">
          If an account exists for <span className="font-medium text-slate-800">{email.trim()}</span>,
          we&apos;ve sent a reset link. It may take a minute to arrive.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      testId="forgot-page"
      title="Reset your password"
      subtitle="Enter your email and we'll send you a link to set a new password."
      footer={backToSignIn}
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <Field label="Email">
          {(p) => (
            <Input
              {...p}
              type="email"
              autoComplete="email"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="forgot-email"
            />
          )}
        </Field>
        <Button type="submit" className="w-full" loading={mutation.isPending} disabled={!email.trim()} data-testid="forgot-submit">
          Send reset link
        </Button>
      </form>
    </AuthLayout>
  );
}
