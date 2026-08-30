# 商品界面优化：刷新入口合并 + 档案双向搬运 实现计划

> **面向 AI 代理的工作者：** 用 `.cursor/skills/executing-plans/SKILL.md` 逐任务实现本计划，每个任务用 `.cursor/skills/test-driven-development/SKILL.md` 的红-绿-提交节奏。
> **本机硬约束（项目记忆 2026-08-25 22:05 过载事故）：** 生产与开发共用这台 Mac 且公网走本机 cloudflared 隧道，**不得在本机跑全量 vitest**。前端门禁 = 定向用例 + `tsc --noEmit` + `vite build`；后端可跑全量 `pytest -q tests --ignore=outputs`。开跑重活前先 `uptime` 看负载。

**目标：** 商品管理页只保留一个刷新入口；商品知识档案支持「从其他商品（含其他账号）导入档案」，并把现有「复制到其他商品」搬进同一个宽弹窗里做顺手。

**架构：** 后端把「档案复制」从单账号闭环扩展为 `(cookie_id, item_id)` 二元组寻址，新增 `import` 端点做反向拉取；两个方向都对**源和目标各自的 cookie 做一次归属校验**，不放宽任何越权边界。前端新增一个独立的 `ItemKnowledgeTransferModal`，用 tab 承载「导入 / 分发」两个方向，`ItemKnowledgeModal` 左栏的旧内联复制面板整块移除。

**技术栈：** FastAPI + SQLite（`db_manager.py` / `reply_server.py`）、React 19 + TypeScript + Vite、vitest + @testing-library/react、pytest。

---

## 选型（2026-08-30 10:38 用户拍板：C / B / B）

| 问题 | 采用方案 | Demo 面板 |
|---|---|---|
| 1 · 双刷新圈 | **1C 分裂按钮 + 下拉菜单**（主按钮同步，菜单收纳「仅刷新本地列表」「同步全部账号」） | `p1c` |
| 2 · 导入档案 | **2B 档案搬运中心**（左栏一个入口 → 880px 独立弹窗，双向 tab） | `p2b` |
| 3 · 复制体验 | **3B**（与 2B 同一弹窗的另一个 tab） | `p3b` |

Demo 对照：`docs/research/item-ui-demos/index.html`（浏览器直接打开，左栏切换 9 个方案）。

### 仍待用户确认的实现细节（不确认不动手的部分已在任务里标注）
1. 「同步全部账号」逐个失败怎么处理、过期账号是否跳过。
2. 导入是整份覆盖还是支持分区级挑选。
3. 源商品同时有草稿和已发布版本时取哪一份。

未确认前，任务 6 的串行调度与任务 4 的导入粒度按计划内标注的默认值实现，确认后如有出入就地调整。

---

## 文件结构

### 新建

| 文件 | 职责 |
|---|---|
| `frontend/components/ItemKnowledgeTransferModal.tsx` | 档案搬运弹窗：两个方向的候选列表、跨账号分组、筛选、选择与提交。自身不持有档案内容，只回调 |
| `frontend/components/ItemKnowledgeTransferModal.test.tsx` | 上述组件的交互测试 |
| `tests/test_item_knowledge_transfer.py` | 跨账号导入/分发的 DAL 与路由测试（含越权 403、源无档案 400、目标缺失） |

### 修改

| 文件 | 改什么 |
|---|---|
| `db_manager.py` | 新增 `import_ai_item_knowledge_draft`；新增 `copy_ai_item_knowledge_draft_to_targets`，让既有 `copy_ai_item_knowledge_draft` 委托给它（同账号语义与返回字段一字不动） |
| `reply_server.py` | 新增 `POST /ai-item-knowledge/{cookie_id}/{item_id}/import`；`AIItemKnowledgeCopyRequest` 增加可选 `targets`；copy 路由对每个目标 cookie 逐一 `_ensure_ai_cookie_access` |
| `frontend/services/api/ai.ts` | 新增 `importAIItemKnowledge`；`copyAIItemKnowledge` 支持跨账号目标对 |
| `frontend/components/ItemKnowledgeModal.tsx` | 删除内联复制面板与其 8 个 state；接入「档案搬运」按钮与新弹窗 |
| `frontend/components/ItemKnowledgeModal.test.tsx` | 旧内联面板的 5 个用例改写为「点档案搬运 → 弹窗出现」 |
| `frontend/components/ItemList.tsx` | 页头单按钮化：删独立刷新按钮，`loading` / `syncing` 拆分，按钮随所选账号换身份，补「上次同步」 |
| `frontend/components/ItemList.test.tsx` | 第 149 行「全部账号时同步商品 disabled」的断言改为「按钮变成刷新列表且可点」 |

---

## 任务 1 · DAL：把复制改成按 (cookie_id, item_id) 寻址

**文件：** `db_manager.py`、`tests/test_item_knowledge_transfer.py`

### 步骤 1.1 写失败的测试

新建 `tests/test_item_knowledge_transfer.py`：

