import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';

const ITEMS = [
  {
    to: '/',
    label: 'Campaigns',
    end: true,
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    to: '/settings',
    label: 'Settings',
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

const STORAGE_KEY = 'ui.nav.collapsed';


function ChevronIcon({ collapsed }) {
  return (
    <svg
      className={`w-4 h-4 transition-transform ${collapsed ? 'rotate-180' : ''}`}
      fill="none" stroke="currentColor" viewBox="0 0 24 24"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
    </svg>
  );
}


export default function Nav() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    } catch {
      /* swallow — non-fatal */
    }
  }, [collapsed]);

  return (
    <nav
      data-testid="nav"
      data-collapsed={collapsed ? 'true' : 'false'}
      className={`${collapsed ? 'w-14 min-w-14' : 'w-60 min-w-60'} bg-slate-900 min-h-screen flex flex-col transition-[width] duration-150`}
    >
      <div className={`flex items-center border-b border-slate-800 ${collapsed ? 'justify-center px-2 py-4' : 'justify-between px-4 py-5'}`}>
        {!collapsed && (
          <span className="text-white font-bold text-lg tracking-tight">Email Blaster</span>
        )}
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          className="text-slate-400 hover:text-white hover:bg-slate-800 rounded-md p-1.5 transition-colors"
        >
          <ChevronIcon collapsed={collapsed} />
        </button>
      </div>
      <div className="pt-4 flex-1">
        {!collapsed && (
          <p className="px-6 pb-2 text-xs font-semibold text-slate-500 uppercase tracking-wider">Menu</p>
        )}
        <ul className="list-none p-0 m-0">
          {ITEMS.map((item) => (
            <li key={item.to} className="px-2">
              <NavLink
                to={item.to}
                end={item.end}
                title={collapsed ? item.label : undefined}
                aria-label={collapsed ? item.label : undefined}
                className={({ isActive }) =>
                  `flex items-center ${collapsed ? 'justify-center' : 'gap-3'} px-4 py-2.5 rounded-lg text-sm font-medium transition-colors no-underline ${
                    isActive
                      ? 'bg-slate-800 text-white'
                      : 'text-slate-400 hover:text-white hover:bg-slate-800'
                  }`
                }
              >
                {item.icon}
                {!collapsed && <span>{item.label}</span>}
              </NavLink>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
