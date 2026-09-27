import { Navigate, useLocation } from 'react-router-dom';
import { useAuth, isUnauthorized } from '../hooks/useAuth.js';
import { loginUrlFor } from '../api/client.js';
import { ErrorState } from './states.jsx';

function FullPageSpinner() {
  return (
    <div
      data-testid="auth-loading"
      role="status"
      aria-live="polite"
      className="min-h-screen flex items-center justify-center bg-slate-50"
    >
      <div className="flex items-center gap-3 text-sm text-slate-500">
        <svg className="w-5 h-5 text-brand-600 motion-safe:animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
        </svg>
        Loading your workspace…
      </div>
    </div>
  );
}

/**
 * Gate for every non-public route.  Reads the shared `['auth-me']` query:
 * loading → spinner; 401 (or no session) → /login?next=<here>; any other
 * failure (API down) → retryable error; otherwise render the app.
 */
export default function RequireAuth({ children }) {
  const { me, isLoading, error, refetch } = useAuth();
  const location = useLocation();

  if (isLoading) return <FullPageSpinner />;

  if (!me) {
    if (error && !isUnauthorized(error)) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-slate-50">
          <ErrorState
            testId="auth-error"
            message="Couldn't reach the server. Check that the API is running."
            onRetry={() => refetch()}
          />
        </div>
      );
    }
    return <Navigate to={loginUrlFor(location.pathname, location.search)} replace />;
  }

  return children;
}
