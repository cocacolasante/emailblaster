const DAYS = [
  { value: 0, label: 'Mon' },
  { value: 1, label: 'Tue' },
  { value: 2, label: 'Wed' },
  { value: 3, label: 'Thu' },
  { value: 4, label: 'Fri' },
  { value: 5, label: 'Sat' },
  { value: 6, label: 'Sun' },
];

const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Asia/Kolkata',
  'Australia/Sydney',
];

export default function ScheduleConfig({ value, onChange }) {
  function toggleDay(day) {
    const set = new Set(value.schedule_days);
    if (set.has(day)) set.delete(day);
    else set.add(day);
    onChange({ ...value, schedule_days: Array.from(set).sort((a, b) => a - b) });
  }

  // min_delay UI unit: derived. If a multiple of 60 and value >= 60, show minutes.
  const delayInMinutes = value.min_delay_unit === 'minutes';
  const displayDelay = delayInMinutes
    ? Math.round(value.min_delay_seconds / 60)
    : value.min_delay_seconds;

  function setDelayValue(displayValue) {
    const display = Number(displayValue) || 0;
    const seconds = delayInMinutes ? display * 60 : display;
    onChange({ ...value, min_delay_seconds: seconds });
  }

  function setDelayUnit(unit) {
    // Switching unit preserves min_delay_seconds — only the display changes.
    onChange({ ...value, min_delay_unit: unit });
  }

  return (
    <div data-testid="schedule-config" style={{ display: 'grid', gap: 16 }}>
      <div>
        <label style={labelStyle}>Days</label>
        <div role="group" aria-label="Days of week" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {DAYS.map((d) => {
            const active = value.schedule_days.includes(d.value);
            return (
              <button
                key={d.value}
                type="button"
                aria-pressed={active}
                aria-label={d.label}
                onClick={() => toggleDay(d.value)}
                style={active ? activePillStyle : pillStyle}
              >
                {d.label}
              </button>
            );
          })}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ flex: 1 }}>
          <label style={labelStyle}>Start time</label>
          <input
            type="time"
            aria-label="Start time"
            value={value.schedule_time_start.slice(0, 5)}
            onChange={(e) => onChange({ ...value, schedule_time_start: e.target.value })}
            style={inputStyle}
          />
        </div>
        <div style={{ flex: 1 }}>
          <label style={labelStyle}>End time</label>
          <input
            type="time"
            aria-label="End time"
            value={value.schedule_time_end.slice(0, 5)}
            onChange={(e) => onChange({ ...value, schedule_time_end: e.target.value })}
            style={inputStyle}
          />
        </div>
      </div>

      <div>
        <label style={labelStyle}>Timezone</label>
        <select
          aria-label="Timezone"
          value={value.schedule_timezone}
          onChange={(e) => onChange({ ...value, schedule_timezone: e.target.value })}
          style={inputStyle}
        >
          {TIMEZONES.map((tz) => (
            <option key={tz} value={tz}>{tz}</option>
          ))}
        </select>
      </div>

      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ flex: 1 }}>
          <label style={labelStyle}>Max per hour</label>
          <input
            type="number"
            min="1"
            aria-label="Max per hour"
            placeholder="No limit"
            value={value.max_per_hour ?? ''}
            onChange={(e) =>
              onChange({
                ...value,
                max_per_hour: e.target.value === '' ? null : Number(e.target.value),
              })
            }
            style={inputStyle}
          />
        </div>
        <div style={{ flex: 1 }}>
          <label style={labelStyle}>Max per day</label>
          <input
            type="number"
            min="1"
            aria-label="Max per day"
            placeholder="No limit"
            value={value.max_per_day ?? ''}
            onChange={(e) =>
              onChange({
                ...value,
                max_per_day: e.target.value === '' ? null : Number(e.target.value),
              })
            }
            style={inputStyle}
          />
        </div>
      </div>

      <div>
        <label style={labelStyle}>Minimum delay between sends</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            type="number"
            min="0"
            aria-label="Min delay"
            value={displayDelay}
            onChange={(e) => setDelayValue(e.target.value)}
            style={{ ...inputStyle, flex: 1 }}
          />
          <select
            aria-label="Min delay unit"
            value={delayInMinutes ? 'minutes' : 'seconds'}
            onChange={(e) => setDelayUnit(e.target.value)}
            style={{ ...inputStyle, width: 120 }}
          >
            <option value="seconds">seconds</option>
            <option value="minutes">minutes</option>
          </select>
        </div>
      </div>
    </div>
  );
}

const labelStyle = {
  display: 'block',
  marginBottom: 6,
  fontSize: 13,
  fontWeight: 500,
  color: '#333',
};

const inputStyle = {
  display: 'block',
  width: '100%',
  padding: '8px 10px',
  border: '1px solid #ccc',
  borderRadius: 4,
  fontSize: 14,
  boxSizing: 'border-box',
};

const pillStyle = {
  padding: '6px 12px',
  border: '1px solid #ccc',
  borderRadius: 999,
  background: 'white',
  cursor: 'pointer',
  fontSize: 13,
};

const activePillStyle = {
  ...pillStyle,
  background: '#2563eb',
  color: 'white',
  border: '1px solid #2563eb',
};