```python
import json
import os
import tempfile
import unittest

from db_manager import DBManager


class ItemKnowledgeTransferDALTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = DBManager(os.path.join(self.tmp, 'test.db'))
        for cookie_id, item_id, title in [
            ('acc-1', 'item-a', '源商品'),
            ('acc-1', 'item-b', '同账号目标'),
            ('acc-2', 'item-c', '跨账号目标'),
        ]:
            self.db.save_item_info(cookie_id, item_id, {'item_title': title, 'item_price': '9.9'})
        self.profile = {
            'overview': {'text': '源档案概览', 'source': 'user', 'status': 'confirmed'},
            'pricing': [{'label': 'Pro', 'amount': '145', 'source': 'ai', 'status': 'confirmed'}],
            'process': [], 'after_sales': [], 'forbidden': [], 'faqs': [], 'notes': [],
        }
        self.db.save_ai_item_knowledge_draft('acc-1', 'item-a', self.profile, 'hash-a')

    def test_copy_to_targets_crosses_accounts(self):
        result = self.db.copy_ai_item_knowledge_draft_to_targets(
            'acc-1', 'item-a', [('acc-1', 'item-b'), ('acc-2', 'item-c')]
        )
        self.assertEqual(result['copied_count'], 2)
        self.assertEqual(result['source_kind'], 'draft')
        for cookie_id, item_id in [('acc-1', 'item-b'), ('acc-2', 'item-c')]:
            got = self.db.get_ai_item_knowledge_profile(cookie_id, item_id)
            self.assertEqual(got['draft']['overview']['text'], '源档案概览')
            self.assertEqual(got['source_detail_hash'], '')
            self.assertEqual(got['published_version'], 0)

    def test_copy_to_targets_reports_missing_pair(self):
        result = self.db.copy_ai_item_knowledge_draft_to_targets(
            'acc-1', 'item-a', [('acc-2', 'item-not-there')]
        )
        self.assertEqual(result['copied_count'], 0)
        self.assertEqual(result['missing_targets'], [{'cookie_id': 'acc-2', 'item_id': 'item-not-there'}])

    def test_legacy_copy_still_scopes_to_source_account(self):
        result = self.db.copy_ai_item_knowledge_draft('acc-1', 'item-a', ['item-b', 'item-c'])
        self.assertEqual(result['copied_item_ids'], ['item-b'])
        self.assertEqual(result['missing_item_ids'], ['item-c'])

    def test_import_pulls_source_into_target_draft(self):
        result = self.db.import_ai_item_knowledge_draft('acc-2', 'item-c', 'acc-1', 'item-a')
        self.assertEqual(result['source_kind'], 'draft')
        got = self.db.get_ai_item_knowledge_profile('acc-2', 'item-c')
        self.assertEqual(got['draft']['pricing'][0]['label'], 'Pro')

    def test_import_prefers_published_when_no_draft(self):
        self.db.publish_ai_item_knowledge('acc-1', 'item-a')
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE ai_item_knowledge_profiles SET draft_json='{}' WHERE cookie_id='acc-1' AND item_id='item-a'"
            )
            self.db.conn.commit()
        result = self.db.import_ai_item_knowledge_draft('acc-2', 'item-c', 'acc-1', 'item-a')
        self.assertEqual(result['source_kind'], 'published')

    def test_import_rejects_empty_source(self):
        with self.assertRaises(ValueError):
            self.db.import_ai_item_knowledge_draft('acc-1', 'item-b', 'acc-2', 'item-c')
```

> 先确认 `save_item_info` 的真实签名（`rg "def save_item_info" db_manager.py`），签名不同就照真实签名改这段夹具，**不要**改被测函数去迁就测试。

### 步骤 1.2 运行，确认红

```bash
cd /Users/mac/Documents/咸鱼监控台
.venv/bin/python -m pytest -q tests/test_item_knowledge_transfer.py
```
预期：`AttributeError: 'DBManager' object has no attribute 'copy_ai_item_knowledge_draft_to_targets'`。

### 步骤 1.3 实现

在 `db_manager.py` 的 `copy_ai_item_knowledge_draft`（当前 L5499）**上方**插入两个方法，并把原方法改成委托：

```python
    def _read_ai_item_knowledge_source(self, cursor, cookie_id: str, item_id: str):
        """读取源商品可复制的档案：草稿优先，其次已发布版本。"""
        cursor.execute('''
        SELECT draft_json, published_json
        FROM ai_item_knowledge_profiles
        WHERE cookie_id = ? AND item_id = ?
        ''', (cookie_id, item_id))
        row = cursor.fetchone()
        if not row:
            raise ValueError('源商品还没有知识档案')
        source_draft = json.loads(row[0] or '{}')
        source_published = json.loads(row[1] or '{}')
        source_profile = source_draft or source_published
        if not source_profile:
            raise ValueError('源商品知识档案为空')
        return source_profile, ('draft' if source_draft else 'published')

    def copy_ai_item_knowledge_draft_to_targets(self, source_cookie_id: str, source_item_id: str,
                                                targets: List[Tuple[str, str]]) -> Dict[str, Any]:
        """把源商品档案覆盖到任意账号下的目标商品草稿，不自动发布。"""
        normalized: List[Tuple[str, str]] = []
        for pair in targets or []:
            target_cookie = str((pair[0] if pair else '') or '').strip()
            target_item = str((pair[1] if pair and len(pair) > 1 else '') or '').strip()
            if not target_cookie or not target_item:
                continue
            if (target_cookie, target_item) == (source_cookie_id, source_item_id):
                continue
            if (target_cookie, target_item) not in normalized:
                normalized.append((target_cookie, target_item))
        with self.lock:
            cursor = self.conn.cursor()
            source_profile, source_kind = self._read_ai_item_knowledge_source(
                cursor, source_cookie_id, source_item_id
            )
            profile_json = json.dumps(source_profile, ensure_ascii=False)
            copied: List[Dict[str, str]] = []
            missing: List[Dict[str, str]] = []
            try:
                for target_cookie, target_item in normalized:
                    cursor.execute(
                        'SELECT 1 FROM item_info WHERE cookie_id = ? AND item_id = ?',
                        (target_cookie, target_item),
                    )
                    if not cursor.fetchone():
                        missing.append({'cookie_id': target_cookie, 'item_id': target_item})
                        continue
                    cursor.execute('''
                    INSERT INTO ai_item_knowledge_profiles
                    (cookie_id, item_id, draft_json, source_detail_hash, draft_updated_at)
                    VALUES (?, ?, ?, '', CURRENT_TIMESTAMP)
                    ON CONFLICT(cookie_id, item_id) DO UPDATE SET
                        draft_json = excluded.draft_json,
                        source_detail_hash = '',
                        draft_updated_at = CURRENT_TIMESTAMP
                    ''', (target_cookie, target_item, profile_json))
                    copied.append({'cookie_id': target_cookie, 'item_id': target_item})
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            return {
                'copied_targets': copied,
                'missing_targets': missing,
                'copied_item_ids': [t['item_id'] for t in copied],
                'skipped_item_ids': [],
                'missing_item_ids': [t['item_id'] for t in missing],
                'source_kind': source_kind,
                'copied_count': len(copied),
                'skipped_count': 0,
                'missing_count': len(missing),
                'skipped_reasons': {
                    t['item_id']: '目标商品不存在或不属于该账号' for t in missing
                },
            }

    def import_ai_item_knowledge_draft(self, target_cookie_id: str, target_item_id: str,
                                       source_cookie_id: str, source_item_id: str) -> Dict[str, Any]:
        """把源商品（可跨账号）的档案拉进目标商品草稿。"""
        result = self.copy_ai_item_knowledge_draft_to_targets(
            source_cookie_id, source_item_id, [(target_cookie_id, target_item_id)]
        )
        if result['copied_count'] != 1:
            raise ValueError('目标商品不存在或不属于该账号')
        return {'source_kind': result['source_kind']}
```

