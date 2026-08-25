import { del, get, post, put } from '../request';
import type {
  ApiResponse,
  ApiValidationResult,
  Card,
  DeliveryModeBatchResult,
  FulfillmentRecordList,
  FulfillmentResendResult,
  Item,
  ItemDeliveryMode,
  ReplyRule,
  ShippingRule,
  StockImportResult,
} from '../../types';

// Cards
export const getCards = async (): Promise<Card[]> => {
  const res = await get<any>('/cards');
  return Array.isArray(res) ? res : (res.cards || []);
};

export const createCard = async (data: Partial<Card>): Promise<{ id: number; message: string }> => {
  return post('/cards', data);
};

export const updateCard = async (cardId: string | number, data: Partial<Card>): Promise<ApiResponse> => {
  return put(`/cards/${cardId}`, data);
};

export const deleteCard = async (cardId: string | number): Promise<ApiResponse> => {
  return del(`/cards/${cardId}`);
};

export const getCardDetails = async (cardId: string | number): Promise<any> => {
  return get(`/cards/${cardId}`);
};

export type StockImportFormat = 'lines' | 'txt' | 'csv';

export const importCardStock = async (
  cardId: string | number,
  payload: { format: StockImportFormat; content: string },
): Promise<StockImportResult> => post(`/cards/${cardId}/stock/import`, payload);

export const validateCardApi = async (
  cardId: string | number,
  token?: string,
): Promise<ApiValidationResult> => post(
  `/cards/${cardId}/api/validate`,
  token ? { api_token: token } : {},
);

// Items
export const getItems = async (): Promise<Item[]> => {
    const res = await get<any>('/items');
    return Array.isArray(res) ? res : (res.items || []);
}

export const getItemsByCookie = async (cookieId: string): Promise<Item[]> => {
    const res = await get<any>(`/items/cookie/${cookieId}`);
    return Array.isArray(res) ? res : (res.items || []);
}

export const syncItemsFromAccount = async (cookieId: string): Promise<any> => {
    return post('/items/get-all-from-account', { cookie_id: cookieId });
}

export const deleteItem = async (cookieId: string, itemId: string): Promise<any> => {
    return del(`/items/${cookieId}/${itemId}`);
}

export const updateItem = async (cookieId: string, itemId: string, data: any): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}`, data);
}

export const updateItemMultiSpec = async (cookieId: string, itemId: string, enabled: boolean): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}/multi-spec`, { is_multi_spec: enabled });
}

export const updateItemMultiQuantityDelivery = async (cookieId: string, itemId: string, enabled: boolean): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}/multi-quantity-delivery`, { multi_quantity_delivery: enabled });
}

// 自动发货：商品级绑定卡密（card_id 传 null 表示解除绑定）
export const updateItemDeliveryBinding = async (
    cookieId: string,
    itemId: string,
    cardId: number | null,
): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}/delivery-binding`, { card_id: cardId });
}

export const updateItemDeliveryBindingsBatch = async (
    cookieId: string,
    itemIds: string[],
    cardId: number | null,
): Promise<{ message: string; updated: number; failed: string[] }> => {
    return post('/items/delivery-bindings/batch', { cookie_id: cookieId, item_ids: itemIds, card_id: cardId });
}

// 邀请重置发货开关（后端一直存在，此前前端没有入口）
export const updateItemInviteAutoFulfillment = async (
    cookieId: string,
    itemId: string,
    enabled: boolean,
): Promise<any> => {
    return put(`/items/${cookieId}/${itemId}/invite-auto-fulfillment`, { invite_auto_fulfillment: enabled });
}

// 新工作台只走原子三态合同；旧接口仅保留给历史调用方。
export const updateItemDeliveryMode = async (
  cookieId: string,
  itemId: string,
  mode: ItemDeliveryMode,
  cardId: number | null = null,
): Promise<{ message?: string; item_id?: string }> => put(
  `/items/${cookieId}/${itemId}/delivery-mode`,
  { mode, ...(mode === 'resource' ? { card_id: cardId } : {}) },
);

export const updateItemDeliveryModesBatch = async (
  cookieId: string,
  itemIds: string[],
  mode: ItemDeliveryMode,
  cardId: number | null = null,
): Promise<DeliveryModeBatchResult> => post('/items/delivery-modes/batch', {
  cookie_id: cookieId,
  item_ids: itemIds,
  mode,
  ...(mode === 'resource' ? { card_id: cardId } : {}),
});

export const getFulfillmentRecords = async (
  state?: string,
): Promise<FulfillmentRecordList> => {
  const query = state && state !== 'all' ? `?state=${encodeURIComponent(state)}` : '';
  const res = await get<any>(`/fulfillment-records${query}`);
  return {
    items: Array.isArray(res) ? res : (res.items || []),
    total: Array.isArray(res) ? res.length : Number(res.total || 0),
  };
};

