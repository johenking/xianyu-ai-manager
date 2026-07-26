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

export type ItemIdentity = 'snapshot' | 'catalog_fallback' | 'missing';
export type BuyerIdentity = 'snapshot' | 'profile' | 'history_unsaved' | 'missing';

export interface Order {
  id: string;
  order_id: string;
  cookie_id: string;
  item_id: string;
  item_title?: string;
  item_image?: string;
  item_price?: string;
  /** 商品展示信息来源：成交快照 / 目录兜底 / 缺失 */
  item_identity?: ItemIdentity;
  item_title_source?: string;
  item_image_source?: string;
  buyer_id: string;
  buyer_display_name?: string;
  buyer_avatar_url?: string;
  /** 买家身份来源：订单快照 / 客户档案 / 历史未保存 / 缺失 */
  buyer_identity?: BuyerIdentity;
  is_bargain?: boolean;
  quantity: number;
  amount: string;
  /** 规范化实付金额（分），未解析成功为 null，不用 0 冒充 */
  paid_amount_fen?: number | null;
  ordered_at_utc?: number | null;
  ordered_at_source?: string;
  status: OrderStatus;
  order_status?: OrderStatus;
  // 收货隐私字段只在详情接口返回，列表行恒为空
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
