import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import Sidebar from './components/Sidebar';
import AuthPortal from './components/AuthPortal';
import { verifyToken, logout } from './services/api';
import { AlertCircle, Loader2, Menu, RefreshCw } from 'lucide-react';

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
    const handle = window.requestIdleCallback(run, { timeout: 2000 });
    return () => window.cancelIdleCallback(handle);
  }
  const handle = globalThis.setTimeout(run, 500);
  return () => globalThis.clearTimeout(handle);
};

const PageLoading: React.FC = () => (
  <div className="flex min-h-[50vh] items-center justify-center" role="status" aria-label="页面加载中">
    <Loader2 className="h-8 w-8 animate-spin text-[#D6B500]" />
  </div>
);

const App: React.FC = () => {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [authError, setAuthError] = useState('');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const clearIdentity = useCallback(() => {
      setIsLoggedIn(false);
      setIsAdmin(false);
      setAuthError('');
  }, []);

  const hydrateIdentity = useCallback(async () => {
      const tokenAtStart = localStorage.getItem('auth_token');
      if (!tokenAtStart) {
          clearIdentity();
          setCheckingAuth(false);
          return;
      }
      setAuthError('');
      try {
          const response = await verifyToken();
          if (localStorage.getItem('auth_token') !== tokenAtStart) return;
          if (!response.authenticated) {
              localStorage.removeItem('auth_token');
              clearIdentity();
              return;
          }
          setIsLoggedIn(true);
          setIsAdmin(response.is_admin);
      } catch (error) {
          if (localStorage.getItem('auth_token') !== tokenAtStart) return;
          setIsLoggedIn(false);
          setIsAdmin(false);
          setAuthError(error instanceof Error ? error.message : '身份验证暂时不可用');
      } finally {
          if (localStorage.getItem('auth_token') === tokenAtStart) {
              setCheckingAuth(false);
          }
      }
  }, [clearIdentity]);

  const handleAuthenticated = useCallback(() => {
      setCheckingAuth(true);
      void hydrateIdentity();
  }, [hydrateIdentity]);

  // Check auth on mount
  useEffect(() => {
      const token = localStorage.getItem('auth_token');
      if (token) {
          void hydrateIdentity();
      } else {
          clearIdentity();
          setCheckingAuth(false);
      }

      const handleLogout = () => {
          clearIdentity();
          window.history.replaceState({}, '', '/login');
      };
      const handleStorage = (event: StorageEvent) => {
          if (event.key !== 'auth_token') return;
          if (!event.newValue) {
              clearIdentity();
              setCheckingAuth(false);
              return;
          }
          setCheckingAuth(true);
          void hydrateIdentity();
      };
      window.addEventListener('auth:logout', handleLogout);
      window.addEventListener('storage', handleStorage);
      return () => {
          window.removeEventListener('auth:logout', handleLogout);
          window.removeEventListener('storage', handleStorage);
      };
  }, [clearIdentity, hydrateIdentity]);

  useEffect(() => {
      if (!isLoggedIn) return undefined;
      return preloadAppPages();
  }, [isLoggedIn]);

  const publicDocument = window.location.pathname === '/terms'
    || window.location.pathname === '/privacy';

  if (publicDocument) {
      return <AuthPortal onAuthenticated={handleAuthenticated} />;
  }

  if (checkingAuth) {
      return (
          <div className="min-h-screen flex items-center justify-center bg-[#f5f5f7]">
              <Loader2 className="w-8 h-8 text-[#FFE815] animate-spin" />
          </div>
      );
  }

  if (authError && localStorage.getItem('auth_token')) {
      return (
          <div className="flex min-h-screen items-center justify-center bg-[#f5f5f7] p-6">
              <div className="max-w-md text-center">
                  <AlertCircle className="mx-auto h-10 w-10 text-amber-500" />
                  <h1 className="mt-4 text-xl font-extrabold text-gray-900">身份验证暂时不可用</h1>
                  <p className="mt-2 text-sm text-gray-500">{authError}</p>
                  <button type="button" onClick={() => { setCheckingAuth(true); void hydrateIdentity(); }} className="mt-5 inline-flex items-center gap-2 rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-bold text-white">
                      <RefreshCw className="h-4 w-4" />重试身份验证
                  </button>
              </div>
          </div>
      );
  }

  if (!isLoggedIn) {
    return <AuthPortal onAuthenticated={handleAuthenticated} />;
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
      case 'settings': return <Settings isAdmin={isAdmin} />;
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
            clearIdentity();
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
