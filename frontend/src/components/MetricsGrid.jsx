/**
 * Simple 4-column metric card grid (collapses on narrow screens).
 *
 * Each metric: { label, value, tooltip?, accent? }
 */
export default function MetricsGrid({ metrics }) {
  return (
    <div
      data-testid="metrics-grid"
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: 12,
        marginBottom: 20,
      }}
    >
      {metrics.map((m) => (
        <div
          key={m.label}
          data-testid={`metric-${m.label.toLowerCase().replace(/\s+/g, '-')}`}
          title={m.tooltip || ''}
          style={{
            padding: 16,
            border: '1px solid #e5e7eb',
            borderRadius: 8,
            background: 'white',
          }}
        >
          <div style={{ fontSize: 12, color: '#666', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            {m.label}
          </div>
          <div
            style={{
              fontSize: 24,
              fontWeight: 600,
              marginTop: 4,
              color: m.accent || '#111',
            }}
          >
            {m.value}
          </div>
          {m.tooltip && (
            <div style={{ fontSize: 11, color: '#888', marginTop: 4 }}>{m.tooltip}</div>
          )}
        </div>
      ))}
    </div>
  );
}
