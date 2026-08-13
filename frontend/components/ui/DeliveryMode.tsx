import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { CreditCard, Gift, X } from 'lucide-react';
import type { Card, Item } from '../../types';

// 一个商品同一时刻只有一种发货方式：绑定卡密、邀请重置，或都不设（回落关键词兜底规则）。
export type DeliveryMode = 'off' | 'card' | 'invite';

const toBool = (value: unknown) => value === true || value === 1 || value === '1';

export const deliveryModeOf = (item: Item): DeliveryMode => {
  if (toBool(item.invite_auto_fulfillment)) return 'invite';
  if (item.delivery_card_id) return 'card';
  return 'off';
};

export const deliveryCardNameOf = (item: Item, cards: Card[]): string => {
  const card = cards.find((entry) => entry.id === item.delivery_card_id);
  return card?.name || (item.delivery_card_id ? `卡密 #${item.delivery_card_id}` : '');
};

export const DeliveryModeBadge: React.FC<{ item: Item; cards: Card[] }> = ({ item, cards }) => {
  const mode = deliveryModeOf(item);
  if (mode === 'invite') {
    return (
      <span className="inline-flex items-center gap-1 rounded-lg bg-violet-100 px-2 py-1 text-xs font-bold text-violet-700">
        <Gift className="h-3.5 w-3.5" aria-hidden="true" />邀请重置
      </span>
    );
  }
  if (mode === 'card') {
    return (
      <span className="inline-flex max-w-[180px] items-center gap-1 rounded-lg bg-emerald-100 px-2 py-1 text-xs font-bold text-emerald-700">
        <CreditCard className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate">{deliveryCardNameOf(item, cards)}</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-lg bg-gray-100 px-2 py-1 text-xs font-bold text-gray-500">
      关键词兜底
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
  const enabledCards = cards.filter((card) => card.enabled !== false);
  const [mode, setMode] = useState<DeliveryMode>(initialMode);
  const [cardId, setCardId] = useState<number | null>(initialCardId ?? enabledCards[0]?.id ?? null);

  useEffect(() => {
    if (mode === 'card' && cardId === null && enabledCards.length > 0) {
      setCardId(enabledCards[0].id);
    }
  }, [mode, cardId, enabledCards]);

  const options: Array<{ value: DeliveryMode; label: string; hint: string }> = [
    { value: 'card', label: '发送卡密', hint: '买家付款后自动把选定卡密发给买家' },
    { value: 'invite', label: '邀请重置', hint: '交由邀请服务履约，不消耗本地卡密库存' },
    { value: 'off', label: '不指定', hint: '回落到关键词管理里的发货规则（按商品标题匹配）' },
  ];

  const canSubmit = mode !== 'card' || cardId !== null;

  return createPortal(
    <div className="modal-overlay-centered">
      <div className="modal-container">
        <div className="modal-header">
          <div>
            <h3 className="text-2xl font-extrabold text-gray-900">{title}</h3>
            <p className="mt-1 text-sm text-gray-500">{subtitle}</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-xl hover:bg-gray-100 transition-colors" aria-label="关闭">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="modal-body">
          <div className="space-y-3">
            {options.map((option) => (
              <label
                key={option.value}
                className={`flex cursor-pointer items-start gap-3 rounded-2xl border p-4 transition-colors ${
                  mode === option.value ? 'border-yellow-400 bg-yellow-50' : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <input
                  type="radio"
                  name="delivery-mode"
                  className="mt-1"
                  checked={mode === option.value}
                  onChange={() => setMode(option.value)}
                />
                <span>
                  <span className="block text-sm font-bold text-gray-900">{option.label}</span>
                  <span className="mt-0.5 block text-xs leading-5 text-gray-500">{option.hint}</span>
                </span>
              </label>
            ))}
          </div>

          {mode === 'card' && (
            <div className="mt-4">
              <label className="mb-2 block text-sm font-bold text-gray-700" htmlFor="delivery-card-select">
                选择卡密
              </label>
              {enabledCards.length === 0 ? (
                <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-700">
                  还没有可用卡密，请先到「卡密资源库」新增。
                </p>
              ) : (
                <select
                  id="delivery-card-select"
                  className="ios-input w-full rounded-xl px-4 py-3 text-sm"
                  value={cardId ?? ''}
                  onChange={(event) => setCardId(Number(event.target.value))}
                >
                  {enabledCards.map((card) => (
                    <option key={card.id} value={card.id}>
                      {card.name}（{card.type}）
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}
        </div>

        <div className="mt-6 flex gap-3">
          <button
            type="button"
            onClick={onClose}
            className="min-h-11 flex-1 rounded-xl border border-gray-200 bg-white px-4 font-bold text-gray-700 hover:bg-gray-50"
          >
            取消
          </button>
          <button
            type="button"
            disabled={!canSubmit || saving}
            onClick={() => onSubmit(mode, mode === 'card' ? cardId : null)}
            className="ios-btn-primary min-h-11 flex-1 rounded-xl px-4 font-bold disabled:opacity-50"
          >
            {saving ? '保存中…' : '保存设置'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};
