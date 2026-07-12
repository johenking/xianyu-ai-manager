import React, { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Eye, EyeOff, Loader2, Pencil, Plus, RefreshCw, TestTube2, Trash2, X } from 'lucide-react';
import {
  createAIProvider,
  deleteAIProvider,
  getAIProviders,
  refreshAIProviderModels,
  testAIProvider,
  updateAIProvider,
} from '../services/api';
import { AIProviderPreset, AIProviderProfile } from '../types';
import { InlineNotice, StatusBadge, ToggleControl } from './ui/StatusControls';
import { IconAction } from './ui/ProtectedPage';

type ProviderForm = {
  id?: number;
  name: string;
  preset: string;
  provider_type: 'openai_compatible' | 'gemini';
  base_url: string;
  api_key: string;
  api_key_action: 'keep' | 'set' | 'clear';
  default_model: string;
  is_default: boolean;
};

const emptyForm = (preset?: AIProviderPreset): ProviderForm => ({
  name: preset?.label || '',
  preset: preset ? 'deepseek' : 'custom',
  provider_type: preset?.provider_type || 'openai_compatible',
  base_url: preset?.base_url || '',
  api_key: '',
  api_key_action: 'keep',
  default_model: preset?.default_model || '',
  is_default: false,
});

const AIProviderManager: React.FC = () => {
  const [providers, setProviders] = useState<AIProviderProfile[]>([]);
  const [presets, setPresets] = useState<Record<string, AIProviderPreset>>({});
  const [form, setForm] = useState<ProviderForm | null>(null);
  const [busy, setBusy] = useState<string>('');
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [showKey, setShowKey] = useState(false);

  const load = async () => {
    try {
      const result = await getAIProviders();
      setProviders(result.providers);
      setPresets(result.presets);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '平台配置读取失败' });
    }
  };

  useEffect(() => { void load(); }, []);

  const presetEntries = useMemo(() => Object.entries(presets), [presets]);

  const choosePreset = (presetKey: string) => {
    const preset = presets[presetKey];
    if (!preset) return;
    setForm((current) => current ? ({
      ...current,
      preset: presetKey,
      provider_type: preset.provider_type,
      base_url: preset.base_url,
      default_model: preset.default_model,
      name: current.id ? current.name : preset.label,
    }) : current);
  };

  const edit = (provider: AIProviderProfile) => setForm({
    id: provider.id,
    name: provider.name,
    preset: provider.preset,
    provider_type: provider.provider_type,
    base_url: provider.base_url,
    api_key: '',
    api_key_action: 'keep',
    default_model: provider.default_model,
    is_default: provider.is_default,
  });

  const save = async () => {
    if (!form) return;
    setBusy('save');
    setNotice(null);
    try {
      if (form.id) {
        await updateAIProvider(form.id, {
          name: form.name,
          preset: form.preset,
          provider_type: form.provider_type,
          base_url: form.base_url,
          api_key: form.api_key,
          api_key_action: form.api_key ? 'set' : form.api_key_action,
          default_model: form.default_model,
          is_default: form.is_default,
        });
      } else {
        await createAIProvider({
          name: form.name,
          preset: form.preset,
          provider_type: form.provider_type,
          base_url: form.base_url,
          api_key: form.api_key,
          default_model: form.default_model,
          is_default: form.is_default,
        });
      }
      setForm(null);
      setShowKey(false);
      await load();
      setNotice({ tone: 'success', text: '平台配置已保存。生成测试回复成功后，账号才能切换到它。' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '平台配置保存失败' });
    } finally {
      setBusy('');
    }
  };

  const refresh = async (provider: AIProviderProfile) => {
    setBusy(`refresh-${provider.id}`);
    setNotice(null);
    try {
      const result = await refreshAIProviderModels(provider.id);
      await load();
      setNotice({ tone: 'success', text: `已读取 ${result.models.length} 个模型；列表不可用时仍可手填模型 ID。` });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '模型列表刷新失败' });
    } finally {
      setBusy('');
    }
  };

  const test = async (provider: AIProviderProfile) => {
    if (!provider.default_model) {
      setNotice({ tone: 'error', text: '请先编辑并填写默认模型' });
      return;
    }
    setBusy(`test-${provider.id}`);
    setNotice(null);
    try {
      const result = await testAIProvider(provider.id, provider.default_model);
      await load();
      setNotice({ tone: 'success', text: `${provider.name} / ${provider.default_model}：${result.reply}` });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '平台测试失败' });
    } finally {
      setBusy('');
    }
  };

  const remove = async (provider: AIProviderProfile) => {
    if (!window.confirm(`删除平台“${provider.name}”？`)) return;
    setBusy(`delete-${provider.id}`);
    setNotice(null);
    try {
      await deleteAIProvider(provider.id);
      await load();
      setNotice({ tone: 'success', text: '平台配置已删除' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '删除失败' });
    } finally {
      setBusy('');
    }
  };

  return <div className="space-y-4">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div><h4 className="font-extrabold text-gray-900">AI 平台配置库</h4><p className="mt-1 text-xs text-gray-500">平台 Key 在这里集中管理；每个闲鱼账号可以单独选择模型。</p></div>
      <button type="button" onClick={() => setForm(emptyForm(presets.deepseek))} className="ios-btn-primary inline-flex h-10 items-center justify-center gap-2 rounded-xl px-4 text-sm font-bold"><Plus className="h-4 w-4" />添加平台</button>
    </div>

    {notice && <InlineNotice tone={notice.tone}>{notice.text}</InlineNotice>}

    <div className="divide-y divide-gray-100 overflow-hidden rounded-xl border border-gray-100 bg-gray-50/70">
      {providers.length === 0 && <div className="px-4 py-6 text-center text-sm text-gray-500">暂无平台配置</div>}
      {providers.map((provider) => <div key={provider.id} className="flex flex-col gap-3 bg-white px-4 py-4 lg:flex-row lg:items-center">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="font-extrabold text-gray-900">{provider.name}</span>{provider.is_default && <span className="rounded-md bg-[#FFE815] px-2 py-0.5 text-[11px] font-bold text-black">默认</span>}<StatusBadge state={provider.verification_status === 'verified' ? 'ready' : provider.verification_status === 'failed' ? 'error' : 'dirty'} label={provider.verification_status === 'verified' ? '已验证' : provider.verification_status === 'failed' ? '验证失败' : '待验证'} /></div>
          <div className="mt-1 truncate text-xs text-gray-500">{provider.default_model || '未填写模型'} · {provider.api_key_masked || '未配置 Key'} · {provider.base_url}</div>
        </div>
        <div className="flex gap-2">
          <IconButton label="刷新模型" busy={busy === `refresh-${provider.id}`} icon={RefreshCw} onClick={() => void refresh(provider)} />
          <IconButton label="测试回复" busy={busy === `test-${provider.id}`} icon={TestTube2} onClick={() => void test(provider)} />
          <IconButton label="编辑平台" icon={Pencil} onClick={() => edit(provider)} />
          <IconButton label="删除平台" busy={busy === `delete-${provider.id}`} icon={Trash2} danger onClick={() => void remove(provider)} />
        </div>
      </div>)}
    </div>

    {form && <div className="space-y-4 rounded-xl border border-yellow-200 bg-yellow-50/60 p-4">
      <div className="flex items-center justify-between"><strong>{form.id ? '编辑平台' : '添加平台'}</strong><button type="button" aria-label="关闭平台编辑" onClick={() => setForm(null)} className="rounded-md p-1.5 hover:bg-white"><X className="h-4 w-4" /></button></div>
      <label className="block text-sm font-bold text-gray-800">平台预设<select value={form.preset} onChange={(e) => choosePreset(e.target.value)} className="ios-input mt-2 h-11 w-full rounded-xl px-3 font-normal">{presetEntries.map(([key, preset]) => <option key={key} value={key}>{preset.label}</option>)}</select></label>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2"><TextField label="配置名称" value={form.name} onChange={(name) => setForm({ ...form, name })} /><TextField label="默认模型" value={form.default_model} onChange={(default_model) => setForm({ ...form, default_model })} placeholder="可刷新后选择或手填" /></div>
      <TextField label="API 地址" value={form.base_url} onChange={(base_url) => setForm({ ...form, base_url })} />
      <label className="block text-sm font-bold text-gray-800">API Key<div className="relative mt-2"><input type={showKey ? 'text' : 'password'} value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value, api_key_action: e.target.value ? 'set' : 'keep' })} placeholder={form.id ? '留空保持原 Key' : '输入平台 API Key'} className="ios-input h-11 w-full rounded-xl px-3 pr-11 font-mono font-normal" /><button type="button" aria-label={showKey ? '隐藏平台密钥' : '显示平台密钥'} onClick={() => setShowKey(!showKey)} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-2 text-gray-500 hover:bg-gray-100">{showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div></label>
      <div className="flex items-center justify-between rounded-xl bg-white px-3 py-2"><span className="text-sm font-bold text-gray-800">设为默认平台</span><ToggleControl checked={form.is_default} onChange={(is_default) => setForm({ ...form, is_default })} label="默认平台" /></div>
      <div className="flex justify-end"><button type="button" disabled={busy === 'save'} onClick={() => void save()} className="ios-btn-primary inline-flex h-10 items-center gap-2 rounded-xl px-5 text-sm font-extrabold">{busy === 'save' ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}保存平台</button></div>
    </div>}
  </div>;
};

const TextField: React.FC<{ label: string; value: string; onChange: (value: string) => void; placeholder?: string }> = ({ label, value, onChange, placeholder }) => <label className="block text-sm font-bold text-gray-800">{label}<input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="ios-input mt-2 h-11 w-full rounded-xl px-3 font-normal" /></label>;

const IconButton: React.FC<{ label: string; icon: React.ComponentType<{ className?: string }>; onClick: () => void; busy?: boolean; danger?: boolean }> = ({ label, icon, onClick, busy, danger }) => <IconAction icon={busy ? Loader2 : icon} label={label} onClick={onClick} busy={busy} danger={danger} disabled={busy} />;

export default AIProviderManager;