把原 `copy_ai_item_knowledge_draft` 的函数体整体替换为委托（**保留方法名、参数与返回字段**，老客户端与既有测试不受影响）：

```python
    def copy_ai_item_knowledge_draft(self, cookie_id: str, source_item_id: str,
                                     target_item_ids: List[str], overwrite: bool = True) -> Dict[str, Any]:
        """复制源商品当前档案并覆盖同账号目标草稿，不自动发布。"""
        return self.copy_ai_item_knowledge_draft_to_targets(
            cookie_id, source_item_id, [(cookie_id, str(value or '').strip()) for value in (target_item_ids or [])]
        )
```

文件顶部若未导入 `Tuple`，在 `from typing import ...` 一行补上。

### 步骤 1.4 跑测试确认绿

```bash
.venv/bin/python -m pytest -q tests/test_item_knowledge_transfer.py tests/test_item_knowledge_status.py
```
预期：全部 passed，且既有档案相关测试不变红。

### 步骤 1.5 提交

```bash
git add db_manager.py tests/test_item_knowledge_transfer.py
git commit -m "feat(knowledge): 档案复制改为按 (cookie_id,item_id) 寻址并支持跨账号导入"
```

---

## 任务 2 · 路由：import 端点 + copy 支持跨账号目标

**文件：** `reply_server.py`、`tests/test_item_knowledge_transfer.py`

### 步骤 2.1 追加失败的路由测试

在 `tests/test_item_knowledge_transfer.py` 末尾追加。**先看一个既有路由测试怎么造登录态**（`rg "def .*client" tests/test_item_knowledge_status.py`），照抄那套 fixture，别自己发明：

```python
class ItemKnowledgeTransferRouteTests(unittest.TestCase):
    """路由层只验三件事：源与目标各校验一次归属、跨账号能过、非本人账号 403。"""

    def test_import_requires_access_to_both_cookies(self):
        # 用户 A 拥有 acc-1；acc-9 属于别的用户
        response = self.client.post(
            '/api/ai-item-knowledge/acc-1/item-a/import',
            json={'source_cookie_id': 'acc-9', 'source_item_id': 'item-x'},
        )
        self.assertEqual(response.status_code, 403)

    def test_import_across_own_accounts_succeeds(self):
        response = self.client.post(
            '/api/ai-item-knowledge/acc-2/item-c/import',
            json={'source_cookie_id': 'acc-1', 'source_item_id': 'item-a'},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source_kind'], 'draft')
        self.assertEqual(body['draft']['overview']['text'], '源档案概览')

    def test_copy_rejects_target_cookie_not_owned(self):
        response = self.client.post(
            '/api/ai-item-knowledge/acc-1/item-a/copy',
            json={'targets': [{'cookie_id': 'acc-9', 'item_id': 'item-x'}]},
        )
        self.assertEqual(response.status_code, 403)
```

### 步骤 2.2 运行确认红

```bash
.venv/bin/python -m pytest -q tests/test_item_knowledge_transfer.py -k Route
```
预期：import 返回 404（路由不存在）。

### 步骤 2.3 实现

`reply_server.py` L7042 的请求模型加两个类型：

```python
class AIItemKnowledgeTargetRef(BaseModel):
    cookie_id: str
    item_id: str


class AIItemKnowledgeCopyRequest(BaseModel):
    target_item_ids: List[str] = Field(default_factory=list)
    # 跨账号目标；与 target_item_ids 二选一，同时给则以 targets 为准。
    targets: List[AIItemKnowledgeTargetRef] = Field(default_factory=list)
    # Kept for older clients. Copying always replaces target drafts.
    overwrite: bool = True


class AIItemKnowledgeImportRequest(BaseModel):
    source_cookie_id: str
    source_item_id: str
```

