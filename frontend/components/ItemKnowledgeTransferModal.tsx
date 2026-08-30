import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { AccountDetail, Item } from '../types';
import {
  AIItemKnowledgeProfile,
  copyAIItemKnowledge,
  getAccountDetails,
  getItems,
  importAIItemKnowledge,
} from '../services/api';
import { Download, Loader2, Search, Upload, X } from 'lucide-react';
import RemoteImage from './ui/RemoteImage';
import { knowledgeStateOf } from '../utils/itemKnowledge';
import { pushToast } from './ui/Toast';
import { confirmDialog } from './ui/ConfirmDialog';

export type TransferTab = 'import' | 'distribute';

type DistributeFilter = 'all' | 'none' | 'has';

const ALL_ACCOUNTS = '__all__';

interface ItemKnowledgeTransferModalProps {
  item: Item;
  /** 当前商品是否有可以分发出去的档案内容 */
  canDistribute: boolean;
  initialTab?: TransferTab;
  /** 当前草稿有未保存修改；分发前会先调用 onBeforeDistribute */
  dirty?: boolean;
  onBeforeDistribute?: () => Promise<void>;
  onImported: (profile: AIItemKnowledgeProfile) => void;
  onDistributed?: () => void;
  onClose: () => void;
}

const accountLabelOf = (accounts: AccountDetail[], cookieId: string) => {
  const account = accounts.find((candidate) => candidate.id === cookieId);
  return account?.nickname || account?.remark || cookieId;
};

const KnowledgeBadge: React.FC<{ item: Item }> = ({ item }) => {
  const state = knowledgeStateOf(item);
  if (state === 'published') {
    return <span className="shrink-0 rounded bg-green-100 px-1.5 py-0.5 text-[11px] font-bold text-green-700">已发布 v{item.knowledge_published_version}</span>;
  }
  if (state === 'draft') {
    return <span className="shrink-0 rounded bg-yellow-100 px-1.5 py-0.5 text-[11px] font-bold text-yellow-800">已有草稿</span>;
  }
  return <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[11px] font-bold text-gray-400">无档案</span>;
};

