/**
 * Owner selection controls (Multi-tenancy P3).
 *
 *  - <OwnerPicker>  — pick the accountable member for a record (value is a
 *    user id or null).  Native <select> for free keyboard + screen-reader
 *    support; options are "Unassigned", an optional "Me" shortcut, then every
 *    workspace member ("Name — email").
 *  - <OwnerFilter>  — list filter: All / Mine / Unassigned / a member.  Value
 *    is the wire format of the `?owner=` query param ('' | 'me' |
 *    'unassigned' | <user id>).
 *  - <ScopeToggle>  — the compact "Mine / Everyone" segmented control.
 */
import { useId } from 'react';

import { Select } from './ui.jsx';
import { memberName, useOwnerLookup } from './OwnerAvatar.jsx';

const ME_SENTINEL = '__me__';
const UNASSIGNED_SENTINEL = '';

function memberOptionLabel(m) {
  const name = memberName(m);
  return m.email && m.email !== name ? `${name} — ${m.email}` : name;
}

/**
 * @param value            user id | null
 * @param onChange         (userId | null) => void
 * @param allowUnassigned  include the "Unassigned" option (default true)
 * @param showMe           include the "Me" shortcut at the top (default true)
 * @param label            accessible label (visually hidden unless `showLabel`)
 */
export function OwnerPicker({
  value = null,
  onChange,
  disabled = false,
  allowUnassigned = true,
  showMe = true,
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
    const v = e.target.value;
    if (v === ME_SENTINEL) onChange?.(myId);
    else onChange?.(v || null);
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
        {showMe && myId && value !== myId && <option value={ME_SENTINEL}>Me</option>}
        {orphan && <option value={value}>{orphan.label}</option>}
        {members.map((m) => (
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
      value={value || ''}
      onChange={(e) => onChange?.(e.target.value)}
      aria-label={label}
      data-testid={testId}
      disabled={disabled}
      className={`px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${className}`}
    >
      {allLabel != null ? <option value="">{allLabel}</option> : <option value="">—</option>}
      <option value="me">Me</option>
      {includeUnassigned && <option value="unassigned">Unassigned</option>}
      {members.map((m) => (
        <option key={m.user_id} value={m.user_id}>
          {memberName(m)}{m.user_id === myId ? ' (you)' : ''}
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