把 L8064 的 copy 路由替换为：

```python
@ai_router.post("/ai-item-knowledge/{cookie_id}/{item_id}/copy")
def copy_ai_item_knowledge(cookie_id: str, item_id: str, request: AIItemKnowledgeCopyRequest,
                           current_user: Dict[str, Any] = Depends(get_current_user)):
    _get_ai_knowledge_item(cookie_id, item_id, current_user)
    if request.targets:
        pairs = [(t.cookie_id, t.item_id) for t in request.targets]
    else:
        pairs = [(cookie_id, target_id) for target_id in request.target_item_ids]
    if not pairs:
        raise HTTPException(status_code=400, detail='请选择至少一个目标商品')
    for target_cookie in {pair[0] for pair in pairs}:
        if target_cookie != cookie_id:
            _ensure_ai_cookie_access(target_cookie, current_user)
    try:
        result = db_manager.copy_ai_item_knowledge_draft_to_targets(cookie_id, item_id, pairs)
        return {
            **result,
            'message': f"已覆盖 {result['copied_count']} 个商品草稿",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@ai_router.post("/ai-item-knowledge/{cookie_id}/{item_id}/import")
def import_ai_item_knowledge(cookie_id: str, item_id: str, request: AIItemKnowledgeImportRequest,
                             current_user: Dict[str, Any] = Depends(get_current_user)):
    item = _get_ai_knowledge_item(cookie_id, item_id, current_user)
    source_cookie_id = str(request.source_cookie_id or '').strip()
    source_item_id = str(request.source_item_id or '').strip()
    if not source_cookie_id or not source_item_id:
        raise HTTPException(status_code=400, detail='请选择一个来源商品')
    if (source_cookie_id, source_item_id) == (cookie_id, item_id):
        raise HTTPException(status_code=400, detail='来源商品不能是当前商品')
    _get_ai_knowledge_item(source_cookie_id, source_item_id, current_user)
    try:
        result = db_manager.import_ai_item_knowledge_draft(
            cookie_id, item_id, source_cookie_id, source_item_id
        )
        return {
            'message': '已导入为当前商品草稿，确认无误后再发布',
            'source_kind': result['source_kind'],
            **_item_knowledge_payload(cookie_id, item_id, item),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

> 注意 `_get_ai_knowledge_item` 内部已调 `_ensure_ai_cookie_access`，所以对源商品**只调它一次**即可同时拿到归属校验与存在性校验，不要重复写。

### 步骤 2.4 绿 + 提交

```bash
.venv/bin/python -m pytest -q tests/test_item_knowledge_transfer.py
.venv/bin/python -m ruff check reply_server.py db_manager.py
git add reply_server.py tests/test_item_knowledge_transfer.py
git commit -m "feat(api): 新增档案导入端点并让复制支持跨账号目标"
```

---

## 任务 3 · 前端 API 层

**文件：** `frontend/services/api/ai.ts`

把 L341 起的 `copyAIItemKnowledge` 改成同时支持两种目标写法，并新增导入：

```ts
export type AIItemKnowledgeTargetRef = { cookie_id: string; item_id: string };

export const copyAIItemKnowledge = async (
  cookieId: string,
  sourceItemId: string,
  targets: string[] | AIItemKnowledgeTargetRef[]
): Promise<{
  message: string;
  copied_item_ids: string[];
  skipped_item_ids: string[];
  missing_item_ids: string[];
  copied_targets?: AIItemKnowledgeTargetRef[];
  missing_targets?: AIItemKnowledgeTargetRef[];
  source_kind?: 'draft' | 'published';
  copied_count?: number;
  skipped_count?: number;
  missing_count?: number;
  skipped_reasons?: Record<string, string>;
}> => post(`/ai-item-knowledge/${cookieId}/${sourceItemId}/copy`,
  typeof targets[0] === 'string' || targets.length === 0
    ? { target_item_ids: targets as string[] }
    : { targets: targets as AIItemKnowledgeTargetRef[] });

export const importAIItemKnowledge = async (
  cookieId: string,
  itemId: string,
  source: AIItemKnowledgeTargetRef
): Promise<ApiResponse & AIItemKnowledgeProfile & { source_kind: 'draft' | 'published' }> =>
  post(`/ai-item-knowledge/${cookieId}/${itemId}/import`, {
    source_cookie_id: source.cookie_id,
    source_item_id: source.item_id,
  });
```

验证：`cd frontend && npx tsc --noEmit`（零错误）。提交：

```bash
git add frontend/services/api/ai.ts
git commit -m "feat(api-client): 档案导入与跨账号复制的前端接口"
```

---

## 任务 4 · 档案搬运弹窗组件

**文件：** `frontend/components/ItemKnowledgeTransferModal.tsx`、`frontend/components/ItemKnowledgeTransferModal.test.tsx`

### 步骤 4.1 先写测试

```tsx
// @vitest-environment jsdom
import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { copyAIItemKnowledge, getItems, importAIItemKnowledge } from '../services/api';
import ItemKnowledgeTransferModal from './ItemKnowledgeTransferModal';

vi.mock('../services/api', () => ({
  getItems: vi.fn(),
  copyAIItemKnowledge: vi.fn(),
  importAIItemKnowledge: vi.fn(),
}));

