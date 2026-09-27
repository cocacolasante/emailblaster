import { Card } from './ui.jsx';

/** Centered-card scaffold for the public auth pages (login, signup, …).
 *  Rendered outside the app shell — no nav, no notification bell. */
export default function AuthLayout({ title, subtitle, children, footer, testId }) {
  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="text-center mb-6">
          <span className="text-slate-900 font-bold text-xl tracking-tight">Email Blaster</span>
        </div>
        <Card className="p-6" data-testid={testId}>
          {title && <h1 className="text-lg font-semibold text-slate-900 m-0">{title}</h1>}
          {subtitle && <p className="text-sm text-slate-500 mt-1 mb-0">{subtitle}</p>}
          <div className={title || subtitle ? 'mt-5' : ''}>{children}</div>
        </Card>
        {footer && <div className="text-center text-sm text-slate-500 mt-4">{footer}</div>}
      </div>
    </div>
  );
}

/** Inline form-level error banner. */
export function FormError({ children, testId = 'form-error' }) {
  if (!children) return null;
  return (
    <div
      role="alert"
      data-testid={testId}
      className="mb-4 px-3 py-2 rounded-lg bg-danger-50 border border-danger-200 text-sm text-danger-700"
    >
      {children}
    </div>
  );
}
