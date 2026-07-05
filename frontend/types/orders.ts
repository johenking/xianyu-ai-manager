// Orders
export type OrderStatus =
  | 'unknown'
  | 'processing'
  | 'pending_ship'
  | 'shipped'
  | 'completed'
  | 'cancelled'
  | 'refunding'
  | 'refunded'
  | 'refund_cancelled';

export interface Order {
  id: string;
  order_id: string;
  cookie_id: string;
  item_id: string;
  item_title?: string;
  item_image?: string;
  item_price?: string;
  buyer_id: string;
  quantity: number;
  amount: string;
  status: OrderStatus;
  receiver_name?: string;
  receiver_phone?: string;
  receiver_address?: string;
  receiver_city?: string;
  platform_status_code?: string;
  platform_status_text?: string;
  status_source?: string;
  status_synced_at?: string;
  last_sync_error?: string;
  created_at?: string;
  updated_at?: string;
}

export interface OrderSyncSummary {
  total_seen: number;
  discovered: number;
  status_updated: number;
  details_updated: number;
  unchanged: number;
  failed: number;
}

export interface OrderSyncResponse {
  success: boolean;
  partial?: boolean;
  message: string;
  days: number;
  summary: OrderSyncSummary;
  requires_login: string[];
  accounts: Array<{ cookie_id: string; success: boolean; message?: string }>;
}
