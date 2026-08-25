import React, { useEffect, useId, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  AlertTriangle,
  CheckCircle2,
  Gift,
  PackageOpen,
  Power,
  X,
} from 'lucide-react';
import type { Card, Item } from '../../types';

// Keep the historical `card` value for ItemList/OrderList callers. The delivery
// workbench translates it to the backend's atomic `resource` mode.
export type DeliveryMode = 'off' | 'card' | 'invite';

const toBool = (value: unknown) => value === true || value === 1 || value === '1';

export const deliveryModeOf = (item: Item): DeliveryMode => {
  if (item.delivery_mode === 'invite') return 'invite';
  if (item.delivery_mode === 'resource') return 'card';
  if (item.delivery_mode === 'off') return 'off';
  if (toBool(item.invite_auto_fulfillment)) return 'invite';
  if (item.delivery_card_id || item.delivery_resource_id) return 'card';
  return 'off';
};

export const deliveryCardNameOf = (item: Item, cards: Card[]): string => {
  const selectedId = item.delivery_card_id ?? item.delivery_resource_id;
  const card = cards.find((entry) => entry.id === selectedId);
  return card?.name || (selectedId ? `资源 #${selectedId}` : '');
};

export interface ResourceHealth {
  ready: boolean;
  label: string;
  detail: string;
}

export const resourceHealthOf = (card: Card | undefined): ResourceHealth => {
  if (!card) return { ready: false, label: '资源不存在', detail: '原绑定资源已不存在' };
  if (card.enabled === false) return { ready: false, label: '已停用', detail: '启用后才能绑定商品' };
  if (card.type === 'data' && Number((card.stats || card.stock_stats)?.available || 0) < 1) {
    return { ready: false, label: '库存不足', detail: '补货后才能绑定商品' };
  }
  if (card.type === 'api' && card.api_validation_status !== 'validated') {
    return { ready: false, label: '待验证', detail: '通过连接验证后才能绑定商品' };
  }
  if (card.type === 'text' && !String(card.text_content || '').trim()) {
    return { ready: false, label: '内容缺失', detail: '补全固定资料后才能绑定商品' };
  }
  if (card.type === 'image' && !String(card.image_url || '').trim()) {
    return { ready: false, label: '图片缺失', detail: '补全图片后才能绑定商品' };
  }
  return { ready: true, label: '可用', detail: '付款核验通过后按此资源交付' };
};

export const DeliveryModeBadge: React.FC<{ item: Item; cards: Card[] }> = ({ item, cards }) => {
  const mode = deliveryModeOf(item);
  if (mode === 'invite') {
    return (
      <span className="inline-flex min-h-7 items-center gap-1 rounded-lg bg-violet-50 px-2.5 text-xs font-bold text-violet-700">
        <Gift className="h-3.5 w-3.5" aria-hidden="true" />邀请重置
      </span>
    );
  }
  if (mode === 'card') {
    const selectedId = item.delivery_card_id ?? item.delivery_resource_id;
    const card = cards.find((entry) => entry.id === selectedId);
    const health = resourceHealthOf(card);
    if (!health.ready) {
      return (
        <span className="inline-flex min-h-7 items-center gap-1 rounded-lg bg-red-50 px-2.5 text-xs font-bold text-red-700">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />{health.label}
        </span>
      );
    }
    return (
      <span className="inline-flex min-h-7 max-w-[190px] items-center gap-1 rounded-lg bg-emerald-50 px-2.5 text-xs font-bold text-emerald-700">
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate">{deliveryCardNameOf(item, cards)}</span>
      </span>
    );
  }
  const explicitlyOff = item.delivery_mode === 'off';
  return (
    <span className="inline-flex min-h-7 items-center rounded-lg bg-gray-100 px-2.5 text-xs font-bold text-gray-600">
      {explicitlyOff ? '已关闭' : '未配置'}
    </span>
  );
};

interface DeliverySettingModalProps {
  title: string;
  subtitle: string;
  cards: Card[];
  initialMode: DeliveryMode;
  initialCardId?: number | null;
  saving?: boolean;
  onClose: () => void;
  onSubmit: (mode: DeliveryMode, cardId: number | null) => void;
}

