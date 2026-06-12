import { Routes, Route } from 'react-router-dom';
import Campaigns from './pages/Campaigns.jsx';
import CampaignCreate from './pages/CampaignCreate.jsx';
import CampaignDetail from './pages/CampaignDetail.jsx';
import Preview from './pages/Preview.jsx';
import Analytics from './pages/Analytics.jsx';
import SequenceBuilder from './pages/SequenceBuilder.jsx';
import Leads from './pages/Leads.jsx';
import Opportunities from './pages/Opportunities.jsx';
import OpportunityDetail from './pages/OpportunityDetail.jsx';
import ResearchClient from './pages/ResearchClient.jsx';
import SocialRadar from './pages/SocialRadar.jsx';
import Settings from './pages/Settings.jsx';
import Nav from './components/Nav.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { ToastProvider } from './components/Toast.jsx';

export default function App() {
  return (
    <ToastProvider>
      <div className="flex min-h-screen bg-slate-50">
        <Nav />
        <main className="flex-1 min-w-0 overflow-auto">
          <ErrorBoundary>
            <Routes>
              <Route path="/" element={<Campaigns />} />
              <Route path="/campaigns/new" element={<CampaignCreate />} />
              <Route path="/campaigns/:id" element={<CampaignDetail />} />
              <Route path="/campaigns/:id/preview" element={<Preview />} />
              <Route path="/campaigns/:id/sequence" element={<SequenceBuilder />} />
              <Route path="/campaigns/:id/analytics" element={<Analytics />} />
              <Route path="/leads" element={<Leads />} />
              <Route path="/opportunities" element={<Opportunities />} />
              <Route path="/opportunities/:id" element={<OpportunityDetail />} />
              <Route path="/research-client" element={<ResearchClient />} />
              <Route path="/social-radar" element={<SocialRadar />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </ErrorBoundary>
        </main>
      </div>
    </ToastProvider>
  );
}
