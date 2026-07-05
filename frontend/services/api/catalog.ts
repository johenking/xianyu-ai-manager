import { del, get, post, put } from '../request';
import type { ApiResponse, Card, Item, ReplyRule, ShippingRule } from '../../types';

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
export const getReplyRules = async (cookieId?: string): Promise<ReplyRule[]> => {
    if (!cookieId) return [];
    const res = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(res) ? res : [];
    return keywords.map((item: any, index: number) => ({
        id: String(index),
        keyword: item.keyword || '',
        reply_content: item.reply || '',
        match_type: 'exact' as const,
        enabled: true
    }));
}

export const updateReplyRule = async (rule: Partial<ReplyRule>, cookieId: string): Promise<any> => {
    // 获取现有关键词
    const existing = await get<any>(`/keywords-with-item-id/${cookieId}`);
    const keywords = Array.isArray(existing) ? existing : [];

    // 更新或添加关键词
    if (rule.id) {
        const index = parseInt(rule.id);
        if (index >= 0 && index < keywords.length) {
            keywords[index] = {
                keyword: rule.keyword,
                reply: rule.reply_content,
                item_id: ''
            };
        }
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
    const index = parseInt(id);
    if (index >= 0 && index < keywords.length) {
        keywords.splice(index, 1);
    }
    return post(`/keywords-with-item-id/${cookieId}`, { keywords });
}
