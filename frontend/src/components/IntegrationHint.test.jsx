import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';

import { IntegrationBanner, IntegrationHint } from './IntegrationHint.jsx';
import { api, integrationsWithMissing, server } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

function renderAt(el) {
  return renderRoutes([<Route key="p" path="/" element={el} />]);
}

describe('IntegrationHint / IntegrationBanner', () => {
  it('render nothing when the provider is configured', async () => {
    let fetched = false;
    server.use(http.get(api('/settings/integrations'), () => {
      fetched = true;
      return HttpResponse.json([]);
    }));
    renderAt(<><IntegrationHint providers="hunter" /><IntegrationBanner providers="brevo" /></>);
    await waitFor(() => expect(fetched).toBe(true));
    // Unknown / absent providers are treated as configured (optimistic).
    expect(screen.queryByTestId('integration-hint-hunter')).toBeNull();
    expect(screen.queryByTestId('integration-banner-brevo')).toBeNull();
  });

  it('names only the missing providers and links to the Integrations tab', async () => {
    server.use(integrationsWithMissing('hunter', 'anthropic'));
    renderAt(<IntegrationHint providers={['anthropic', 'brevo', 'hunter']} />);
    const hint = await screen.findByTestId('integration-hint-anthropic-hunter');
    expect(hint).toHaveTextContent('Connect Anthropic and Hunter.io in Settings → Integrations');
    expect(screen.getByRole('link')).toHaveAttribute('href', '/settings?tab=integrations');
  });
});
