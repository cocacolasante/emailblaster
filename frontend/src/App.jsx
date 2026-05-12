import { Routes, Route } from 'react-router-dom';
import Campaigns from './pages/Campaigns.jsx';
import CampaignCreate from './pages/CampaignCreate.jsx';
import CampaignDetail from './pages/CampaignDetail.jsx';
import Preview from './pages/Preview.jsx';
import Analytics from './pages/Analytics.jsx';
import Settings from './pages/Settings.jsx';
import Nav from './components/Nav.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { ToastProvider } from './components/Toast.jsx';

export default function App() {
  return (
    <ToastProvider>
      <div style={{ display: 'flex', minHeight: '100vh', background: '#f9fafb' }}>
        <Nav />
        <main style={{ flex: 1, minWidth: 0 }}>
          <ErrorBoundary>
            <Routes>
              <Route path="/" element={<Campaigns />} />
              <Route path="/campaigns/new" element={<CampaignCreate />} />
              <Route path="/campaigns/:id" element={<CampaignDetail />} />
              <Route path="/campaigns/:id/preview" element={<Preview />} />
              <Route path="/campaigns/:id/analytics" element={<Analytics />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </ToastProvider>
  );
}