export const DeliverySettingModal: React.FC<DeliverySettingModalProps> = ({
  title,
  subtitle,
  cards,
  initialMode,
  initialCardId = null,
  saving = false,
  onClose,
  onSubmit,
}) => {
  const headingId = useId();
  const healthyCards = useMemo(
    () => cards.filter((card) => resourceHealthOf(card).ready),
    [cards],
  );
  const [mode, setMode] = useState<DeliveryMode>(initialMode);
  const [cardId, setCardId] = useState<number | null>(
    initialCardId ?? healthyCards[0]?.id ?? null,
  );

  useEffect(() => {
    if (mode === 'card' && !healthyCards.some((card) => card.id === cardId)) {
      setCardId(healthyCards[0]?.id ?? null);
    }
  }, [mode, cardId, healthyCards]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !saving) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, saving]);

  const options: Array<{
    value: DeliveryMode;
    label: string;
    hint: string;
    icon: React.ComponentType<{ className?: string }>;
  }> = [
    { value: 'card', label: '资源发货', hint: '发送固定资料、一次一密、图片或幂等 API 结果', icon: PackageOpen },
    { value: 'invite', label: '邀请重置', hint: '交由邀请服务履约，本地资源不会被消耗', icon: Gift },
    { value: 'off', label: '关闭自动发货', hint: '付款后不自动发送，也不会回落到其他资源', icon: Power },
  ];
  const canSubmit = mode !== 'card' || healthyCards.some((card) => card.id === cardId);

  return createPortal(
    <div className="delivery-sheet-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !saving) onClose();
    }}>
      <section
        className="delivery-sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
      >
        <header className="delivery-sheet-header">
          <div className="min-w-0">
            <h3 id={headingId} className="text-xl font-extrabold text-gray-950 sm:text-2xl">{title}</h3>
            <p className="mt-1 truncate text-sm text-gray-500">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="delivery-icon-button"
            aria-label="关闭"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="delivery-sheet-body">
          <fieldset>
            <legend className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-gray-400">发货方式</legend>
            <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
              {options.map(({ value, label, hint, icon: Icon }, index) => (
                <label
                  key={value}
                  className={`flex min-h-[76px] cursor-pointer items-center gap-3 px-4 py-3 transition-colors ${
                    index > 0 ? 'border-t border-gray-100' : ''
                  } ${mode === value ? 'bg-[#FFFBE2]' : 'hover:bg-gray-50'}`}
                >
                  <input
                    type="radio"
                    name="delivery-mode"
                    value={value}
                    checked={mode === value}
                    onChange={() => setMode(value)}
                    className="h-4 w-4 accent-[#111111]"
                  />
                  <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${mode === value ? 'bg-[#FFE815]' : 'bg-gray-100 text-gray-500'}`}>
                    <Icon className="h-5 w-5" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-bold text-gray-950">{label}</span>
                    <span className="mt-0.5 block text-xs leading-5 text-gray-500">{hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          {mode === 'card' && (
            <fieldset className="mt-6">
              <legend className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-gray-400">选择可用资源</legend>
              {cards.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-gray-300 px-4 py-8 text-center text-sm text-gray-500">
                  资源库还是空的，请先新建资源。
                </div>
              ) : (
                <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
                  {cards.map((card, index) => {
                    const health = resourceHealthOf(card);
                    const stats = card.stats || card.stock_stats;
                    return (
                      <label
                        key={card.id}
                        className={`flex min-h-[68px] items-center gap-3 px-4 py-3 ${
                          index > 0 ? 'border-t border-gray-100' : ''
                        } ${health.ready ? 'cursor-pointer hover:bg-gray-50' : 'cursor-not-allowed bg-gray-50/70 opacity-65'} ${
                          cardId === card.id && health.ready ? 'bg-[#FFFBE2]' : ''
                        }`}
                      >
                        <input
                          type="radio"
                          name="delivery-resource"
                          aria-label={`${card.name} ${health.label}`}
                          checked={cardId === card.id}
                          disabled={!health.ready}
                          onChange={() => setCardId(card.id)}
                          className="h-4 w-4 accent-[#111111]"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-bold text-gray-900">{card.name}</span>
                          <span className="mt-0.5 block truncate text-xs text-gray-500">
                            {card.type === 'data' ? `可用 ${Number(stats?.available || 0)} 条` : health.detail}
                          </span>
                        </span>
                        <span className={`shrink-0 rounded-lg px-2 py-1 text-xs font-bold ${health.ready ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>
                          {health.label}
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
            </fieldset>
          )}
        </div>

        <footer className="delivery-sheet-footer">
          <button type="button" onClick={onClose} disabled={saving} className="delivery-secondary-button">
            取消
          </button>
          <button
            type="button"
            disabled={!canSubmit || saving}
            onClick={() => onSubmit(mode, mode === 'card' ? cardId : null)}
            className="ios-btn-primary min-h-11 flex-1 rounded-xl px-5 text-sm font-bold disabled:cursor-not-allowed"
          >
            {saving ? '保存中…' : '保存设置'}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
};