const current = { id: 1, cookie_id: 'acc-1', item_id: 'item-a', item_title: '当前商品', item_price: '2.99' };
const pool = [
  current,
  { id: 2, cookie_id: 'acc-1', item_id: 'item-b', item_title: '同账号有档案', item_price: '3.99', knowledge_published_version: 3 },
  { id: 3, cookie_id: 'acc-2', item_id: 'item-c', item_title: '跨账号无档案', item_price: '4.99' },
];
const accounts = [
  { id: 'acc-1', nickname: '账号一' },
  { id: 'acc-2', nickname: '账号二' },
];

describe('ItemKnowledgeTransferModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getItems).mockResolvedValue(pool as any);
  });
  afterEach(cleanup);

  it('导入方向：按账号分组展示候选，只列有档案的商品，单选后可提交', async () => {
    vi.mocked(importAIItemKnowledge).mockResolvedValue({ message: '已导入', source_kind: 'published' } as any);
    const onImported = vi.fn();
    render(
      <ItemKnowledgeTransferModal
        item={current as any} accounts={accounts as any} hasContent dirty={false}
        initialTab="import" onClose={() => undefined} onImported={onImported} onDistributed={() => undefined}
      />
    );
    expect(await screen.findByText('账号一')).toBeTruthy();
    // 无档案商品不能作为导入来源
    expect(screen.queryByRole('radio', { name: /跨账号无档案/ })).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: /同账号有档案/ }));
    fireEvent.click(screen.getByRole('button', { name: '导入为当前商品草稿' }));
    await waitFor(() => expect(importAIItemKnowledge).toHaveBeenCalledWith(
      'acc-1', 'item-a', { cookie_id: 'acc-1', item_id: 'item-b' }
    ));
    await waitFor(() => expect(onImported).toHaveBeenCalled());
  });

  it('分发方向：多选跨账号目标并提交成对目标', async () => {
    vi.mocked(copyAIItemKnowledge).mockResolvedValue({ message: '已覆盖 2 个商品草稿', copied_count: 2 } as any);
    render(
      <ItemKnowledgeTransferModal
        item={current as any} accounts={accounts as any} hasContent dirty={false}
        initialTab="distribute" onClose={() => undefined} onImported={() => undefined} onDistributed={() => undefined}
      />
    );
    fireEvent.click(await screen.findByRole('checkbox', { name: /同账号有档案/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /跨账号无档案/ }));
    fireEvent.click(screen.getByRole('button', { name: /覆盖所选 2 个商品草稿/ }));
    await waitFor(() => expect(copyAIItemKnowledge).toHaveBeenCalledWith('acc-1', 'item-a', [
      { cookie_id: 'acc-1', item_id: 'item-b' },
      { cookie_id: 'acc-2', item_id: 'item-c' },
    ]));
  });

  it('分发方向提示有多少目标会被覆盖', async () => {
    render(
      <ItemKnowledgeTransferModal
        item={current as any} accounts={accounts as any} hasContent dirty={false}
        initialTab="distribute" onClose={() => undefined} onImported={() => undefined} onDistributed={() => undefined}
      />
    );
    fireEvent.click(await screen.findByRole('checkbox', { name: /同账号有档案/ }));
    expect(screen.getByText(/其中 1 个已有档案/)).toBeTruthy();
  });

  it('当前商品没有档案时分发 tab 禁用并给出原因', async () => {
    render(
      <ItemKnowledgeTransferModal
        item={current as any} accounts={accounts as any} hasContent={false} dirty={false}
        initialTab="import" onClose={() => undefined} onImported={() => undefined} onDistributed={() => undefined}
      />
    );
    expect((await screen.findByRole('tab', { name: /复制到其他商品/ })).getAttribute('aria-disabled')).toBe('true');
  });
});
```

### 步骤 4.2 运行确认红

```bash
cd frontend && npx vitest run components/ItemKnowledgeTransferModal.test.tsx
```

### 步骤 4.3 实现组件

要点（视觉细节照抄 Demo 的方案 2B/3B，`docs/research/item-ui-demos/index.html` 里 `transferModal()` 一段）：

- `createPortal` 到 `document.body`，外层 `modal-overlay-centered`，容器 `modal-container` + `style={{ maxWidth: 880 }}`。
- 挂载时 `getItems()` 拉全账号商品，`useMemo` 过滤掉当前商品；导入方向再滤掉 `knowledgeStateOf(candidate) === 'none'`。
- 分组：`accounts.map(acc => [acc, pool.filter(i => i.cookie_id === acc.id)]).filter(([, l]) => l.length)`，当前账号排最前并打「当前账号」标。
- 筛选：关键词（标题或 item_id）、账号 chips、状态 chips。
- 选择：导入方向 `role="radio"` 单选存 `{cookie_id,item_id}`；分发方向 `role="checkbox"` 多选存数组。**a11y name 必须包含商品标题**，否则上面的测试选不中。
- 底部固定条：分发方向显示已选 chips + 「已选 N 个目标，其中 M 个已有档案，草稿会被顶掉」；导入方向显示「将用 X（账号 · 已发布 vN）覆盖当前商品草稿」。
- 提交前若 `dirty`，先 `await onBeforeSubmit?.()` 让父组件保存草稿（复用现有 `saveAIItemKnowledgeDraft` 逻辑，不在本组件里重复实现）。
- 成功后 `pushToast('success', ...)`，失败 `pushToast('error', ...)`，然后 `onClose()`。
- 复制方向的破坏性确认沿用 `confirmDialog`：目标里有已存在档案时，提交前弹一次确认（与全站 16 处 `window.confirm` 替换后的规范一致）。

### 步骤 4.4 绿 + tsc + 提交

```bash
cd frontend && npx vitest run components/ItemKnowledgeTransferModal.test.tsx && npx tsc --noEmit
git add frontend/components/ItemKnowledgeTransferModal.tsx frontend/components/ItemKnowledgeTransferModal.test.tsx
git commit -m "feat(ui): 新增档案搬运弹窗，支持跨账号导入与分发"
```

---

## 任务 5 · 知识档案弹窗接入新组件、拆掉旧内联面板

**文件：** `frontend/components/ItemKnowledgeModal.tsx`、`frontend/components/ItemKnowledgeModal.test.tsx`

### 步骤 5.1 先改测试（旧断言必然红）

`ItemKnowledgeModal.test.tsx` 第 118/143/179/229/244 行这批用例，全部依赖已被移除的内联面板。改法：

- 把 5 个内联复制用例**删掉**（它们的能力已由任务 4 的组件测试覆盖，重复保留会变成对已删 UI 的断言）。
- 新增两个用例：

```tsx
  it('点档案搬运会打开搬运弹窗，并把当前档案状态传下去', async () => {
    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');
    fireEvent.click(screen.getByRole('button', { name: /档案搬运/ }));
    expect(await screen.findByRole('dialog', { name: '档案搬运' })).toBeTruthy();
  });

  it('导入成功后主区直接显示导入来的草稿内容', async () => {
    render(<ItemKnowledgeModal item={sourceItem as any} onClose={() => undefined} />);
    await screen.findByText('草稿档案');
    fireEvent.click(screen.getByRole('button', { name: /档案搬运/ }));
    // 由 mock 的 importAIItemKnowledge 回传 profile，父组件应把 knowledge 换成新内容
    ...
  });
