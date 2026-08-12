// @vitest-environment jsdom
import React from 'react';
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { OrderStatus } from '../../types';
import { STATUS_META, StatusBadge, statusMetaOf } from './dashboardParts';

/** 与 types/orders.ts 的 OrderStatus 一一对应；新增取值必须同步补进 STATUS_META */
const EXPECTED_LABELS: Record<OrderStatus, string> = {
  unknown: '待核对',
  processing: '处理中',
  pending_ship: '待发货',
  shipped: '已发货',
  completed: '已完成',
  cancelled: '已取消',
  refunding: '退款中',
  refunded: '已退款',
  refund_cancelled: '退款已关闭',
};

describe('订单状态语义色唯一真源', () => {
  afterEach(() => cleanup());

  it('覆盖 OrderStatus 的全部取值并使用统一标签', () => {
    expect(Object.keys(STATUS_META).sort()).toEqual(Object.keys(EXPECTED_LABELS).sort());
    for (const [status, label] of Object.entries(EXPECTED_LABELS)) {
      expect(statusMetaOf(status).label).toBe(label);
    }
  });

  it('退款与关闭这两组近义状态使用互不相同的占比条颜色', () => {
    const bars = Object.values(STATUS_META).map((meta) => meta.bar);
    expect(new Set(bars).size).toBe(bars.length);
  });

  it('未识别的状态回落到「待核对」而不是显示原始英文码', () => {
    expect(statusMetaOf('some_future_status').label).toBe('待核对');
    render(<StatusBadge status="some_future_status" />);
    expect(screen.getByText('待核对')).toBeInTheDocument();
  });
});
