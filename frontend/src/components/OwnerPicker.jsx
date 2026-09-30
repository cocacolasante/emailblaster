/**
 * Owner selection controls (Multi-tenancy P3).
 *
 *  - <OwnerPicker>  — pick the accountable member for a record (value is a
 *    user id or null).  Native <select> for free keyboard + screen-reader
 *    support; options are "Unassigned", then every workspace member
 *    ("Name — email"), yourself first and marked "(you)" — one entry per
 *    person, so you never appear twice.
 *  - <OwnerFilter>  — list filter: All / Mine / Unassigned / a member.  Value
 *    is the wire format of the `?owner=` query param ('' | 'me' |
 *    'unassigned' | <user id>).
 *  - <ScopeToggle>  — the compact "Mine / Everyone" segmented control.
 */
import { useId } from 'react';

import { Select } from './ui.jsx';
import { memberName, useOwnerLookup } from './OwnerAvatar.jsx';

const UNASSIGNED_SENTINEL = '';

/** Members with the signed-in user first. */
function meFirst(members, myId) {
  return [...members].sort((a, b) => (b.user_id === myId) - (a.user_id === myId));
}

function memberOptionLabel(m) {
  const name = memberName(m);
  return m.email && m.email !== name ? `${name} — ${m.email}` : name;
}

/**
 * @param value            user id | null
 * @param onChange         (userId | null) => void
 * @param allowUnassigned  include the "Unassigned" option (default true)
 * @param showMe           deprecated (kept for callers); you're always listed once as "(you)"
 * @param label            accessible label (visually hidden unless `showLabel`)
 */
export function OwnerPicker({
  value = null,
  onChange,
  disabled = false,
  allowUnassigned = true,
  showMe: _showMe = true, // eslint-disable-line no-unused-vars
  label = 'Owner',
  showLabel = false,
  size = 'md',
  className = '',
  testId = 'owner-picker',
  id,
}) {
  const autoId = useId();
  const selectId = id || autoId;
  const { members, myId, resolve } = useOwnerLookup();
  const current = value || UNASSIGNED_SENTINEL;
  // A removed member still owning the record keeps a visible, selectable
  // entry so the select never silently shows the wrong person.
  const orphan = value && !members.some((m) => m.user_id === value) ? resolve(value) : null;

  function handleChange(e) {
    onChange?.(e.target.value || null);
  }

  const sizeCls = size === 'sm' ? '!py-1 !text-xs' : '';

  return (
    <span className={`inline-flex flex-col ${className}`}>
      <label htmlFor={selectId} className={showLabel ? 'block text-xs font-medium text-slate-700 mb-1' : 'sr-only'}>
        {label}
      </label>
      <Select
        id={selectId}
        value={current}
        onChange={handleChange}
        disabled={disabled}
        data-testid={testId}
        className={sizeCls}
      >
        {allowUnassigned && <option value={UNASSIGNED_SENTINEL}>Unassigned</option>}
        {!allowUnassigned && !value && <option value="" disabled>Choose a member…</option>}
        {orphan && <option value={value}>{orphan.label}</option>}
        {meFirst(members, myId).map((m) => (
          <option key={m.user_id} value={m.user_id}>
            {memberOptionLabel(m)}{m.user_id === myId ? ' (you)' : ''}
          </option>
        ))}
      </Select>
    </span>
  );
}

/**
 * List filter.  `value` is '' (everyone) | 'me' | 'unassigned' | <user id>.
 * `allLabel={null}` hides the "All owners" option (report-builder values);
 * `includeUnassigned={false}` hides "Unassigned".
 */
export function OwnerFilter({
  value = '',
  onChange,
  allLabel = 'All owners',
  includeUnassigned = true,
  label = 'Filter by owner',
  className = '',
  testId = 'owner-filter',
  disabled = false,
}) {
  const { members, myId } = useOwnerLookup();
  return (
    <select
      // Your own id is shown as "Me" (you're not listed twice).
      value={value && value === myId ? 'me' : (value || '')}
      onChange={(e) => onChange?.(e.target.value)}
      aria-label={label}
      data-testid={testId}
      disabled={disabled}
      className={`px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${className}`}
    >
      {allLabel != null ? <option value="">{allLabel}</option> : <option value="">—</option>}
      <option value="me">Me</option>
      {includeUnassigned && <option value="unassigned">Unassigned</option>}
      {/* "Me" already covers you — list only the other members. */}
      {members.filter((m) => m.user_id !== myId).map((m) => (
        <option key={m.user_id} value={m.user_id}>
          {memberName(m)}
        </option>
      ))}
    </select>
  );
}

/** "Mine / Everyone" segmented toggle.  `mine` is a boolean. */
export function ScopeToggle({
  mine, onChange, mineLabel = 'Mine', allLabel = 'Everyone', testId = 'scope-toggle', label = 'Owner scope',
}) {
  const opts = [
    { key: 'mine', text: mineLabel, on: mine },
    { key: 'all', text: allLabel, on: !mine },
  ];
  return (
    <div
      className="inline-flex rounded-lg border border-slate-300 overflow-hidden"
      role="group"
      aria-label={label}
      data-testid={testId}
    >
      {opts.map((o) => (
        <button
          key={o.key}
          type="button"
          aria-pressed={o.on}
          data-testid={`${testId}-${o.key}`}
          onClick={() => onChange?.(o.key === 'mine')}
          className={`px-3 py-1.5 text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 ${
            o.on ? 'bg-brand-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'
          }`}
        >
          {o.text}
        </button>
      ))}
    </div>
  );
}

export default OwnerPicker;