```

同时把 `vi.mock('../services/api', ...)` 里补上 `getItems` 与 `importAIItemKnowledge` 两个 mock，否则子组件挂载即炸。

### 步骤 5.2 改实现

- 删除 state：`copying / copyOpen / availableItems / copyTargetIds / copySearch`，以及 `copyCandidates / visibleCopyCandidates / selectedWithKnowledgeCount` 三个 memo、`toggleCopyTarget / selectAllCopyTargets / selectCopyTargetsWithoutKnowledge / clearCopyTargets / copyKnowledge / refreshCopyCandidates` 六个函数、L100-106 的 `getItemsByCookie` effect，以及 L414-485 的整块 JSX。
- 新增 `const [transferOpen, setTransferOpen] = useState<false | 'import' | 'distribute'>(false);`
- 左栏 `复制到其他商品` 按钮替换为：

```tsx
                <button
                  onClick={() => setTransferOpen('import')}
                  className="w-full px-4 py-3 rounded-lg bg-gray-900 text-white font-bold flex items-center justify-between"
                >
                  <span className="flex items-center gap-2"><ArrowLeftRight className="w-4 h-4" />档案搬运</span>
                  <ChevronRight className="w-4 h-4" />
                </button>
                <p className="text-xs text-gray-500 leading-5">
                  从其他商品（含其他账号）导入一份现成档案，或把当前档案复制给其他商品。
                </p>
```

- 弹窗渲染（放在 `createPortal` 的根 div 内、`modal-container` 之后）：

```tsx
        {transferOpen && (
          <ItemKnowledgeTransferModal
            item={item}
            accounts={accounts}
            hasContent={hasKnowledgeContent(knowledge)}
            dirty={dirty}
            initialTab={transferOpen}
            onBeforeSubmit={async () => {
              if (!dirty) return;
              const saved = await saveAIItemKnowledgeDraft(item.cookie_id, item.item_id, knowledge);
              setProfile(saved);
              setKnowledge(normalizeItemKnowledge(saved.draft));
              setDirty(false);
            }}
            onImported={(imported) => {
              setProfile(imported);
              setKnowledge(normalizeItemKnowledge(imported.draft));
              setDirty(false);
              setMessage('已导入档案为当前草稿，确认内容后再发布');
            }}
            onDistributed={(text) => setMessage(text)}
            onClose={() => setTransferOpen(false)}
          />
        )}
```

- `accounts` 从父组件透传：`ItemKnowledgeModalProps` 增加 `accounts: AccountDetail[]`，`ItemList.tsx` 渲染处补 `accounts={accounts}`（ItemList 本来就有这个 state）。

### 步骤 5.3 绿 + 提交

```bash
cd frontend && npx vitest run components/ItemKnowledgeModal.test.tsx components/ItemKnowledgeTransferModal.test.tsx && npx tsc --noEmit
git add frontend/components/ItemKnowledgeModal.tsx frontend/components/ItemKnowledgeModal.test.tsx frontend/components/ItemList.tsx
git commit -m "refactor(ui): 知识档案弹窗改用档案搬运弹窗，移除左栏内联复制面板"
```

---

## 任务 6 · 商品管理页头：分裂按钮 + 下拉菜单（方案 1C）

**文件：** `frontend/components/ItemList.tsx`、`frontend/components/ItemList.test.tsx`

**交互定义（Demo 面板 `p1c`）：**
- 删除账号下拉左边的独立刷新按钮，页头右侧只剩「账号下拉 + 分裂按钮」。
- 分裂按钮 = 黄色主按钮 +（右侧）`⌄` 触发区，两者外观连成一体。
- 主按钮：选中单个账号 → 「同步商品」；选中「全部账号」→ **「同步全部账号」**（默认值，见下方待确认项 1）。
- 菜单项两条：「仅刷新本地列表」（只调 `loadData`，不碰平台）、「同步全部账号」（串行逐个同步）。
- 转圈只出现在主按钮上，且只绑 `syncing`；切账号造成的 `loading` 不让主按钮转。

**待确认项 1（默认值先按此实现）：** 串行同步遇到失败的账号**继续跑完其余账号**，最后汇总一条「N 个账号同步完成，M 个失败：账号甲、账号乙」；不做自动重试；账号处于 `manual_reauth_required` 时接口本就返回失败，直接计入失败清单，不单独跳过。

### 步骤 6.1 先改测试

`ItemList.test.tsx` L149 现在断言「全部账号时同步商品 disabled」，1C 下改为：

```tsx
    expect(screen.queryByRole('button', { name: '刷新' })).toBeNull();
    expect(screen.getByRole('button', { name: /同步全部账号/ })).not.toBeDisabled();