const ItemKnowledgeTransferModal: React.FC<ItemKnowledgeTransferModalProps> = ({
  item, canDistribute, initialTab = 'import', dirty = false,
  onBeforeDistribute, onImported, onDistributed, onClose,
}) => {
  const [tab, setTab] = useState<TransferTab>(initialTab);
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [candidates, setCandidates] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [search, setSearch] = useState('');
  const [accountFilter, setAccountFilter] = useState(ALL_ACCOUNTS);
  const [distributeFilter, setDistributeFilter] = useState<DistributeFilter>('all');
  const [importSource, setImportSource] = useState<Item | null>(null);
  const [targetKeys, setTargetKeys] = useState<string[]>([]);

  const keyOf = (candidate: Item) => `${candidate.cookie_id}::${candidate.item_id}`;

  const loadCandidates = async () => {
    try {
      const [accountList, itemList] = await Promise.all([getAccountDetails(), getItems()]);
      setAccounts(accountList);
      setCandidates(itemList.filter((candidate) => !(
        candidate.cookie_id === item.cookie_id && candidate.item_id === item.item_id
      )));
    } catch (error) {
      pushToast('error', error instanceof Error ? error.message : '商品列表加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadCandidates(); }, [item.cookie_id, item.item_id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const visibleCandidates = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return candidates.filter((candidate) => {
      if (accountFilter !== ALL_ACCOUNTS && candidate.cookie_id !== accountFilter) return false;
      if (keyword && !(
        (candidate.item_title || '').toLowerCase().includes(keyword) ||
        candidate.item_id.toLowerCase().includes(keyword)
      )) return false;
      const hasKnowledge = knowledgeStateOf(candidate) !== 'none';
      // 导入只能取有档案的商品，没有档案的来源没有意义
      if (tab === 'import') return hasKnowledge;
      if (distributeFilter === 'none') return !hasKnowledge;
      if (distributeFilter === 'has') return hasKnowledge;
      return true;
    });
  }, [candidates, search, accountFilter, tab, distributeFilter]);

  const groupedCandidates = useMemo(() => {
    const groups = new Map<string, Item[]>();
    for (const candidate of visibleCandidates) {
      const group = groups.get(candidate.cookie_id) || [];
      group.push(candidate);
      groups.set(candidate.cookie_id, group);
    }
    return [...groups.entries()];
  }, [visibleCandidates]);

  const selectedTargets = useMemo(
    () => candidates.filter((candidate) => targetKeys.includes(keyOf(candidate))),
    [candidates, targetKeys]
  );
  const targetsWithKnowledge = selectedTargets.filter(
    (candidate) => knowledgeStateOf(candidate) !== 'none'
  ).length;

  const toggleTarget = (candidate: Item) => {
    const key = keyOf(candidate);
    setTargetKeys((current) => current.includes(key)
      ? current.filter((value) => value !== key)
      : [...current, key]);
  };

  const runImport = async () => {
    if (!importSource) return;
    // 当前商品已有档案内容时导入是破坏性覆盖，按约定先二次确认
    if (canDistribute) {
      const confirmed = await confirmDialog({
        title: '覆盖当前草稿',
        message: `将用「${importSource.item_title || importSource.item_id}」的档案整份替换当前商品草稿；已发布版本与历史版本不受影响。`,
        confirmText: '导入并覆盖',
        tone: 'danger',
      });
      if (!confirmed) return;
    }
    setWorking(true);
    try {
      const result = await importAIItemKnowledge(item.cookie_id, item.item_id, {
        cookie_id: importSource.cookie_id,
        item_id: importSource.item_id,
      });
      const sourceLabel = result.source_kind === 'published' ? '已发布版本' : '草稿';
      onImported(result);
      pushToast('success', `${result.message || '已导入为当前商品草稿'}（来源：${sourceLabel}）`);
      onClose();
    } catch (error) {
      pushToast('error', error instanceof Error ? error.message : '导入知识档案失败');
    } finally {
      setWorking(false);
    }
  };

  const runDistribute = async () => {
    if (selectedTargets.length === 0) return;
    if (targetsWithKnowledge > 0) {
      const confirmed = await confirmDialog({
        title: '覆盖已有档案',
        message: `所选目标中有 ${targetsWithKnowledge} 个已有档案，其草稿将被当前档案覆盖；各自的已发布版本不受影响。`,
        confirmText: '继续覆盖',
        tone: 'danger',
      });
      if (!confirmed) return;
    }
    setWorking(true);
    try {
      if (dirty && onBeforeDistribute) await onBeforeDistribute();
      const result = await copyAIItemKnowledge(item.cookie_id, item.item_id, selectedTargets.map(
        (candidate) => ({ cookie_id: candidate.cookie_id, item_id: candidate.item_id })
      ));
      const copiedCount = result.copied_count ?? result.copied_item_ids.length;
      const missingCount = result.missing_count ?? result.missing_item_ids.length;
      const sourceLabel = result.source_kind === 'published' ? '已发布版本' : '草稿';
      const details = [
        `来源：${sourceLabel}`,
        `已覆盖 ${copiedCount}`,
        missingCount > 0 ? `不存在 ${missingCount}` : '',
      ].filter(Boolean).join('，');
      pushToast('success', `${result.message}（${details}）`);
      setTargetKeys([]);
      onDistributed?.();
      await loadCandidates();
    } catch (error) {
      pushToast('error', error instanceof Error ? error.message : '复制知识档案失败');
    } finally {
      setWorking(false);
    }
  };

  const currentState = knowledgeStateOf(item);
  const currentStateLabel = currentState === 'published'
    ? `已发布 v${item.knowledge_published_version}`
    : currentState === 'draft' ? '仅草稿' : '无档案';

  const renderCandidate = (candidate: Item) => {
    const selected = tab === 'import'
      ? importSource?.cookie_id === candidate.cookie_id && importSource?.item_id === candidate.item_id
      : targetKeys.includes(keyOf(candidate));
    const label = `${candidate.item_title || candidate.item_id} ${candidate.item_price || ''} ${accountLabelOf(accounts, candidate.cookie_id)}`;
    return (
      <label
        key={keyOf(candidate)}
        className={`flex cursor-pointer items-center gap-3 rounded-2xl border px-3 py-2.5 transition-colors ${
          selected ? 'border-gray-900 bg-white shadow-sm' : 'border-gray-200 bg-white hover:border-gray-300'
        }`}
      >
        <RemoteImage
          src={candidate.item_image}
          alt=""
          className="h-10 w-10 shrink-0 rounded-xl object-cover"
        />
        <span className="min-w-0 flex-1">
          <b className="block truncate text-sm text-gray-900">{candidate.item_title || candidate.item_id}</b>
          <span className="mt-0.5 flex items-center gap-2 text-[11px] text-gray-400">
            <span>¥{candidate.item_price || '-'}</span>
            <span>…{candidate.item_id.slice(-6)}</span>
            <KnowledgeBadge item={candidate} />
          </span>
        </span>
        <input
          type={tab === 'import' ? 'radio' : 'checkbox'}
          name={tab === 'import' ? 'transfer-source' : undefined}
          aria-label={label}
          checked={selected}
          onChange={() => (tab === 'import' ? setImportSource(candidate) : toggleTarget(candidate))}
          className="h-4 w-4 shrink-0 accent-gray-900"
        />
      </label>
    );
  };

  const distributeDisabled = !canDistribute;

  return createPortal(
    <div className="modal-overlay-centered">
      <div className="modal-container" style={{ maxWidth: '880px', width: '94vw', maxHeight: '90vh' }}>
        <div className="modal-header flex w-full items-start justify-between">
          <div>
            <h3 className="text-xl font-extrabold text-gray-900">档案搬运</h3>
            <p className="mt-1 text-sm text-gray-500">
              当前商品 · {item.item_title || item.item_id} · {accountLabelOf(accounts, item.cookie_id)} · {currentStateLabel}
            </p>
          </div>
          <button onClick={onClose} className="rounded-lg p-2 hover:bg-gray-100" title="关闭"><X className="h-5 w-5" /></button>
        </div>

        <div className="modal-body space-y-4">
          <div className="grid grid-cols-2 gap-1 rounded-2xl bg-gray-100 p-1" role="tablist">
            {([
              ['import', '从其他商品导入', <Download key="i" className="h-4 w-4" />],
              ['distribute', '复制到其他商品', <Upload key="d" className="h-4 w-4" />],
            ] as Array<[TransferTab, string, React.ReactNode]>).map(([value, label, icon]) => (
              <button
                key={value}
                role="tab"
                aria-selected={tab === value}
                onClick={() => { setTab(value); setSearch(''); }}
                className={`flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold transition-colors ${
                  tab === value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {icon}{label}
              </button>
            ))}
          </div>

          <div className="rounded-2xl bg-gray-50 px-4 py-3 text-xs leading-5 text-gray-600">
            {tab === 'import'
              ? '把别人的档案搬进来：选一个来源商品，它的档案会成为当前商品的草稿，当前草稿被整份替换。已发布版本和历史版本不受影响，导入后仍需你确认并手动发布。'
              : '把当前档案发出去：选中的目标商品草稿会被本档案覆盖，不会自动发布，目标的已发布版本与历史版本保持不变。'}
            {tab === 'distribute' && dirty && (
              <span className="mt-1 block font-bold text-orange-700">当前草稿有未保存修改，执行复制时会先保存当前草稿。</span>
            )}
          </div>

          {distributeDisabled && tab === 'distribute' ? (
            <div className="rounded-2xl border border-dashed border-gray-200 px-4 py-10 text-center text-sm text-gray-500">
              当前商品还没有可分发的档案，请先在档案页填写并保存草稿，或从其他商品导入一份。
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    aria-label="搜索商品标题或ID"
                    placeholder="搜索商品标题或ID"
                    className="ios-input w-full rounded-xl py-2.5 pl-9 pr-3 text-sm"
                  />
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {[[ALL_ACCOUNTS, '全部账号'] as [string, string]].concat(
                    accounts.map((account) => [account.id, account.nickname || account.remark || account.id])
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      onClick={() => setAccountFilter(value)}
                      className={`rounded-full px-3 py-1.5 text-xs font-bold transition-colors ${
                        accountFilter === value ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {tab === 'distribute' && (
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex gap-1.5">
                    {([
                      ['all', '全部商品'],
                      ['none', '只看无档案'],
                      ['has', '只看有档案'],
                    ] as Array<[DistributeFilter, string]>).map(([value, label]) => (
                      <button
                        key={value}
                        onClick={() => setDistributeFilter(value)}
                        className={`rounded-full px-3 py-1.5 text-xs font-bold transition-colors ${
                          distributeFilter === value ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="ml-auto flex gap-3 text-xs font-bold">
                    <button
                      onClick={() => setTargetKeys(visibleCandidates.map(keyOf))}
                      disabled={visibleCandidates.length === 0}
                      className="text-blue-700 disabled:text-gray-300"
                    >
                      全选当前列表
                    </button>
                    <button
                      onClick={() => setTargetKeys(visibleCandidates
                        .filter((candidate) => knowledgeStateOf(candidate) === 'none')
                        .map(keyOf))}
                      disabled={visibleCandidates.length === 0}
                      className="text-green-700 disabled:text-gray-300"
                    >
                      只选无档案
                    </button>
                    <button
                      onClick={() => setTargetKeys([])}
                      disabled={targetKeys.length === 0}
                      className="text-gray-500 disabled:text-gray-300"
                    >
                      清空
                    </button>
                  </div>
                </div>
              )}

              <div className="max-h-[42vh] space-y-4 overflow-y-auto pr-1">
                {loading && <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-[#FFE815]" /></div>}
                {!loading && groupedCandidates.map(([cookieId, group]) => (
                  <div key={cookieId} role="group" aria-label={accountLabelOf(accounts, cookieId)} className="space-y-2">
                    <div className="flex items-center gap-2 text-xs font-bold text-gray-500">
                      <span className="h-1.5 w-1.5 rounded-full bg-gray-300" />
                      {accountLabelOf(accounts, cookieId)}
                      <span className="text-gray-400">({group.length})</span>
                      {cookieId === item.cookie_id && <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">当前账号</span>}
                    </div>
                    <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                      {group.map(renderCandidate)}
                    </div>
                  </div>
                ))}
                {!loading && groupedCandidates.length === 0 && (
                  <div className="py-10 text-center text-sm text-gray-400">
                    {tab === 'import'
                      ? '该账号下没有可作为来源的商品，换个账号或清空搜索再看看。'
                      : '没有匹配的目标商品，换个筛选条件再看看。'}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        <div className="modal-footer flex w-full items-center justify-between gap-4">
          {tab === 'import' ? (
            <>
              <div className="min-w-0 text-xs text-gray-500">
                {importSource
                  ? <>将取用「{importSource.item_title || importSource.item_id}」的{knowledgeStateOf(importSource) === 'published' ? '已发布版本' : '草稿'}，覆盖当前商品草稿</>
                  : '先在上面选一个来源商品'}
              </div>
              <button
                onClick={() => void runImport()}
                disabled={!importSource || working}
                className="ios-btn-primary shrink-0 rounded-2xl px-5 py-3 text-sm font-bold disabled:opacity-40"
              >
                {working ? '导入中…' : '导入为当前商品草稿'}
              </button>
            </>
          ) : (
            <>
              <div className="min-w-0 text-xs text-gray-500">
                已选 <b className="text-gray-900">{selectedTargets.length}</b> 个目标
                {targetsWithKnowledge > 0 && (
                  <span className="ml-1 font-bold text-orange-700">其中 {targetsWithKnowledge} 个已有档案，草稿会被覆盖</span>
                )}
              </div>
              <button
                onClick={() => void runDistribute()}
                disabled={distributeDisabled || selectedTargets.length === 0 || working}
                className="ios-btn-primary shrink-0 rounded-2xl px-5 py-3 text-sm font-bold disabled:opacity-40"
              >
                {working
                  ? '覆盖中…'
                  : `${dirty ? '保存草稿并' : ''}覆盖所选 ${selectedTargets.length} 个商品草稿`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
};

export default ItemKnowledgeTransferModal;
