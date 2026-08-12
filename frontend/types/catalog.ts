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
  invite_auto_fulfillment?: boolean;
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
  scope: 'user';
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

export interface AnalyticsCoverage {
  total_orders: number;
  coverage_rate: number;
}

export interface AmountCoverage extends AnalyticsCoverage {
  with_amount: number;
}

// 经营驾驶舱：订单时段分析（按已保存的平台订单时间分东八区小时/星期）
export interface TrafficAnalytics {
  // 覆盖率：窗口内有效订单里有多少笔带订单时间，用于标注时段分布的可信度
  coverage: {
    total_orders: number;
    with_ordered_at: number;
    coverage_rate: number;
  };
  time_coverage: {
    total_orders: number;
    with_ordered_at: number;
    coverage_rate: number;
  };
  amount_coverage: AmountCoverage;
  metric_source: 'order_transactions';
  time_source: 'order_snapshot_ordered_at';
  time_semantics: 'platform_order_recorded_at';
  // hour 为东八区 0-23；后端仅返回有数据的小时，缺口由前端补零
  hourly: Array<{ hour: number; order_count: number; amount: number }>;
  // weekday 为 strftime('%w') 字符串，'0'=周日 ... '6'=周六
  weekday: Array<{ weekday: string; order_count: number; amount: number }>;
  sufficient_data: boolean;
  data_requirement: { minimum_orders: number; minimum_time_coverage: number };
  insufficient_reason: string;
  recommendation: null | { type: 'transaction_timing'; hour: number; message: string };
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
  amount_coverage: AmountCoverage;
  metric_source: 'order_transactions';
}

export interface ItemPerformanceAnalytics {
  metric_source: 'order_transactions';
  amount_coverage: AmountCoverage;
  items: Array<{
    item_id: string;
    item_title: string;
    order_count: number;
    total_amount: number;
    avg_amount: number;
    orders_with_amount: number;
  }>;
}

export interface ItemTrafficAnalytics {
  metric_source: 'seller_backend_verified_snapshots';
  aggregation_semantics: 'counter_delta_between_consecutive_snapshots';
  time_precision: 'observation_window';
  timezone: 'Asia/Shanghai';
  schedule_interval_hours: number;
  snapshot_count: number;
  valid_snapshot_count: number;
  valid_observation_window_count: number;
  recommendation_window_count: number;
  recommendation_distinct_days: number;
  irregular_window_count: number;
  distinct_days: number;
  reset_count: number;
  totals: {
    exposure_delta: number;
    view_delta: number;
    want_delta: number;
  };
  observation_windows: Array<{
    start_hour: number;
    end_hour: number;
    day_span: number;
    crosses_midnight: boolean;
    window_count: number;
    average_duration_hours: number;
    minimum_duration_hours: number;
    maximum_duration_hours: number;
    exposure_delta: number;
    view_delta: number;
    want_delta: number;
  }>;
  hourly: Array<{
    hour: number;
    window_start_hour: number;
    window_end_hour: number;
    day_span: number;
    crosses_midnight: boolean;
    window_count: number;
    average_duration_hours: number;
    exposure_delta: number;
    view_delta: number;
    want_delta: number;
  }>;
  hourly_semantics: 'legacy_observation_window_end_hour';
  items: Array<{
    item_id: string;
    snapshot_count: number;
    observation_window_count: number;
    exposure_delta: number;
    view_delta: number;
    want_delta: number;
  }>;
  sufficient_data: boolean;
  data_requirement: {
    minimum_days: number;
    minimum_snapshots: number;
    minimum_observation_windows: number;
    minimum_window_hours: number;
    maximum_window_hours: number;
  };
  insufficient_reason: string;
  recommendation: null | {
    type: 'timing';
    semantics: 'observation_window';
    hour: number;
    start_hour: number;
    end_hour: number;
    crosses_midnight: boolean;
    average_duration_hours: number;
    precision: 'approximate_observation_window';
    message: string;
  };
}

export interface ItemMetricCollectionState {
  cookie_id: string;
  canary_success_count: number;
  enabled: boolean;
  collection_enabled: boolean;
  adapter_available: boolean;
  last_attempt_at: number | null;
  last_success_at: number | null;
  last_error_code: string;
  updated_at: number | null;
}

export interface ItemMetricStatus {
  adapter_available: boolean;
  enabled_accounts: number;
  accounts: ItemMetricCollectionState[];
}
