// Cards
export interface Card {
  id: number;
  name: string;
  type: 'api' | 'text' | 'data' | 'image';
  description?: string;
  enabled: boolean;
  // 文本类型
  text_content?: string;
  // 批量数据类型
  data_content?: string;
  // API 类型配置
  api_config?: {
    url: string;
    method: 'GET' | 'POST';
    timeout?: number;
    headers?: string;
    params?: string;
  };
  // 图片类型
  image_url?: string;
  // 通用配置
  delay_seconds?: number;
  // 多规格配置
  is_multi_spec?: boolean;
  spec_name?: string;
  spec_value?: string;
  created_at: string;
  updated_at: string;
}

// Items
export interface Item {
  id: string | number;
  cookie_id: string;
  item_id: string;
  item_title?: string;
  item_description?: string;
  item_price?: string;
  item_image?: string;
  platform_item_status?: number | null;
  catalog_active?: boolean;
  catalog_last_seen_at?: string | null;
  catalog_metadata?: string;
  item_category?: string;
  item_detail?: string;
  is_multi_spec?: number | boolean;
  multi_quantity_delivery?: number | boolean;
  created_at?: string;
  updated_at?: string;
}

// Rules
export interface ShippingRule {
  id: string;
  name: string;
  item_keyword: string; // Matches item title
  card_group_id: number; // ID from Card list
  card_group_name?: string; // UI helper
  priority: number;
  enabled: boolean;
}

export interface ReplyRule {
  id: string;
  keyword: string;
  reply_content: string;
  match_type: 'exact' | 'fuzzy';
  enabled: boolean;
}

// Stats
export interface AdminStats {
  total_users: number;
  total_cookies: number;
  active_cookies: number;
  total_cards: number;
  total_keywords: number;
  total_orders: number;
}

export interface OrderAnalytics {
  revenue_stats: {
    total_amount: number;
    total_orders: number;
  };
  daily_stats: Array<{ date: string; amount: number; order_count?: number }>;
  item_stats?: Array<{
    item_id: string;
    order_count: number;
    total_amount: number;
    avg_amount: number;
  }>;
  // 按订单状态聚合（受仪表盘 include_statuses 限定为待发货/已发货/已完成）
  status_stats?: Array<{ status: string; count: number; amount: number }>;
  // 按收货城市聚合的地区分布（后端按订单量降序 Top 50，仅运营地理统计）
  city_stats?: Array<{ city: string; order_count: number; total_amount: number }>;
}

export interface DashboardSummary {
  success: boolean;
  scope: 'user' | 'system';
  range: {
    start_date: string;
    end_date: string;
    previous_start_date: string;
    previous_end_date: string;
  };
  stats: AdminStats;
  current: OrderAnalytics;
  previous: OrderAnalytics;
  item_names: Record<string, string>;
}

// 经营驾驶舱：时段流量分析（按真实成交时间 ordered_at_utc 分东八区小时/星期）
export interface TrafficAnalytics {
  // 覆盖率：窗口内有效订单里有多少笔带成交时间，用于标注时段分布的可信度
  coverage: {
    total_orders: number;
    with_ordered_at: number;
    coverage_rate: number;
  };
  // hour 为东八区 0-23；后端仅返回有数据的小时，缺口由前端补零
  hourly: Array<{ hour: number; order_count: number; amount: number }>;
  // weekday 为 strftime('%w') 字符串，'0'=周日 ... '6'=周六
  weekday: Array<{ weekday: string; order_count: number; amount: number }>;
}

// 经营驾驶舱：买家行为分析（仅订单可直接得出的行为量，不刻画客户画像）
export interface BuyerBehaviorAnalytics {
  summary: {
    total_buyers: number;
    repeat_buyers: number;
    repeat_rate: number;
  };
  // order_count=下单次数，buyer_count=该次数对应的买家数
  frequency: Array<{ order_count: number; buyer_count: number }>;
  // 贡献榜（后端按金额降序 Top 20）
  top_buyers: Array<{
    buyer_id: string;
    buyer_nickname: string;
    order_count: number;
    total_amount: number;
  }>;
}