```

新增三个用例，分别锁住痛点、菜单和串行汇总：

```tsx
  it('切换账号只重载列表，不让主按钮进入加载态', async () => {
    render(<><ToastViewport /><ItemList /></>);
    await screen.findByText('账号一商品');
    fireEvent.change(screen.getByLabelText('商品账号'), { target: { value: 'account-2' } });
    expect(screen.getByRole('button', { name: /同步商品/ })).not.toBeDisabled();
    await screen.findByText('账号二商品');
    expect(syncItemsFromAccount).not.toHaveBeenCalled();
  });

  it('菜单里的「仅刷新本地列表」不调用平台同步', async () => {
    render(<><ToastViewport /><ItemList /></>);
    await screen.findByText('账号一商品');
    fireEvent.click(screen.getByRole('button', { name: '更多同步方式' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: '仅刷新本地列表' }));
    await waitFor(() => expect(getItemsByCookie).toHaveBeenCalledTimes(2));
    expect(syncItemsFromAccount).not.toHaveBeenCalled();
  });

  it('同步全部账号逐个跑完，单个失败不中断并在末尾汇总', async () => {
    vi.mocked(syncItemsFromAccount).mockImplementation(async (cookieId: string) => (
      cookieId === 'account-2'
        ? { success: false, message: '账号登录态已过期' }
        : { success: true, message: '商品同步完成' }
    ));
    render(<><ToastViewport /><ItemList /></>);
    await screen.findByText('账号一商品');
    fireEvent.click(screen.getByRole('button', { name: '更多同步方式' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: '同步全部账号' }));
    await waitFor(() => expect(syncItemsFromAccount).toHaveBeenCalledTimes(2));
    expect(syncItemsFromAccount).toHaveBeenNthCalledWith(1, 'account-1');
    expect(syncItemsFromAccount).toHaveBeenNthCalledWith(2, 'account-2');
    expect(await screen.findByText(/1 个账号同步完成，1 个失败：账号二/)).toBeInTheDocument();
  });
```

### 步骤 6.2 运行确认红

```bash
cd frontend && npx vitest run components/ItemList.test.tsx
```

### 步骤 6.3 改实现

1. 新增状态：

```tsx
  const [syncing, setSyncing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [lastSyncAt, setLastSyncAt] = useState<number | null>(null);
```

2. `handleSync` 把 `setLoading` 换成 `setSyncing`，成功后 `setLastSyncAt(Date.now())`；`handleAccountChange` 保持 `setLoading`（列表骨架用）。
3. 删除 L267-274 的独立刷新按钮。
4. 新增串行同步：

```tsx
  // 逐个账号串行同步：单个失败不中断，跑完后汇总。串行本身即节流，不并发打平台接口。
  const handleSyncAllAccounts = async () => {
    setMenuOpen(false);
    setSyncing(true);
    setStatusText('');
    const failed: string[] = [];
    let succeeded = 0;
    try {
      for (const account of accounts) {
        try {
          const result = await syncItemsFromAccount(account.id);
          if (result?.success === false) throw new Error(result.message || '同步失败');
          succeeded += 1;
        } catch {
          failed.push(account.nickname || account.remark || account.id);
        }
      }
      await loadItemsForAccount(selectedAccount);
      setLastSyncAt(Date.now());
      const message = failed.length
        ? `${succeeded} 个账号同步完成，${failed.length} 个失败：${failed.join('、')}`
        : `${succeeded} 个账号全部同步完成`;
      setStatusText(message);
      pushToast(failed.length ? 'error' : 'success', message);
    } finally {
      setSyncing(false);
    }
  };
```

5. 页头右侧替换为分裂按钮 + 菜单（`relative` 容器托住绝对定位菜单）：

```tsx
          <div className="relative flex self-start">
            <button
              onClick={() => void (canSyncSelectedAccount ? handleSync() : handleSyncAllAccounts())}
              disabled={syncing}
              title={canSyncSelectedAccount
                ? '去闲鱼拉取该账号的在售商品，并刷新列表'
                : '逐个账号串行同步，结束后汇总成功与失败'}
              className="ios-btn-primary flex items-center gap-2 px-6 py-3 rounded-l-2xl font-bold shadow-lg shadow-yellow-200 disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} />
              {syncing ? '同步中…' : canSyncSelectedAccount ? '同步商品' : '同步全部账号'}
            </button>
            <button
              onClick={() => setMenuOpen((current) => !current)}
              disabled={syncing}
              aria-label="更多同步方式"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className="ios-btn-primary rounded-r-2xl border-l border-black/10 px-3 shadow-lg shadow-yellow-200 disabled:opacity-50"
            >
              <ChevronDown className="w-4 h-4" />
            </button>
            {menuOpen && (
              <div role="menu" className="absolute right-0 top-full z-30 mt-2 w-60 rounded-2xl bg-white p-1.5 shadow-xl">
                <button
                  role="menuitem"
                  onClick={() => { setMenuOpen(false); void loadData(); }}
                  className="w-full rounded-xl px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-100"
                >
                  仅刷新本地列表
                  <span className="mt-0.5 block text-xs text-gray-400">不调用闲鱼接口，只重拉数据库里的商品</span>
                </button>
                <button
                  role="menuitem"
                  onClick={() => void handleSyncAllAccounts()}
                  className="w-full rounded-xl px-3 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-100"
                >
                  同步全部账号
                  <span className="mt-0.5 block text-xs text-gray-400">逐个账号串行同步，结束后汇总成功与失败</span>
                </button>
              </div>
            )}
          </div>
```

6. 菜单关闭行为——挂一个 effect，点击别处与按 Escape 都关：

```tsx
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('click', close);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('click', close); document.removeEventListener('keydown', onKey); };
  }, [menuOpen]);
