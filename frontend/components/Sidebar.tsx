import React from 'react';
import { LayoutDashboard, Users, ShoppingBag, CreditCard, Settings, LogOut, Box, Sparkles, MessageSquare, SlidersHorizontal, X } from 'lucide-react';
import BrandLockup from './BrandLockup';

interface SidebarProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  onPreloadTab?: (tab: string) => void;
  onLogout: () => void;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ activeTab, setActiveTab, onPreloadTab, onLogout, mobileOpen = false, onMobileClose }) => {
  const menuItems = [
    { id: 'dashboard', icon: LayoutDashboard, label: '仪表盘' },
    { id: 'accounts', icon: Users, label: '账号管理' },
    { id: 'orders', icon: ShoppingBag, label: '订单管理' },
    { id: 'cards', icon: CreditCard, label: '卡密库存' },
    { id: 'items', icon: Box, label: '商品列表' },
    { id: 'keywords', icon: MessageSquare, label: '关键词管理' },
    { id: 'skills', icon: SlidersHorizontal, label: '技能中心' },
    { id: 'settings', icon: Settings, label: '系统与AI' },
  ];

  return (
    <aside className={`w-64 h-screen fixed left-0 top-0 bg-white border-r border-gray-100 flex flex-col justify-between z-50 shadow-[4px_0_24px_rgba(0,0,0,0.08)] transition-transform duration-200 lg:translate-x-0 ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}>
      <div className="p-6">
        <div className="mb-10 flex items-center px-2">
          <BrandLockup />
          <button type="button" onClick={onMobileClose} className="ml-auto p-2 rounded-lg hover:bg-gray-100 lg:hidden" aria-label="关闭导航">
            <X className="w-5 h-5" />
          </button>
        </div>

        <nav className="space-y-2">
          {menuItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onMouseEnter={() => onPreloadTab?.(item.id)}
                onFocus={() => onPreloadTab?.(item.id)}
                onClick={() => {
                  onPreloadTab?.(item.id);
                  setActiveTab(item.id);
                  onMobileClose?.();
                }}
                className={`w-full flex items-center gap-3 px-4 py-3.5 rounded-2xl transition-all duration-300 group relative overflow-hidden ${
                  isActive
                    ? 'bg-[#FFE815] text-black font-bold shadow-lg shadow-yellow-100 transform scale-[1.02]'
                    : 'text-gray-500 hover:bg-gray-50 hover:text-gray-900'
                }`}
              >
                <Icon className={`w-5 h-5 transition-colors ${isActive ? 'text-black' : 'text-gray-400 group-hover:text-gray-600'}`} />
                <span className="text-sm tracking-wide">{item.label}</span>
                {isActive && <Sparkles className="w-4 h-4 absolute right-3 text-black/20 animate-pulse" />}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="p-6 border-t border-gray-50">
        <button
          onClick={onLogout}
          className="w-full flex items-center gap-3 px-4 py-3 text-gray-500 hover:text-red-500 hover:bg-red-50 rounded-2xl transition-all duration-200 font-medium"
        >
          <LogOut className="w-5 h-5" />
          <span className="text-sm">退出登录</span>
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;
