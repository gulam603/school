/** Left navigation - the ten sections of the terminal. */

import { NavLink } from 'react-router-dom';

const NAV = [
  { to: '/', label: 'Dashboard', icon: '#', end: true },
  { to: '/market', label: 'Market Watch', icon: '~' },
  { to: '/positions', label: 'Positions', icon: '=' },
  { to: '/orders', label: 'Orders', icon: '>' },
  { to: '/history', label: 'History', icon: 'H' },
  { to: '/strategy', label: 'Strategy', icon: 'S' },
  { to: '/backtest', label: 'Backtest', icon: 'B' },
  { to: '/risk', label: 'Risk Management', icon: '!' },
  { to: '/settings', label: 'Settings', icon: '*' },
  { to: '/logs', label: 'Logs', icon: 'L' },
];

export function Sidebar({ openPositions, botRunning }: { openPositions: number; botRunning: boolean }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="brand-mark">^</span>
        <span>
          <div className="brand-name">MT5 BOT</div>
          <div className="brand-sub">Trading Terminal</div>
        </span>
      </div>

      <nav className="nav">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            title={item.label}
          >
            <span className="nav-icon mono">{item.icon}</span>
            <span className="nav-text">{item.label}</span>
            {item.to === '/positions' && openPositions > 0 && (
              <span className="nav-badge">{openPositions}</span>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-foot">
        <div className="row tight">
          <span className={`dot ${botRunning ? 'blink' : ''}`} style={{ color: botRunning ? '#22c55e' : '#626d8c' }} />
          <span>{botRunning ? 'Bot running' : 'Bot stopped'}</span>
        </div>
        <div className="faint" style={{ marginTop: 4 }}>v1.0.0 - local only</div>
      </div>
    </aside>
  );
}
