import { NavLink } from 'react-router-dom';

const ITEMS = [
  { to: '/', label: 'Campaigns', end: true },
  { to: '/settings', label: 'Settings' },
];

export default function Nav() {
  return (
    <nav data-testid="nav" style={navStyle}>
      <div style={brandStyle}>Email Blaster</div>
      <ul style={listStyle}>
        {ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              style={({ isActive }) => linkStyle(isActive)}
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

const navStyle = {
  width: 200,
  minWidth: 200,
  background: '#1f2937',
  color: 'white',
  minHeight: '100vh',
  padding: '20px 0',
};

const brandStyle = {
  padding: '0 20px 20px',
  fontWeight: 700,
  fontSize: 18,
  borderBottom: '1px solid #374151',
  marginBottom: 16,
  letterSpacing: 0.3,
};

const listStyle = {
  listStyle: 'none',
  padding: 0,
  margin: 0,
};

function linkStyle(isActive) {
  return {
    display: 'block',
    padding: '10px 20px',
    color: isActive ? 'white' : '#cbd5e1',
    background: isActive ? '#2563eb' : 'transparent',
    textDecoration: 'none',
    fontSize: 14,
    borderLeft: isActive ? '3px solid white' : '3px solid transparent',
  };
}