```

配合在分裂按钮容器上加 `onClick={(event) => event.stopPropagation()}`，否则点开就立刻被 document 监听关掉。

7. 状态行（替换现有 `selectedAccountLabel` 那两段小字，避免同一信息出现两次）：

```tsx
        <div className="w-full text-right text-xs text-gray-400">
          {lastSyncAt ? `上次同步：${formatRelativeTime(lastSyncAt)} · ` : ''}
          {canSyncSelectedAccount
            ? `当前账号共 ${items.length} 个商品`
            : `全部账号共 ${items.length} 个商品`}
        </div>
```

`formatRelativeTime` 放 `frontend/utils/`，若已有同类工具就复用（先 `rg "分钟前" frontend/utils`）。`ChevronDown` 从 `lucide-react` 导入。

### 步骤 6.4 绿 + 提交

```bash
cd frontend && npx vitest run components/ItemList.test.tsx && npx tsc --noEmit
git add frontend/components/ItemList.tsx frontend/components/ItemList.test.tsx
git commit -m "feat(ui): 商品管理页头改为分裂按钮，刷新入口收敛为一个"
```

---

## 任务 7 · 门禁与构建

```bash
uptime                                   # load 高于 ~8 就先等
cd /Users/mac/Documents/咸鱼监控台
.venv/bin/python -m pytest -q tests --ignore=outputs
.venv/bin/python -m ruff check .
cd frontend
npx tsc --noEmit
npx vitest run components/ItemList.test.tsx components/ItemKnowledgeModal.test.tsx components/ItemKnowledgeTransferModal.test.tsx
npm run build && npm run verify:build
```

预期：后端 passed 数 ≥ 当前基线（1256 passed + 280 subtests）并新增本轮用例；ruff 全绿；tsc 零错误；三个前端文件全绿；`verify:build` orphaned=0。

提交：`git commit -m "chore: 商品界面优化门禁通过"`（若无文件变化则跳过）。

---

## 任务 8 · 部署（须用户显式授权后再执行）

遵循项目既有派生镜像流程，不在本任务里自行发起：
- 后端改 `db_manager.py`、`reply_server.py` 两个文件 → 派生镜像仅 COPY 这两个文件。
- 前端 → `static/` **整代同源同时刻**拷贝（`assets/` + `index.html` + `.asset-generations.json` 一次成套，2026-08-28 白屏事故教训）。
- 无迁移（不动表结构），发布后核验：容器 healthy、迁移号仍为 `2026082901`、双公网 `/health/ready`、**公网入口 JS 200 且哈希与本地构建一致**、Traceback=0、账号 listener 心跳恢复。
- release 目录留回滚脚本。

---

## 自检

**1. 规格覆盖度**
- 问题 1（两个刷新圈）→ 任务 6。
- 问题 2（从其他商品/其他账号导入）→ 任务 1（DAL）+ 任务 2（路由）+ 任务 3（api）+ 任务 4（弹窗导入 tab）+ 任务 5（接入）。
- 问题 3（复制体验难受）→ 任务 4 的分发 tab + 任务 5 移除旧面板。
- 多版 Demo → `docs/research/item-ui-demos/index.html`（本计划外已交付）。

**2. 占位符扫描：** 任务 4 步骤 4.3 是本计划唯一以要点而非整段代码给出的实现步骤——因为它是纯视觉组件，且有逐像素可抄的 Demo 源码（`transferModal()`）。其余步骤均给了可直接粘贴的代码。

**3. 类型一致性：**
- DAL：`copy_ai_item_knowledge_draft_to_targets(source_cookie_id, source_item_id, targets: List[Tuple[str,str]])`、`import_ai_item_knowledge_draft(target_cookie_id, target_item_id, source_cookie_id, source_item_id)` —— 任务 1 定义，任务 2 按同名同参调用。
- 路由：`POST …/import` body `{source_cookie_id, source_item_id}` —— 任务 2 定义，任务 3 的 `importAIItemKnowledge` 按同名字段发送。
- 前端：`AIItemKnowledgeTargetRef = {cookie_id, item_id}` —— 任务 3 定义，任务 4 的两个提交路径都用它。
- 组件 props `onBeforeSubmit` —— 任务 4 步骤 4.3 与任务 5 的接入代码用的是同一个名字（不是 `onBeforeAction`）。

**4. 已知会变红的既有测试（必须在对应任务内一起改，不能留给最后）：**
- `frontend/components/ItemKnowledgeModal.test.tsx` 5 个内联复制用例 → 任务 5 步骤 5.1。
- `frontend/components/ItemList.test.tsx` L149 → 任务 6 步骤 6.1。
