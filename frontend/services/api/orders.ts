import { del, get, post, put } from '../request';
import type {
  AdminStats,
  ApiResponse,
  BuyerBehaviorAnalytics,
  DashboardSummary,
  ItemMetricStatus,
  ItemPerformanceAnalytics,
  ItemTrafficAnalytics,
  Order,
  OrderAnalytics,
  OrderRefreshResponse,
  OrderSyncResponse,
  PaginatedResponse,
  TrafficAnalytics,
} from '../../types';

// Orders
export interface OrderListQuery {
  cookieId?: string;
  status?: string;
  search?: string;
  startDate?: string;
  endDate?: string;
  page?: number;
  pageSize?: number;
}

const ORDER_READ_TIMEOUT_MS = 15_000;

export const getOrders = async (
  query: OrderListQuery = {},
  signal?: AbortSignal,
): Promise<PaginatedResponse<Order>> => {
  const page = query.page ?? 1;
  const pageSize = query.pageSize ?? 20;
  const params: Record<string, string | number | undefined> = {
    page,
    page_size: pageSize,
    cookie_id: query.cookieId || undefined,
    status: query.status && query.status !== 'all' ? query.status : undefined,
    search: query.search?.trim() || undefined,
    start_date: query.startDate || undefined,
    end_date: query.endDate || undefined,
  };

  const res = await get<any>('/api/orders', params, signal, ORDER_READ_TIMEOUT_MS);

  // Handle backend response variations
  const orders = res.orders || res.data || [];
  return {
    success: true,
    data: orders,
    total: res.total ?? orders.length,
    page: res.page || page,
    page_size: res.page_size || pageSize,
    total_pages: res.total_pages ?? 1
  };
};

export const getOrderDetail = async (
  orderId: string,
  signal?: AbortSignal,
): Promise<{ success: boolean; data?: Order }> => {
  const result = await get<{ order?: Order; data?: Order }>(
    `/api/orders/${orderId}`,
    undefined,
    signal,
    ORDER_READ_TIMEOUT_MS,
  );
  return {
    success: true,
    data: result.order || result.data
  };
};

export const updateOrder = async (orderId: string, data: Partial<Order>): Promise<ApiResponse> => {
  return put(`/api/orders/${orderId}`, data);
};

export const deleteOrder = async (orderId: string): Promise<ApiResponse> => {
  return del(`/api/orders/${orderId}`);
};

export const syncOrders = async (cookieId?: string, days: number = 90): Promise<OrderSyncResponse> => {
  const token = localStorage.getItem('auth_token');
  const response = await fetch('/api/orders/sync', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({ cookie_id: cookieId || null, days }),
  });
  const result = await response.json();
  if (!response.ok && response.status !== 409) {
    throw new Error(result?.message || result?.detail || `订单同步失败 (${response.status})`);
  }
  return result as OrderSyncResponse;
};

export const syncSingleOrder = async (orderId: string): Promise<OrderRefreshResponse> => {
  const token = localStorage.getItem('auth_token');
  const response = await fetch(`/api/orders/${orderId}/refresh`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
  });
  const result = await response.json();
  if (response.status === 401) {
    localStorage.removeItem('auth_token');
    window.dispatchEvent(new Event('auth:logout'));
  }
  if (!response.ok && response.status !== 409) {
    throw new Error(result?.message || result?.detail || `订单刷新失败 (${response.status})`);
  }
  return result as OrderRefreshResponse;
};

export const manualShipOrder = async (orderIds: string[], shipMode: 'status_only' | 'full_delivery', content?: string): Promise<any> => {
    return post('/api/orders/manual-ship', {
        order_ids: orderIds,
        ship_mode: shipMode,
        custom_content: content
    });
}

// 两个并列契约：程序化调用传 JSON 订单数组；电子表格上传传仅含 .xlsx 的 FormData。
export type ProgrammaticOrderImportPayload = Partial<Order>[];
export type SpreadsheetOrderImportPayload = FormData;
export type OrderImportPayload =
  | ProgrammaticOrderImportPayload
  | SpreadsheetOrderImportPayload;

export const importOrders = async (data: OrderImportPayload): Promise<any> => {
  return post('/api/orders/import', data);
}

// Stats
export const getAdminStats = async (): Promise<AdminStats> => {
  return get('/admin/stats');
};

export const getDashboardSummary = async (params: {
  range: 'today' | 'yesterday' | '3days' | '7days' | '30days' | 'custom';
  start_date?: string;
  end_date?: string;
}, signal?: AbortSignal): Promise<DashboardSummary> => {
  return get('/api/dashboard/summary', params, signal);
};

export const getOrderAnalytics = async (daysOrParams: number | {start_date: string; end_date: string} = 7): Promise<OrderAnalytics> => {
    let params: {start_date: string; end_date: string};

    if (typeof daysOrParams === 'number') {
        const endDate = new Date();
        const startDate = new Date();
        startDate.setDate(startDate.getDate() - daysOrParams);
        params = {
            start_date: startDate.toISOString().split('T')[0],
            end_date: endDate.toISOString().split('T')[0]
        };
    } else {
        params = daysOrParams;
    }

    return get('/analytics/orders', params);
}

export const getValidOrders = async (dateRange: {start_date: string; end_date: string}): Promise<Order[]> => {
    const res = await get<any>('/analytics/orders/valid', {
        start_date: dateRange.start_date,
        end_date: dateRange.end_date
    });
    return res.orders || [];
}

// 经营驾驶舱：订单时段分析。租户隔离由后端按登录用户强制执行。
export const getTrafficAnalytics = async (
  dateRange: { start_date: string; end_date: string },
  signal?: AbortSignal,
): Promise<TrafficAnalytics> => {
    return get('/analytics/traffic', {
        start_date: dateRange.start_date,
        end_date: dateRange.end_date,
    }, signal);
}

// 经营驾驶舱：买家行为分析。租户隔离由后端按登录用户强制执行。
export const getBuyerBehaviorAnalytics = async (
  dateRange: { start_date: string; end_date: string },
  signal?: AbortSignal,
): Promise<BuyerBehaviorAnalytics> => {
    return get('/analytics/buyers', {
        start_date: dateRange.start_date,
        end_date: dateRange.end_date,
    }, signal);
}

export const getItemPerformanceAnalytics = async (
  dateRange: { start_date: string; end_date: string },
  signal?: AbortSignal,
): Promise<ItemPerformanceAnalytics> => {
    return get('/analytics/items/performance', {
        start_date: dateRange.start_date,
        end_date: dateRange.end_date,
    }, signal);
}

export const getItemTrafficAnalytics = async (
  dateRange: { start_date: string; end_date: string },
  signal?: AbortSignal,
): Promise<ItemTrafficAnalytics> => {
    return get('/analytics/items/traffic', {
        start_date: dateRange.start_date,
        end_date: dateRange.end_date,
    }, signal);
}

export const getItemMetricStatus = async (signal?: AbortSignal): Promise<ItemMetricStatus> => {
    return get('/analytics/items/metrics/status', undefined, signal);
};
