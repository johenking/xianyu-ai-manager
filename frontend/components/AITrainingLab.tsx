import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail, Item } from '../types';
import {
  AIReplyLabMessage,
  AITrainingRule,
  deleteAITrainingRule,
  getAIItemKnowledge,
  getAITrainingRules,
  getItemsByCookie,
  saveAIItemKnowledgeDraft,
  saveAITrainingRules,
  sendAITrainingMessage,
  setAITrainingRuleEnabled,
} from '../services/api';
import {
  AlertTriangle, Bot, Globe2, Loader2, Package, RefreshCw,
  Save, Send, Trash2, X,
} from 'lucide-react';
import { newKnowledgeEntry, normalizeItemKnowledge } from '../utils/itemKnowledge';

const PRESET_MESSAGES = [
  '这个商品怎么操作？',
  '这个价格是多少？',
  '有售后吗？',
  '多久能处理？',
  '还能优惠吗？',
];

const itemDetailPreview = (item?: Item) => {
  if (!item) return '请选择商品';
  const raw = (item.item_detail || item.item_description || '').trim();
  if (!raw) return '商品暂无详情';
  try {
    const parsed = JSON.parse(raw);
    const text = [parsed.title, parsed.desc, parsed.description]
      .filter((value) => typeof value === 'string' && value.trim())
      .join(' ');
    if (text) return text.slice(0, 180);
  } catch {
    // Plain-text product descriptions are expected.
  }
  return raw.replace(/\s+/g, ' ').slice(0, 180);
};

interface AITrainingLabProps {
  account: AccountDetail;
  initialItemId?: string;
  onClose: () => void;
  onSaved?: () => void;
}

