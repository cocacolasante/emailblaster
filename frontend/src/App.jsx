import { Routes, Route, useLocation } from 'react-router-dom';
import { MotionConfig, motion } from 'framer-motion';
import Campaigns from './pages/Campaigns.jsx';
import CampaignCreate from './pages/CampaignCreate.jsx';
import CampaignDetail from './pages/CampaignDetail.jsx';
import Preview from './pages/Preview.jsx';
import Analytics from './pages/Analytics.jsx';
import SequenceBuilder from './pages/SequenceBuilder.jsx';
import Leads from './pages/Leads.jsx';
import Opportunities from './pages/Opportunities.jsx';
import Reports from './pages/Reports.jsx';
import ReportBuilder from './pages/ReportBuilder.jsx';
import Dashboard from './pages/Dashboard.jsx';
import UiKit from './pages/UiKit.jsx';
import OpportunityDetail from './pages/OpportunityDetail.jsx';
import Replies from './pages/Replies.jsx';
import ResearchClient from './pages/ResearchClient.jsx';
import Lookalikes from './pages/Lookalikes.jsx';
import Signals from './pages/Signals.jsx';
import SocialRadar from './pages/SocialRadar.jsx';
import Settings from './pages/Settings.jsx';
import Login from './pages/Login.jsx';
import Signup from './pages/Signup.jsx';
import ForgotPassword from './pages/ForgotPassword.jsx';
import ResetPassword from './pages/ResetPassword.jsx';
import AcceptInvite from './pages/AcceptInvite.jsx';
import RequireAuth from './components/RequireAuth.jsx';
import IntegrationMissingListener from './components/IntegrationMissingListener.jsx';
import { useAuth } from './hooks/useAuth.js';
import Nav from './components/Nav.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { ToastProvider } from './components/Toast.jsx';
import NotificationBell from './components/NotificationBell.jsx';
import { spring } from './utils/motion.js';

/**
 * Routed content with a calm page-mount transition (Phase 4, UI refinement).
 * Keyed on the pathname so navigating between pages re-mounts + animates the
 * content in (fade + a few px rise) — non-blocking: there's no exit animation
 * to wait on, the new page renders immediately with its initial style and
 * settles via the shared spring.  Under `prefers-reduced-motion: reduce` the
 * MotionConfig at the root snaps it instantly.
 */
function RoutedContent() {
  const location = useLocation();
  // Keyed on the workspace too: switching workspace remounts every page so
  // nothing renders from the previous workspace's (cleared) cache.
  const { workspace } = useAuth();
  return (
    <motion.div
      key={`${workspace?.id || ''}:${location.pathname}`}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={spring}
    >
      <Routes location={location}>
        <Route path="/" element={<Campaigns />} />
        <Route path="/campaigns/new" element={<CampaignCreate />} />
        <Route path="/campaigns/:id" element={<CampaignDetail />} />
        <Route path="/campaigns/:id/preview" element={<Preview />} />
        <Route path="/campaigns/:id/sequence" element={<SequenceBuilder />} />
        <Route path="/campaigns/:id/analytics" element={<Analytics />} />
        <Route path="/leads" element={<Leads />} />
        <Route path="/opportunities" element={<Opportunities />} />
        <Route path="/opportunities/:id" element={<OpportunityDetail />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/reports/builder" element={<ReportBuilder />} />
        <Route path="/replies" element={<Replies />} />
        <Route path="/research-client" element={<ResearchClient />} />
        <Route path="/signals" element={<Signals />} />
        <Route path="/lookalikes" element={<Lookalikes />} />
        <Route path="/social-radar" element={<SocialRadar />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/ui-kit" element={<UiKit />} />{/* internal design-system preview */}
      </Routes>
    </motion.div>
  );
}

/** The authenticated app: sidebar + notification bell + routed pages. */
function AppShell() {
  return (
    <div className="flex min-h-screen bg-slate-50">
      <Nav />
      <main className="flex-1 min-w-0 overflow-auto">
        <NotificationBell />
        <ErrorBoundary>
          <RoutedContent />
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default function App() {
  return (
    // reducedMotion="user" → every framer-motion surface (modals, menus, tab
    // indicator, list mounts, this page transition) honors the OS
    // "reduce motion" setting automatically.
    <MotionConfig reducedMotion="user" transition={spring}>
      <ToastProvider>
        <IntegrationMissingListener />
        <Routes>
          {/* Public auth pages — rendered without the app shell. */}
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/accept-invite" element={<AcceptInvite />} />
          {/* Everything else requires a session. */}
          <Route
            path="*"
            element={(
              <RequireAuth>
                <AppShell />
              </RequireAuth>
            )}
          />
        </Routes>
      </ToastProvider>
    </MotionConfig>
  );
}
