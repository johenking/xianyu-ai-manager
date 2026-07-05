import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail, Item } from '../types';
import {
  AIReplyLabMessage,
  AIRuleAudit,
  AITrainingRule,
  AITrainingRuleContext,
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
  AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronUp, Globe2,
  Loader2, MessageSquare, Package, RefreshCw, Save, Send, Trash2, X,
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
  const [ruleContext, setRuleContext] = useState<AITrainingRuleContext | null>(null);
  const [ruleAudit, setRuleAudit] = useState<AIRuleAudit | null>(null);
  const [regenerated, setRegenerated] = useState(false);
  const [guardedByRule, setGuardedByRule] = useState(false);
  const [guardReason, setGuardReason] = useState('');
  const [knowledgeSource, setKnowledgeSource] = useState<'draft' | 'published' | 'none'>('none');
  const [knowledgeVersion, setKnowledgeVersion] = useState(0);
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
  const [itemDetailExpanded, setItemDetailExpanded] = useState(false);
  const [auditExpanded, setAuditExpanded] = useState(false);
  const [expandedRuleKeys, setExpandedRuleKeys] = useState<Set<string>>(new Set());
  const [mobilePanel, setMobilePanel] = useState<'chat' | 'rules'>('chat');

  const selectedItem = useMemo(
    () => items.find((item) => item.item_id === selectedItemId),
    [items, selectedItemId]
  );
  const pendingRules = trainingRules.filter((rule) => !rule.id);
  const auditSummary = useMemo(() => {
    const results = ruleAudit?.results || [];
    return {
      followed: results.filter((entry) => entry.status === 'followed').length,
      violated: results.filter((entry) => entry.status === 'violated').length,
      notRelevant: results.filter((entry) => entry.status === 'not_relevant').length,
      unknown: results.filter((entry) => entry.status !== 'followed' && entry.status !== 'violated' && entry.status !== 'not_relevant').length,
      conflicts: ruleAudit?.conflicts.length || 0,
    };
  }, [ruleAudit]);

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
      setRuleContext(null);
      return;
    }
    setLoadingRules(true);
    try {
      const result = await getAITrainingRules(account.id, itemId);
      setTrainingRules([...result.global_rules, ...result.item_rules]);
      setRuleContext(result.context || null);
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
    setRuleAudit(null);
    setRegenerated(false);
    setKnowledgeSource('none');
    setKnowledgeVersion(0);
    setSavedMessage('');
    setError('');
    setItemDetailExpanded(false);
    setAuditExpanded(false);
    setExpandedRuleKeys(new Set());
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
      setRuleContext(result.rule_context || ruleContext);
      setRuleAudit(result.rule_audit || null);
      setAuditExpanded(false);
      setRegenerated(Boolean(result.regenerated));
      setGuardedByRule(Boolean(result.guarded_by_rule));
      setGuardReason(result.guard_reason || '');
      setKnowledgeSource(result.knowledge_source || 'none');
      setKnowledgeVersion(result.knowledge_version || 0);
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

  const ruleKey = (rule: AITrainingRule, index: number) => String(rule.id || `${rule.scope}-${index}-${rule.text}`);

  const toggleRuleText = (key: string) => {
    setExpandedRuleKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const renderAuditEntry = (entry: AIRuleAudit['results'][number]) => {
    const label = entry.status === 'followed' ? '已遵守' : entry.status === 'violated' ? '仍违反' : entry.status === 'not_relevant' ? '本问无关' : '未能确认';
    const tone = entry.status === 'followed' ? 'text-green-700' : entry.status === 'violated' ? 'text-red-700' : 'text-gray-600';
    return (
      <div key={String(entry.rule_id)} className="ai-training-audit-entry">
        <div className={`font-bold ${tone}`}>规则 R{entry.rule_id} · {label}</div>
        <div className="text-gray-600">{entry.text}</div>
        {entry.reason && <div className="text-gray-400">{entry.reason}</div>}
      </div>
    );
  };

  return createPortal(
    <div className="modal-overlay-centered">
      <div className="modal-container ai-training-modal">
        <div className="modal-header ai-training-header">
          <div>
            <h3 className="text-xl font-extrabold text-gray-900 flex items-center gap-2">
              <Bot className="w-6 h-6 text-yellow-500" />
              AI训练
            </h3>
            <p className="text-sm text-gray-500 mt-1">{account.nickname || account.remark || account.id}</p>
          </div>
          <button type="button" onClick={onClose} className="ai-training-icon-button" title="关闭" aria-label="关闭 AI 训练">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="ai-training-body">
          <div className="ai-training-mobile-tabs" role="tablist" aria-label="AI 训练视图">
            <button type="button" role="tab" aria-selected={mobilePanel === 'chat'} onClick={() => setMobilePanel('chat')} className={mobilePanel === 'chat' ? 'is-active' : ''}>
              <MessageSquare className="w-4 h-4" />对话训练
            </button>
            <button type="button" role="tab" aria-selected={mobilePanel === 'rules'} onClick={() => setMobilePanel('rules')} className={mobilePanel === 'rules' ? 'is-active' : ''}>
              <Package className="w-4 h-4" />商品与规则
            </button>
          </div>

          <div className="ai-training-workbench">
            <aside className={`ai-training-sidebar ${mobilePanel === 'rules' ? 'is-mobile-active' : ''}`}>
              <section className="ai-training-product-section">
                <label className="block text-sm font-bold text-gray-800 mb-2" htmlFor="ai-training-item">当前商品</label>
                <select
                  id="ai-training-item"
                  value={selectedItemId}
                  onChange={(event) => changeItem(event.target.value)}
                  className="w-full ios-input px-3 py-3 rounded-lg text-sm font-semibold"
                  disabled={loadingItems}
                >
                  {items.length === 0 && <option value="">暂无商品</option>}
                  {items.map((item) => (
                    <option key={item.item_id} value={item.item_id}>{item.item_title || item.item_id}</option>
                  ))}
                </select>
                <div className="ai-training-product-summary">
                  <div className="flex items-start justify-between gap-3">
                    <div className="font-bold text-gray-900 min-w-0 break-words">{selectedItem?.item_title || '未选择商品'}</div>
                    <span className="shrink-0 text-gray-500">¥{selectedItem?.item_price || '-'}</span>
                  </div>
                  <div className={`ai-training-product-description ${itemDetailExpanded ? 'is-expanded' : ''}`}>{itemDetailPreview(selectedItem)}</div>
                  {itemDetailPreview(selectedItem).length > 72 && (
                    <button type="button" onClick={() => setItemDetailExpanded((value) => !value)} className="ai-training-text-button" aria-expanded={itemDetailExpanded}>
                      {itemDetailExpanded ? '收起详情' : '展开详情'}
                      {itemDetailExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                    </button>
                  )}
                </div>
              </section>

              <section className="ai-training-rules-section">
                <div className="flex items-center justify-between mb-2 shrink-0">
                  <div className="text-sm font-bold text-gray-800">生效规则</div>
                  {loadingRules && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
                </div>
                {ruleContext && (
                  <div className="ai-training-rule-stats">
                    <span className="is-positive">已加载 {ruleContext.applied_count} / {ruleContext.total_count}</span>
                    <span>其他商品 {ruleContext.excluded_count}</span>
                    <span>已停用 {ruleContext.disabled_count}</span>
                  </div>
                )}
                <div className="ai-training-rule-list">
                  {trainingRules.map((rule, index) => {
                    const key = ruleKey(rule, index);
                    const isExpanded = expandedRuleKeys.has(key);
                    return (
                    <div key={key} className={`ai-training-rule-row ${rule.enabled === false ? 'is-disabled' : ''}`}>
                      <div className="flex items-start gap-2">
                        <button
                          type="button"
                          onClick={() => void toggleRule(rule)}
                          className={`ai-training-toggle ${rule.enabled === false ? '' : 'is-enabled'}`}
                          title={rule.enabled === false ? '启用规则' : '停用规则'}
                          aria-pressed={rule.enabled !== false}
                        >
                          <span />
                        </button>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-bold text-gray-500 mb-1">
                            {rule.scope === 'global' ? <Globe2 className="w-3 h-3" /> : <Package className="w-3 h-3" />}
                            {rule.scope === 'global' ? '全店通用' : '当前商品'}
                            {!rule.id && <span className="ai-training-unsaved-badge">待保存</span>}
                          </div>
                          <div className={`ai-training-rule-copy ${isExpanded ? 'is-expanded' : ''}`}>{rule.text}</div>
                          {rule.text.length > 72 && (
                            <button type="button" className="ai-training-text-button mt-1" onClick={() => toggleRuleText(key)} aria-expanded={isExpanded}>
                              {isExpanded ? '收起' : '展开'}
                            </button>
                          )}
                        </div>
                        <button type="button" onClick={() => void removeRule(rule)} className="ai-training-row-action" title="删除规则" aria-label="删除规则">
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  );})}
                  {!loadingRules && trainingRules.length === 0 && (
                    <div className="ai-training-empty-state">暂无规则</div>
                  )}
                </div>
              </section>
            </aside>

            <main className={`ai-training-workspace ${mobilePanel === 'chat' ? 'is-mobile-active' : ''}`}>
              <div className="ai-training-presets" aria-label="快捷测试问题">
                {PRESET_MESSAGES.map((preset) => (
                  <button key={preset} type="button" onClick={() => setBuyerMessage(preset)}>
                    {preset}
                  </button>
                ))}
              </div>

              <div className="ai-training-chat" aria-live="polite">
                {messages.length === 0 && (
                  <div className="ai-training-chat-empty">
                    <MessageSquare className="w-5 h-5" />
                    <span>以买家身份发送咨询</span>
                  </div>
                )}
                {messages.map((message, index) => (
                  <div key={`${message.role}-${index}`} className={`ai-training-message ${message.role === 'user' ? 'is-buyer' : 'is-ai'}`}>
                    <div className="ai-training-message-wrap">
                      <span className="ai-training-message-label">{message.role === 'user' ? '买家' : 'AI 客服'}</span>
                      <div className="ai-training-message-bubble">{message.content}</div>
                    </div>
                  </div>
                ))}
                {sending && (
                  <div className="ai-training-message is-ai">
                    <div className="ai-training-message-wrap">
                      <span className="ai-training-message-label">AI 客服</span>
                      <div className="ai-training-message-bubble flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" />生成中</div>
                    </div>
                  </div>
                )}
              </div>

              {warnings.length > 0 && (
                <div className="ai-training-alert is-warning">
                  <AlertTriangle className="w-4 h-4 mt-0.5" />
                  <div><strong>命中风险词：</strong>{warnings.join('、')}</div>
                </div>
              )}
              {ruleAudit && (
                <section className={`ai-training-audit ${auditSummary.violated > 0 || auditSummary.conflicts > 0 ? 'has-issues' : ''} ${auditExpanded ? 'is-expanded' : ''}`}>
                  <div className="ai-training-audit-header">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2 text-xs font-bold">
                        <span className="text-gray-900">规则审计</span>
                        <span className="ai-training-audit-count is-positive">遵守 {auditSummary.followed}</span>
                        <span className={`ai-training-audit-count ${auditSummary.violated > 0 ? 'is-negative' : ''}`}>违反 {auditSummary.violated}</span>
                        <span className="ai-training-audit-count">无关 {auditSummary.notRelevant}</span>
                        {(auditSummary.unknown > 0 || auditSummary.conflicts > 0) && <span className="ai-training-audit-count is-warning">待核对 {auditSummary.unknown + auditSummary.conflicts}</span>}
                        {regenerated && <span className="ai-training-audit-count is-warning">已自动重答</span>}
                        {guardedByRule && <span className="ai-training-audit-count is-negative">已规则兜底</span>}
                      </div>
                      <div className="mt-1 text-[11px] text-gray-500">
                      {knowledgeSource === 'draft' ? '本次读取：未发布草稿' : knowledgeSource === 'published' ? `本次读取：已发布 v${knowledgeVersion}` : '本次未读取知识档案'}
                      {guardedByRule && <span className="ml-2 text-red-600 font-bold">{guardReason === 'price_rule_conflict' ? '价格规则冲突，已阻止模型猜价' : '价格规则硬优先，已阻止违规报价'}</span>}
                      </div>
                    </div>
                    <button type="button" onClick={() => setAuditExpanded((value) => !value)} className="ai-training-text-button shrink-0" aria-expanded={auditExpanded}>
                      {auditExpanded ? '收起审计' : '查看完整审计'}
                      {auditExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                  {(auditExpanded || auditSummary.violated > 0 || ruleAudit.conflicts.length > 0) && (
                    <div className="ai-training-audit-body">
                      {(auditExpanded ? ruleAudit.results : ruleAudit.results.filter((entry) => entry.status === 'violated')).map(renderAuditEntry)}
                      {ruleAudit.conflicts.length > 0 && (
                        <div className="ai-training-audit-conflicts">
                          <div className="font-bold">发现事实或规则冲突</div>
                          {ruleAudit.conflicts.map((conflict) => <div key={conflict}>{conflict}</div>)}
                        </div>
                      )}
                    </div>
                  )}
                </section>
              )}
              {error && <div className="ai-training-alert is-error">{error}</div>}
              {savedMessage && <div className="ai-training-alert is-success"><CheckCircle2 className="w-4 h-4" />{savedMessage}</div>}

              <div className="ai-training-composer">
                <textarea value={buyerMessage} onChange={(event) => setBuyerMessage(event.target.value)} className="ios-input" placeholder="输入买家问题..." aria-label="买家问题" />
                <button type="button" onClick={() => void sendMessage()} disabled={sending || !buyerMessage.trim() || !selectedItem} className="ios-btn-primary" title={!buyerMessage.trim() ? '请先输入买家问题' : '发送测试'}>
                  {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}发送测试
                </button>
              </div>

              <section className="ai-training-correction">
                <div className="ai-training-correction-header">
                  <div>
                    <div className="text-sm font-bold text-gray-900">修正这次回复</div>
                    <div className="text-[11px] text-gray-500 mt-0.5">先加入待保存规则，再复测确认效果</div>
                  </div>
                  <div className="ai-training-segmented" role="group" aria-label="规则范围">
                    <button type="button" onClick={() => setRuleScope('item')} aria-pressed={ruleScope === 'item'} className={ruleScope === 'item' ? 'is-active' : ''}><Package className="w-3.5 h-3.5" />当前商品</button>
                    <button type="button" onClick={() => setRuleScope('global')} aria-pressed={ruleScope === 'global'} className={ruleScope === 'global' ? 'is-active' : ''}><Globe2 className="w-3.5 h-3.5" />全店通用</button>
                  </div>
                </div>
                <div className="ai-training-correction-row">
                  <textarea value={ruleDraft} onChange={(event) => setRuleDraft(event.target.value)} className="ios-input" placeholder="指出回复哪里有问题..." aria-label="回复修正内容" />
                  <button type="button" onClick={addRule} disabled={!ruleDraft.trim()} className="ai-training-dark-button" title={!ruleDraft.trim() ? '请先填写修正内容' : '加入待保存规则'}>加入待保存</button>
                  <button type="button" onClick={retestLastBuyerMessage} disabled={sending || !messages.some((message) => message.role === 'user')} className="ai-training-secondary-button"><RefreshCw className="w-4 h-4" />再测</button>
                </div>
                <div className="ai-training-knowledge-row">
                  <select value={knowledgeSection} onChange={(event) => setKnowledgeSection(event.target.value as typeof knowledgeSection)} className="ios-input" aria-label="商品知识分类">
                    <option value="pricing">规格与价格</option>
                    <option value="process">操作流程</option>
                    <option value="after_sales">售后边界</option>
                    <option value="forbidden">禁止说法</option>
                    <option value="notes">其他补充</option>
                  </select>
                  <button type="button" onClick={() => void addCorrectionToKnowledge()} disabled={saving || !ruleDraft.trim() || !selectedItem} className="ai-training-knowledge-button">
                    <Package className="w-4 h-4" />补充到商品知识草稿
                  </button>
                </div>
              </section>
            </main>
          </div>
        </div>

        <div className="modal-footer ai-training-footer">
          <div className="ai-training-save-status" role="status">
            {saving ? <><Loader2 className="w-4 h-4 animate-spin" />正在保存修正</> : error ? <><AlertTriangle className="w-4 h-4 text-red-500" />操作失败，请查看上方提示</> : pendingRules.length > 0 ? <><span className="ai-training-status-dot is-pending" />有 {pendingRules.length} 条待保存</> : savedMessage ? <><CheckCircle2 className="w-4 h-4 text-green-600" />已保存</> : <><span className="ai-training-status-dot" />暂无修改</>}
          </div>
          <div className="ai-training-footer-actions">
            <button type="button" onClick={onClose} className="ai-training-secondary-button">关闭</button>
            <button type="button" onClick={() => void saveRules()} disabled={saving || pendingRules.length === 0} className="ios-btn-primary" title={pendingRules.length === 0 ? '当前没有待保存的修正规则' : '保存到真实 AI'}>
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              保存到真实 AI{pendingRules.length > 0 ? ` (${pendingRules.length})` : ''}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default AITrainingLab;