const AITrainingLab: React.FC<AITrainingLabProps> = ({ account, initialItemId, onClose, onSaved }) => {
  const [items, setItems] = useState<Item[]>([]);
  const [selectedItemId, setSelectedItemId] = useState('');
  const [messages, setMessages] = useState<AIReplyLabMessage[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [buyerMessage, setBuyerMessage] = useState('这个商品怎么操作？');
  const [trainingRules, setTrainingRules] = useState<AITrainingRule[]>([]);
  const [ruleDraft, setRuleDraft] = useState('');
  const [ruleScope, setRuleScope] = useState<'item' | 'global'>('item');
  const [knowledgeSection, setKnowledgeSection] = useState<'pricing' | 'process' | 'after_sales' | 'forbidden' | 'notes'>('notes');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loadingItems, setLoadingItems] = useState(false);
  const [loadingRules, setLoadingRules] = useState(false);
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [savedMessage, setSavedMessage] = useState('');

  const selectedItem = useMemo(
    () => items.find((item) => item.item_id === selectedItemId),
    [items, selectedItemId]
  );
  const pendingRules = trainingRules.filter((rule) => !rule.id);

  useEffect(() => {
    let mounted = true;
    setLoadingItems(true);
    getItemsByCookie(account.id)
      .then((list) => {
        if (!mounted) return;
        setItems(list);
        const initial = list.find((item) => item.item_id === initialItemId) || list[0];
        if (initial) setSelectedItemId(initial.item_id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : '商品加载失败'))
      .finally(() => mounted && setLoadingItems(false));
    return () => { mounted = false; };
  }, [account.id, initialItemId]);

  const loadRules = async (itemId: string) => {
    if (!itemId) {
      setTrainingRules([]);
      return;
    }
    setLoadingRules(true);
    try {
      const result = await getAITrainingRules(account.id, itemId);
      setTrainingRules([...result.global_rules, ...result.item_rules]);
    } catch (err) {
      setError(err instanceof Error ? err.message : '训练规则加载失败');
    } finally {
      setLoadingRules(false);
    }
  };

  useEffect(() => {
    setMessages([]);
    setSessionId('');
    setWarnings([]);
    setSavedMessage('');
    setError('');
    void loadRules(selectedItemId);
  }, [selectedItemId]);

  const changeItem = (nextItemId: string) => {
    if (pendingRules.length > 0 && !window.confirm('有未保存的修正规则，切换商品会丢失这些修改。继续吗？')) {
      return;
    }
    setSelectedItemId(nextItemId);
  };

  const addRule = () => {
    const text = ruleDraft.trim();
    if (!text) return;
    const duplicate = trainingRules.some((rule) => rule.scope === ruleScope && rule.text === text);
    if (!duplicate) {
      setTrainingRules((current) => [...current, { scope: ruleScope, text, enabled: true }]);
    }
    setRuleDraft('');
    setSavedMessage('');
  };

  const sendMessage = async (message = buyerMessage) => {
    const text = message.trim();
    if (!text || !selectedItem) return;
    setSending(true);
    setError('');
    setSavedMessage('');
    setWarnings([]);
    setMessages((current) => [...current, { role: 'user', content: text }]);

    try {
      const result = await sendAITrainingMessage(account.id, {
        session_id: sessionId || undefined,
        message: text,
        item_id: selectedItem.item_id,
        training_rules: trainingRules.filter((rule) => rule.enabled !== false),
      });
      setSessionId(result.session_id);
      setWarnings(result.warnings || []);
      setMessages((current) => [...current, { role: 'assistant', content: result.reply }]);
      setBuyerMessage('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '训练测试失败');
      setMessages((current) => current.slice(0, -1));
    } finally {
      setSending(false);
    }
  };

  const retestLastBuyerMessage = () => {
    const lastBuyer = [...messages].reverse().find((message) => message.role === 'user');
    if (lastBuyer) void sendMessage(lastBuyer.content);
  };

  const saveRules = async () => {
    if (!selectedItemId || pendingRules.length === 0) return;
    setSaving(true);
    setError('');
    try {
      await saveAITrainingRules(account.id, selectedItemId, pendingRules);
      await loadRules(selectedItemId);
      setSavedMessage('修正规则已按选择的范围保存');
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存训练规则失败');
    } finally {
      setSaving(false);
    }
  };

  const addCorrectionToKnowledge = async () => {
    const text = ruleDraft.trim();
    if (!text || !selectedItem) return;
    setSaving(true);
    setError('');
    try {
      const profile = await getAIItemKnowledge(account.id, selectedItem.item_id);
      const base = normalizeItemKnowledge(
        Object.keys(profile.draft || {}).length > 0 ? profile.draft : profile.published
      );
      const entry = newKnowledgeEntry(text);
      const next = { ...base, [knowledgeSection]: [...base[knowledgeSection], entry] };
      await saveAIItemKnowledgeDraft(account.id, selectedItem.item_id, next);
      setRuleDraft('');
      setSavedMessage(`已补充到当前商品的${knowledgeSection === 'pricing' ? '规格与价格' : knowledgeSection === 'process' ? '操作流程' : knowledgeSection === 'after_sales' ? '售后边界' : knowledgeSection === 'forbidden' ? '禁止说法' : '其他补充'}草稿`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '补充商品知识失败');
    } finally {
      setSaving(false);
    }
  };

  const removeRule = async (rule: AITrainingRule) => {
    setError('');
    if (!rule.id) {
      setTrainingRules((current) => current.filter((item) => item !== rule));
      return;
    }
    try {
      await deleteAITrainingRule(account.id, rule.id);
      setTrainingRules((current) => current.filter((item) => item.id !== rule.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除训练规则失败');
    }
  };

  const toggleRule = async (rule: AITrainingRule) => {
    const enabled = rule.enabled === false;
    if (!rule.id) {
      setTrainingRules((current) => current.map((item) => item === rule ? { ...item, enabled } : item));
      return;
    }
    try {
      await setAITrainingRuleEnabled(account.id, rule.id, enabled);
      setTrainingRules((current) => current.map((item) => item.id === rule.id ? { ...item, enabled } : item));
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新训练规则失败');
    }
  };

  return createPortal(
    <div className="modal-overlay-centered">
      <div className="modal-container" style={{ maxWidth: '1080px', width: '96vw', maxHeight: '92vh' }}>
        <div className="modal-header flex items-center justify-between w-full">
          <div>
            <h3 className="text-xl font-extrabold text-gray-900 flex items-center gap-2">
              <Bot className="w-6 h-6 text-yellow-500" />
              AI训练
            </h3>
            <p className="text-sm text-gray-500 mt-1">{account.nickname || account.remark || account.id}</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100" title="关闭">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="modal-body">
          <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
            <aside className="space-y-5">
              <section>
                <label className="block text-sm font-bold text-gray-700 mb-2">当前商品</label>
                <select
                  value={selectedItemId}
                  onChange={(event) => changeItem(event.target.value)}
                  className="w-full ios-input px-3 py-3 rounded-lg"
                  disabled={loadingItems}
                >
                  {items.length === 0 && <option value="">暂无商品</option>}
                  {items.map((item) => (
                    <option key={item.item_id} value={item.item_id}>{item.item_title || item.item_id}</option>
                  ))}
                </select>
                <div className="mt-3 border-l-4 border-yellow-400 pl-3 text-xs text-gray-600 leading-relaxed">
                  <div className="font-bold text-gray-900">{selectedItem?.item_title || '未选择商品'}</div>
                  <div className="mt-1">价格：{selectedItem?.item_price || '-'}</div>
                  <div className="mt-2 text-gray-500">{itemDetailPreview(selectedItem)}</div>
                </div>
              </section>

              <section>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm font-bold text-gray-700">生效规则</div>
                  {loadingRules && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
                </div>
                <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                  {trainingRules.map((rule, index) => (
                    <div key={rule.id || `${rule.scope}-${index}`} className={`border px-3 py-2 rounded-lg ${rule.enabled === false ? 'bg-gray-50 opacity-55' : 'bg-white'}`}>
                      <div className="flex items-start gap-2">
                        <button
                          type="button"
                          onClick={() => void toggleRule(rule)}
                          className={`mt-1 h-4 w-7 rounded-full p-0.5 transition-colors ${rule.enabled === false ? 'bg-gray-300' : 'bg-green-500'}`}
                          title={rule.enabled === false ? '启用规则' : '停用规则'}
                        >
                          <span className={`block h-3 w-3 rounded-full bg-white transition-transform ${rule.enabled === false ? '' : 'translate-x-3'}`} />
                        </button>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1 text-[11px] font-bold text-gray-500 mb-1">
                            {rule.scope === 'global' ? <Globe2 className="w-3 h-3" /> : <Package className="w-3 h-3" />}
                            {rule.scope === 'global' ? '全店通用' : '当前商品'}
                            {!rule.id && <span className="text-orange-600">未保存</span>}
                          </div>
                          <div className="text-xs text-gray-700 leading-relaxed">{rule.text}</div>
                        </div>
                        <button onClick={() => void removeRule(rule)} className="text-gray-400 hover:text-red-500" title="删除规则">
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                  {!loadingRules && trainingRules.length === 0 && (
                    <div className="border border-dashed border-gray-200 rounded-lg px-3 py-4 text-xs text-gray-400">暂无规则</div>
                  )}
                </div>
              </section>
            </aside>

            <main className="space-y-4 min-w-0">
              <div className="flex flex-wrap gap-2">
                {PRESET_MESSAGES.map((preset) => (
                  <button key={preset} type="button" onClick={() => setBuyerMessage(preset)} className="text-xs font-bold px-3 py-2 rounded-lg bg-gray-100 text-gray-700 hover:bg-gray-200">
                    {preset}
                  </button>
                ))}
              </div>

              <div className="h-[390px] overflow-y-auto rounded-lg border border-gray-200 bg-[#F7F8FA] p-4 space-y-3">
                {messages.length === 0 && <div className="h-full flex items-center justify-center text-sm font-bold text-gray-400">以买家身份发送咨询</div>}
                {messages.map((message, index) => (
                  <div key={`${message.role}-${index}`} className={`flex ${message.role === 'user' ? 'justify-start' : 'justify-end'}`}>
                    <div className={`max-w-[80%] rounded-lg px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${message.role === 'user' ? 'bg-white border border-gray-200' : 'bg-[#FFE815]'}`}>
                      {message.content}
                    </div>
                  </div>
                ))}
                {sending && <div className="flex justify-end"><div className="rounded-lg bg-[#FFE815] px-4 py-3 text-sm font-bold flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" />生成中</div></div>}
              </div>

              {warnings.length > 0 && (
                <div className="border border-orange-200 bg-orange-50 rounded-lg px-4 py-3 flex items-start gap-2 text-sm text-orange-900">
                  <AlertTriangle className="w-4 h-4 mt-0.5" />
                  <div><strong>命中风险词：</strong>{warnings.join('、')}</div>
                </div>
              )}
              {error && <div className="border border-red-200 bg-red-50 rounded-lg px-4 py-3 text-sm font-bold text-red-800">{error}</div>}
              {savedMessage && <div className="border border-green-200 bg-green-50 rounded-lg px-4 py-3 text-sm font-bold text-green-800">{savedMessage}</div>}

              <div className="grid grid-cols-[1fr_auto] gap-3">
                <textarea value={buyerMessage} onChange={(event) => setBuyerMessage(event.target.value)} className="w-full ios-input px-4 py-3 rounded-lg h-20 resize-none" placeholder="输入买家问题..." />
                <button type="button" onClick={() => void sendMessage()} disabled={sending || !buyerMessage.trim() || !selectedItem} className="ios-btn-primary px-5 py-3 rounded-lg font-bold flex items-center justify-center gap-2 disabled:opacity-60">
                  {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}发送测试
                </button>
              </div>

              <div className="border-t border-gray-200 pt-4">
                <div className="flex gap-2 mb-3" role="group" aria-label="规则范围">
                  <button type="button" onClick={() => setRuleScope('item')} className={`px-3 py-2 rounded-lg text-xs font-bold flex items-center gap-1 ${ruleScope === 'item' ? 'bg-black text-white' : 'bg-gray-100 text-gray-600'}`}><Package className="w-3.5 h-3.5" />当前商品</button>
                  <button type="button" onClick={() => setRuleScope('global')} className={`px-3 py-2 rounded-lg text-xs font-bold flex items-center gap-1 ${ruleScope === 'global' ? 'bg-black text-white' : 'bg-gray-100 text-gray-600'}`}><Globe2 className="w-3.5 h-3.5" />全店通用</button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_auto] gap-3">
                  <textarea value={ruleDraft} onChange={(event) => setRuleDraft(event.target.value)} className="w-full ios-input px-4 py-3 rounded-lg h-20 resize-none" placeholder="指出回复哪里有问题..." />
                  <button type="button" onClick={addRule} disabled={!ruleDraft.trim()} className="px-5 py-3 rounded-lg font-bold bg-black text-white disabled:opacity-60">记住修正</button>
                  <button type="button" onClick={retestLastBuyerMessage} disabled={sending || !messages.some((message) => message.role === 'user')} className="px-4 py-3 rounded-lg font-bold bg-gray-100 text-gray-700 flex items-center gap-2 disabled:opacity-60"><RefreshCw className="w-4 h-4" />再测</button>
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-3">
                  <select value={knowledgeSection} onChange={(event) => setKnowledgeSection(event.target.value as typeof knowledgeSection)} className="ios-input px-3 py-2 rounded-lg text-xs">
                    <option value="pricing">规格与价格</option>
                    <option value="process">操作流程</option>
                    <option value="after_sales">售后边界</option>
                    <option value="forbidden">禁止说法</option>
                    <option value="notes">其他补充</option>
                  </select>
                  <button type="button" onClick={() => void addCorrectionToKnowledge()} disabled={saving || !ruleDraft.trim() || !selectedItem} className="px-4 py-2 rounded-lg bg-yellow-100 text-yellow-900 text-xs font-bold flex items-center gap-2 disabled:opacity-50">
                    <Package className="w-4 h-4" />补充到商品知识草稿
                  </button>
                </div>
              </div>
            </main>
          </div>
        </div>

        <div className="modal-footer">
          <div className="flex gap-3 w-full">
            <button onClick={onClose} className="flex-1 px-6 py-3 rounded-lg font-bold bg-gray-100 text-gray-700">关闭</button>
            <button onClick={() => void saveRules()} disabled={saving || pendingRules.length === 0} className="flex-1 ios-btn-primary px-6 py-3 rounded-lg font-bold flex items-center justify-center gap-2 disabled:opacity-60">
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              保存修正{pendingRules.length > 0 ? ` (${pendingRules.length})` : ''}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default AITrainingLab;
