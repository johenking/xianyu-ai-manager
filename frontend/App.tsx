import React, { Suspense, lazy, useEffect, useState } from 'react';
import Sidebar from './components/Sidebar';
import AuthPortal from './components/AuthPortal';
import { verifyToken, logout } from './services/api';
import { Loader2, Menu } from 'lucide-react';

const pageLoaders = {
  dashboard: () => import('./components/Dashboard'),
  accounts: () => import('./components/AccountList'),
  orders: () => import('./components/OrderList'),
  cards: () => import('./components/CardList'),
  items: () => import('./components/ItemList'),
  keywords: () => import('./components/Keywords'),
  skills: () => import('./components/SkillCenter'),
  settings: () => import('./components/Settings'),
};

const Dashboard = lazy(pageLoaders.dashboard);
const AccountList = lazy(pageLoaders.accounts);
const OrderList = lazy(pageLoaders.orders);
const CardList = lazy(pageLoaders.cards);
const ItemList = lazy(pageLoaders.items);
const Settings = lazy(pageLoaders.settings);
const Keywords = lazy(pageLoaders.keywords);
const SkillCenter = lazy(pageLoaders.skills);

type PageKey = keyof typeof pageLoaders;

const preloadPage = (page: string) => {
  const loader = pageLoaders[page as PageKey];
  if (loader) {
    void loader();
  }
};

const preloadAppPages = () => {
  const run = () => {
    (Object.keys(pageLoaders) as PageKey[]).forEach((key) => {
      if (key !== 'dashboard') {
        void pageLoaders[key]();
      }
    });
  };
  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(run, { timeout: 2000 });
  } else {
    globalThis.setTimeout(run, 500);
  }
};

const PageLoading: React.FC = () => (
  <div className="flex min-h-[50vh] items-center justify-center" role="status" aria-label="页面加载中">
    <Loader2 className="h-8 w-8 animate-spin text-[#D6B500]" />
  </div>
);

const App: React.FC = () => {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Check auth on mount
  useEffect(() => {
      const token = localStorage.getItem('auth_token');
      if (token) {
          verifyToken()
            .then((res) => {
                if (res.authenticated) {
                    setIsLoggedIn(true);
                } else {
                    localStorage.removeItem('auth_token');
                }
            })
            .catch(() => localStorage.removeItem('auth_token'))
            .finally(() => setCheckingAuth(false));
      } else {
          setCheckingAuth(false);
      }

      const handleLogout = () => {
          setIsLoggedIn(false);
          window.history.replaceState({}, '', '/login');
      };
      window.addEventListener('auth:logout', handleLogout);
      return () => window.removeEventListener('auth:logout', handleLogout);
  }, []);

  useEffect(() => {
      if (isLoggedIn) {
          preloadAppPages();
      }
  }, [isLoggedIn]);

  const publicDocument = window.location.pathname === '/terms'
    || window.location.pathname === '/privacy';

  if (publicDocument) {
      return <AuthPortal onAuthenticated={() => setIsLoggedIn(true)} />;
  }

  if (checkingAuth) {
      return (
          <div className="min-h-screen flex items-center justify-center bg-[#f5f5f7]">
              <Loader2 className="w-8 h-8 text-[#FFE815] animate-spin" />
          </div>
      );
  }

  if (!isLoggedIn) {
    return <AuthPortal onAuthenticated={() => setIsLoggedIn(true)} />;
  }

  // Main App Layout
  const renderContent = () => {
    switch (activeTab) {
      case 'dashboard': return <Dashboard />;
      case 'accounts': return <AccountList />;
      case 'orders': return <OrderList />;
      case 'cards': return <CardList />;
      case 'items': return <ItemList />;
      case 'keywords': return <Keywords />;
      case 'skills': return <SkillCenter />;
      case 'settings': return <Settings />;
      default: return <Dashboard />;
    }
  };

  return (
    <div className="flex min-h-screen bg-[#F4F5F7] text-[#111]">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onPreloadTab={preloadPage}
        onLogout={async () => {
            try {
                await logout();
            } catch {
                // 本地退出优先，服务端会话过期后自动清理。
            }
            localStorage.removeItem('auth_token');
            window.history.replaceState({}, '', '/login');
            setIsLoggedIn(false);
        }}
        mobileOpen={mobileNavOpen}
        onMobileClose={() => setMobileNavOpen(false)}
      />

      {mobileNavOpen && (
        <button type="button" aria-label="关闭导航遮罩" onClick={() => setMobileNavOpen(false)} className="fixed inset-0 z-40 bg-black/30 lg:hidden" />
      )}

      <main className="flex-1 min-w-0 lg:ml-64 p-4 sm:p-6 lg:p-10 overflow-y-auto min-h-screen relative scroll-smooth">
        <div className="mb-5 flex items-center gap-3 lg:hidden">
          <button type="button" onClick={() => setMobileNavOpen(true)} className="h-10 w-10 rounded-xl bg-white border border-gray-100 flex items-center justify-center shadow-sm" aria-label="打开导航">
            <Menu className="w-5 h-5" />
          </button>
          <span className="font-extrabold text-gray-900">闲鱼智控</span>
        </div>
        <div className="max-w-[1400px] mx-auto pb-10">
            <Suspense fallback={<PageLoading />}>
              {renderContent()}
            </Suspense>
        </div>
      </main>
    </div>
  );
};

export default App;
