/** Routing + shared live state for every page. */

import { Route, Routes, useLocation } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { ToastProvider } from './components/Toasts';
import { useSnapshot } from './hooks/useSnapshot';
import { Backtest } from './pages/Backtest';
import { Dashboard } from './pages/Dashboard';
import { History } from './pages/History';
import { Logs } from './pages/Logs';
import { MarketWatch } from './pages/MarketWatch';
import { Orders } from './pages/Orders';
import { PositionsPage } from './pages/Positions';
import { RiskManagement } from './pages/RiskManagement';
import { SettingsPage } from './pages/Settings';
import { StrategyPage } from './pages/Strategy';

const TITLES: Record<string, string> = {
  '/': 'Dashboard',
  '/market': 'Market Watch',
  '/positions': 'Positions',
  '/orders': 'Orders',
  '/history': 'History',
  '/strategy': 'Strategy',
  '/backtest': 'Backtest',
  '/risk': 'Risk Management',
  '/settings': 'Settings',
  '/logs': 'Logs',
};

export default function App() {
  const live = useSnapshot();
  const location = useLocation();
  const title = TITLES[location.pathname] ?? 'MT5 BOT';

  return (
    <ToastProvider>
      <div className="app">
        <Sidebar
          openPositions={live.snapshot?.open_positions ?? 0}
          botRunning={live.snapshot?.bot.running ?? false}
        />
        <div className="main">
          <TopBar snapshot={live.snapshot} transport={live.transport} title={title} />
          <Routes>
            <Route path="/" element={<Dashboard live={live} />} />
            <Route path="/market" element={<MarketWatch live={live} />} />
            <Route path="/positions" element={<PositionsPage live={live} />} />
            <Route path="/orders" element={<Orders />} />
            <Route path="/history" element={<History />} />
            <Route path="/strategy" element={<StrategyPage live={live} />} />
            <Route path="/backtest" element={<Backtest />} />
            <Route path="/risk" element={<RiskManagement live={live} />} />
            <Route path="/settings" element={<SettingsPage live={live} />} />
            <Route path="/logs" element={<Logs live={live} />} />
            <Route path="*" element={<Dashboard live={live} />} />
          </Routes>
        </div>
      </div>
    </ToastProvider>
  );
}
