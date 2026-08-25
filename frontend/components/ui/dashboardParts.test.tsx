// @vitest-environment jsdom
import React from 'react';
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { OrderStatus } from '../../types';
import {
  STATUS_META,
  StatusBadge,
  fillHourlySeries,
  getTrendHighlights,
  selectTrendPoints,
  statusMetaOf,
} from './dashboardParts';

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

  it('将单日时段补齐为 24 桶并合并重复小时', () => {
    const points = fillHourlySeries([
      { hour: 9, amount: 10, order_count: 1 },
      { hour: '09:00', amount: 5, order_count: 2 },
      { hour: 23, amount: 8, order_count: 1 },
    ], '2026-07-10');
    expect(points).toHaveLength(24);
    expect(points[9]).toMatchObject({ label: '09:00', amount: 15, orders: 3 });
    expect(points[10]).toMatchObject({ label: '10:00', amount: 0, orders: 0 });
    expect(points[23]).toMatchObject({ label: '23:00', amount: 8, orders: 1 });
  });

  it('标记订单峰低点与相邻时段营收快慢变化', () => {
    const points = [
      { date: '2026-07-10 09:00', label: '09:00', amount: 10, orders: 1 },
      { date: '2026-07-10 10:00', label: '10:00', amount: 35, orders: 4 },
      { date: '2026-07-10 11:00', label: '11:00', amount: 20, orders: 2 },
    ];
    const highlights = getTrendHighlights(points);
    expect(highlights.peakOrders?.label).toBe('10:00');
    expect(highlights.lowOrders?.label).toBe('09:00');
    expect(highlights.fastestGrowth?.delta).toBe(25);
    expect(highlights.slowestGrowth?.delta).toBe(-15);
  });

  it('当前小时只展示已发生点，洞察排除未结束小时', () => {
    const points = fillHourlySeries([
      { hour: 8, amount: 20, order_count: 2 },
      { hour: 9, amount: 40, order_count: 4 },
      { hour: 10, amount: 10, order_count: 1 },
    ], '2026-08-18');
    const selected = selectTrendPoints(points, 'hour', '2026-08-18', '2026-08-18', 10);

    expect(selected.chartPoints).toHaveLength(11);
    expect(selected.chartPoints.at(-1)?.label).toBe('10:00');
    expect(selected.highlightPoints.at(-1)?.label).toBe('09:00');
    expect(getTrendHighlights(selected.highlightPoints).slowestGrowth).toBeNull();
  });

  it('当前日期的日趋势保留图表点但排除当天洞察', () => {
    const points = [
      { date: '2026-08-17', label: '08-17', amount: 100, orders: 10 },
      { date: '2026-08-18', label: '08-18', amount: 20, orders: 2 },
    ];
    const selected = selectTrendPoints(points, 'day', '2026-08-18', '2026-08-18', 12);

    expect(selected.chartPoints).toHaveLength(2);
    expect(selected.highlightPoints).toHaveLength(1);
    expect(getTrendHighlights(selected.highlightPoints).peakOrders).toBeNull();
  });

  it('只有一个已完成点时不制造峰谷或涨跌结论', () => {
    expect(getTrendHighlights([
      { date: '2026-08-18 00:00', label: '00:00', amount: 10, orders: 1 },
    ])).toEqual({
      peakOrders: null,
      lowOrders: null,
      fastestGrowth: null,
      slowestGrowth: null,
    });
  });
});