export const resendFulfillmentRecord = async (
  recordId: string | number,
): Promise<FulfillmentResendResult> => post(`/fulfillment-records/${recordId}/resend`, {});

// Rules - 发货规则 (使用正确的后端API)
export const getShippingRules = async (): Promise<ShippingRule[]> => {
    const res = await get<any>('/delivery-rules');
    const rules = Array.isArray(res) ? res : (res.data || res.rules || []);
    // 转换后端数据格式到前端格式
    return rules.map((item: any) => ({
        id: String(item.id),
        name: item.description || item.keyword || '',
        item_keyword: item.keyword || '',
        card_group_id: item.card_id || 0,
        card_group_name: item.card_name || '',
        priority: item.delivery_count || 1,
        enabled: item.enabled || false
    }));
}

export const updateShippingRule = async (rule: Partial<ShippingRule>): Promise<any> => {
    const payload = {
        keyword: rule.item_keyword,
        card_id: rule.card_group_id,
        delivery_count: rule.priority,
        enabled: rule.enabled ?? true,
        description: rule.name
    };
    return rule.id ? put(`/delivery-rules/${rule.id}`, payload) : post('/delivery-rules', payload);
}

export const deleteShippingRule = async (id: string): Promise<any> => del(`/delivery-rules/${id}`);

// Rules - 关键词回复规则 (使用关键词API)
// 后端 keywords 是“整表数组”契约、没有稳定行 ID。历史实现用数组下标定位删改，
// 在多标签页/多设备/管理端并发下会删错或改错另一条，并把他端修改整表覆盖。
// 这里改用“关键词+回复”内容作为稳定身份：id 由内容编码而来，删改前按内容在
// 最新列表里精确定位，命中数 ≠ 1 就抛错让用户刷新，绝不按陈旧下标盲改。
const REPLY_RULE_ID_SEPARATOR = '\u0000';

const encodeReplyRuleId = (keyword: string, reply: string): string =>
    `${keyword}${REPLY_RULE_ID_SEPARATOR}${reply}`;

const decodeReplyRuleId = (id: string): { keyword: string; reply: string } | null => {
    const separatorIndex = id.indexOf(REPLY_RULE_ID_SEPARATOR);
    if (separatorIndex < 0) return null;
    return {
        keyword: id.slice(0, separatorIndex),
        reply: id.slice(separatorIndex + REPLY_RULE_ID_SEPARATOR.length),
    };
};

const matchesReplyRule = (
    item: any,
    identity: { keyword: string; reply: string },
): boolean =>
    (item?.keyword || '') === identity.keyword && (item?.reply || '') === identity.reply;

export const getReplyRules = async (cookieId?: string): Promise<ReplyRule[]> => {
    if (!cookieId) return [];
    const res = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(res) ? res : [];
    return keywords.map((item: any) => ({
        id: encodeReplyRuleId(item.keyword || '', item.reply || ''),
        keyword: item.keyword || '',
        reply_content: item.reply || '',
        match_type: 'exact' as const,
        enabled: true
    }));
}

export const updateReplyRule = async (rule: Partial<ReplyRule>, cookieId: string): Promise<any> => {
    const existing = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(existing) ? existing : [];

    if (rule.id) {
        // 编辑：按“原始关键词+回复”内容精确定位，杜绝用陈旧下标改错行
        const identity = decodeReplyRuleId(rule.id);
        if (!identity) {
            throw new Error('关键词标识无效，请刷新后重试');
        }
        const matched = keywords.filter((item: any) => matchesReplyRule(item, identity));
        if (matched.length !== 1) {
            throw new Error('关键词列表已变化，请刷新后重试');
        }
        // 原地改写命中项，保留其 item_id 等既有字段
        matched[0].keyword = rule.keyword;
        matched[0].reply = rule.reply_content;
    } else {
        keywords.push({
            keyword: rule.keyword,
            reply: rule.reply_content,
            item_id: ''
        });
    }

    return post(`/keywords-with-item-id/${cookieId}`, { keywords });
}

export const deleteReplyRule = async (id: string, cookieId: string): Promise<any> => {
    const existing = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(existing) ? existing : [];
    const identity = decodeReplyRuleId(id);
    if (!identity) {
        throw new Error('关键词标识无效，请刷新后重试');
    }
    const matched = keywords.filter((item: any) => matchesReplyRule(item, identity));
    if (matched.length !== 1) {
        throw new Error('关键词列表已变化，请刷新后重试');
    }
    const remaining = keywords.filter((item: any) => !matchesReplyRule(item, identity));
    return post(`/keywords-with-item-id/${cookieId}`, { keywords: remaining });
}
