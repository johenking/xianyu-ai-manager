// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { get, post, put } from '../request';
import {
  deleteReplyRule,
  getFulfillmentRecords,
  getReplyRules,
  resendFulfillmentRecord,
  updateItemDeliveryMode,
  updateItemDeliveryModesBatch,
  updateReplyRule,
  validateCardApi,
} from './catalog';

vi.mock('../request', () => ({
  del: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

describe('关键词回复规则按内容定位（防下标漂移误删/误改）', () => {
  afterEach(() => vi.clearAllMocks());

  it('用内容编码 id，而不是数组下标', async () => {
    vi.mocked(get).mockResolvedValue([
      { keyword: '包邮', reply: '全国包邮', item_id: 'i1' },
      { keyword: '发货', reply: '48小时内', item_id: '' },
    ]);

    const rules = await getReplyRules('acct-1');

    expect(rules[0].id).not.toBe('0');
    expect(rules[0].id).toContain('包邮');
    expect(rules[1].id).toContain('发货');
  });

  it('删除按内容定位，即使服务端列表已插入新条目导致下标漂移也删对行', async () => {
    // 页面加载时的列表：目标 B 在下标 1
    vi.mocked(get).mockResolvedValueOnce([
      { keyword: 'A', reply: 'ra', item_id: '' },
      { keyword: 'B', reply: 'rb', item_id: '' },
    ]);
    const [, ruleB] = await getReplyRules('acct-1');

    // 删除前服务端已变化：前面插入了 X，B 现在在下标 2
    vi.mocked(get).mockResolvedValueOnce([
      { keyword: 'X', reply: 'rx', item_id: '' },
      { keyword: 'A', reply: 'ra', item_id: '' },
      { keyword: 'B', reply: 'rb', item_id: '' },
    ]);
    vi.mocked(post).mockResolvedValue({ success: true });

    await deleteReplyRule(ruleB.id, 'acct-1');

    const posted = vi.mocked(post).mock.calls[0][1] as { keywords: Array<{ keyword: string }> };
    // 陈旧下标 1 指向的是 A；内容定位删掉的必须是 B
    expect(posted.keywords.map((k) => k.keyword)).toEqual(['X', 'A']);
  });

  it('目标已被他端删除时报错并且不写回，避免误删', async () => {
    vi.mocked(get).mockResolvedValueOnce([
      { keyword: 'A', reply: 'ra', item_id: '' },
      { keyword: 'B', reply: 'rb', item_id: '' },
    ]);
    const [, ruleB] = await getReplyRules('acct-1');

    vi.mocked(get).mockResolvedValueOnce([
      { keyword: 'A', reply: 'ra', item_id: '' },
    ]);

    await expect(deleteReplyRule(ruleB.id, 'acct-1')).rejects.toThrow('关键词列表已变化');
    expect(post).not.toHaveBeenCalled();
  });

  it('编辑按原始内容定位并保留命中项的 item_id', async () => {
    vi.mocked(get).mockResolvedValueOnce([
      { keyword: 'A', reply: 'ra', item_id: 'ia' },
      { keyword: 'B', reply: 'rb', item_id: 'ib' },
    ]);
    const [, ruleB] = await getReplyRules('acct-1');

    vi.mocked(get).mockResolvedValueOnce([
      { keyword: 'A', reply: 'ra', item_id: 'ia' },
      { keyword: 'B', reply: 'rb', item_id: 'ib' },
    ]);
    vi.mocked(post).mockResolvedValue({ success: true });

    await updateReplyRule(
      { id: ruleB.id, keyword: 'B2', reply_content: 'rb2', match_type: 'exact', enabled: true },
      'acct-1',
    );

    const posted = vi.mocked(post).mock.calls[0][1] as {
      keywords: Array<{ keyword: string; reply: string; item_id: string }>;
    };
    expect(posted.keywords.find((k) => k.item_id === 'ib')).toMatchObject({
      keyword: 'B2',
      reply: 'rb2',
      item_id: 'ib',
    });
    // 另一条不受影响
    expect(posted.keywords.find((k) => k.item_id === 'ia')).toMatchObject({
      keyword: 'A',
      reply: 'ra',
    });
  });
});

describe('交付中心 API 客户端合同', () => {
  afterEach(() => vi.clearAllMocks());

  it('single and batch mode writes use the atomic resource contract', async () => {
    vi.mocked(put).mockResolvedValue({ message: 'ok', item_id: 'i-1' });
    vi.mocked(post).mockResolvedValue({ updated: ['i-1'], failed: [] });

    await updateItemDeliveryMode('acct-1', 'i-1', 'resource', 7);
    await updateItemDeliveryModesBatch('acct-1', ['i-1'], 'resource', 7);

    expect(put).toHaveBeenCalledWith('/items/acct-1/i-1/delivery-mode', {
      mode: 'resource',
      card_id: 7,
    });
    expect(post).toHaveBeenCalledWith('/items/delivery-modes/batch', {
      cookie_id: 'acct-1',
      item_ids: ['i-1'],
      mode: 'resource',
      card_id: 7,
    });
  });

  it('API validation sends api_token and never the legacy token field', async () => {
    vi.mocked(post).mockResolvedValue({ status: 'validated', message: 'ok' });

    await validateCardApi(9, 'fresh-secret');

    expect(post).toHaveBeenCalledWith('/cards/9/api/validate', {
      api_token: 'fresh-secret',
    });
    expect(vi.mocked(post).mock.calls[0][1]).not.toHaveProperty('token');
  });

  it('record filters and immutable-payload resend use the dedicated endpoints', async () => {
    vi.mocked(get).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(post).mockResolvedValue({ status: 'succeeded', event_id: 3 });

    await getFulfillmentRecords('manual_review');
    await resendFulfillmentRecord(77);

    expect(get).toHaveBeenCalledWith('/fulfillment-records?state=manual_review');
    expect(post).toHaveBeenCalledWith('/fulfillment-records/77/resend', {});
  });
});
