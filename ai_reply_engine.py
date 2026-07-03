"""
AI回复引擎模块
集成XianyuAutoAgent的AI回复功能到现有项目中

【P0/P1 最小化修改版】
- 修复 P1-1 (高成本): detect_intent 改为本地关键词
- 修复 P0-2 (部署陷阱): 移除客户端缓存，实现无状态
- 修复 P1-3 (健壮性): 增强 Gemini 消息格式化
- 遵照指示，未修复 P0-1 (议价竞争条件)
"""

import os
import json
import time
import sqlite3
import requests  # 确保已导入
import threading
import re
import uuid
from typing import List, Dict, Optional
from loguru import logger
from openai import OpenAI
from db_manager import db_manager


class AIReplyEngine:
    """AI回复引擎"""

    def __init__(self):
        # 修复 P0-2: 移除有状态的缓存，以支持多进程部署
        # self.clients = {}  # 已移除
        # self.agents = {}   # 已移除
        # self.client_last_used = {}  # 已移除
        self._init_default_prompts()
        # 用于控制同一chat_id消息的串行处理
        self._chat_locks = {}
        self._chat_locks_lock = threading.Lock()

    def _init_default_prompts(self):
        """初始化默认提示词"""
        self.default_prompts = {
            'classify': '''你是一个意图分类专家...（此提示词已不再被 detect_intent 使用）''',

            'price': '''你是一位经验丰富的销售专家，擅长议价。
语言要求：简短直接，每句≤10字，总字数≤40字。
议价策略：
1. 根据议价次数递减优惠：第1次小幅优惠，第2次中等优惠，第3次最大优惠
2. 接近最大议价轮数时要坚持底线，强调商品价值
3. 优惠不能超过设定的最大百分比和金额
4. 语气要友好但坚定，突出商品优势
注意：结合商品信息、对话历史和议价设置，给出合适的回复。''',

            'tech': '''你是一位技术专家，专业解答产品相关问题。
语言要求：简短专业，每句≤10字，总字数≤40字。
回答重点：产品功能、使用方法、注意事项。
注意：基于商品信息回答，避免过度承诺。''',

            'default': '''你是一位资深电商卖家，提供优质客服。
语言要求：简短友好，每句≤10字，总字数≤40字。
回答重点：商品介绍、物流、售后等常见问题。
注意：结合商品信息，给出实用建议。'''
        }

    def _create_openai_client(self, cookie_id: str) -> Optional[OpenAI]:
        """
        (原 get_client) 创建指定账号的OpenAI客户端
        修复 P0-2: 移除了缓存逻辑，以支持多进程无状态部署
        """
        settings = db_manager.get_ai_reply_settings(cookie_id)
        if not settings['ai_enabled'] or not settings['api_key']:
            return None

        try:
            logger.info(f"创建新的OpenAI客户端实例 {cookie_id}: base_url={settings['base_url']}, api_key={'***' + settings['api_key'][-4:] if settings['api_key'] else 'None'}")
            client = OpenAI(
                api_key=settings['api_key'],
                base_url=settings['base_url']
            )
            logger.info(f"为账号 {cookie_id} 创建OpenAI客户端成功，实际base_url: {client.base_url}")
            return client
        except Exception as e:
            logger.error(f"创建OpenAI客户端失败 {cookie_id}: {e}")
            return None

    def _is_dashscope_api(self, settings: dict) -> bool:
        """判断是否为DashScope API - 只有选择自定义模型时才使用"""
        model_name = settings.get('model_name', '')
        base_url = settings.get('base_url', '')

        is_custom_model = model_name.lower() in ['custom', '自定义', 'dashscope', 'qwen-custom']
        is_dashscope_url = 'dashscope.aliyuncs.com' in base_url

        logger.info(f"API类型判断: model_name={model_name}, is_custom_model={is_custom_model}, is_dashscope_url={is_dashscope_url}")

        return is_custom_model and is_dashscope_url

    def _is_gemini_api(self, settings: dict) -> bool:
        """判断是否为 Gemini，优先使用平台类型并兼容旧模型名判断。"""
        if settings.get('provider_type'):
            return settings.get('provider_type') == 'gemini'
        model_name = settings.get('model_name', '').lower()
        return 'gemini' in model_name

    def _is_deepseek_api(self, settings: dict) -> bool:
        """判断是否为DeepSeek API。DeepSeek V4 默认开启 thinking，客服短回复需要关闭。"""
        model_name = settings.get('model_name', '').lower()
        base_url = settings.get('base_url', '').lower()
        return 'deepseek' in model_name or 'api.deepseek.com' in base_url

    def _resolve_system_prompt(self, intent: str, custom_prompts_raw: str) -> str:
        """解析自定义提示词，兼容历史JSON格式和当前前端的普通文本输入。"""
        base_prompt = self.default_prompts.get(intent, self.default_prompts['default'])
        custom_prompts_raw = (custom_prompts_raw or '').strip()
        if not custom_prompts_raw:
            return base_prompt

        try:
            custom_prompts = json.loads(custom_prompts_raw)
        except json.JSONDecodeError:
            logger.info("自定义提示词为普通文本，按全局额外规则应用")
            return f"{base_prompt}\n\n额外商品/回复规则：\n{custom_prompts_raw}"

        if isinstance(custom_prompts, dict):
            custom_prompt = custom_prompts.get(intent) or custom_prompts.get('default')
            if isinstance(custom_prompt, str) and custom_prompt.strip():
                return f"{base_prompt}\n\n额外商品/回复规则：\n{custom_prompt.strip()}"
            return base_prompt

        if isinstance(custom_prompts, str) and custom_prompts.strip():
            return f"{base_prompt}\n\n额外商品/回复规则：\n{custom_prompts.strip()}"

        return base_prompt

    @staticmethod
    def _rule_texts(rules: Optional[List]) -> List[str]:
        values = []
        for rule in rules or []:
            text = rule.get('text', '') if isinstance(rule, dict) else str(rule)
            text = str(text).strip()
            if text and text not in values:
                values.append(text)
        return values

    @staticmethod
    def _get_expert_prompt(cookie_id: str, intent: str) -> str:
        """Return the enabled user-level expert strategy for this account."""
        try:
            account = db_manager.get_cookie_details(cookie_id) or {}
            user_id = account.get('user_id')
            if not user_id:
                return ''
            prompts = db_manager.get_skill_agent_prompts(user_id)
            prompt = prompts.get(intent) or prompts.get('default') or {}
            if not prompt.get('enabled', True):
                return ''
            return str(prompt.get('content') or '').strip()
        except Exception as e:
            logger.warning(f"读取专家提示词失败 {cookie_id}/{intent}: {e}")
            return ''

    def build_product_system_prompt(self, intent: str, custom_prompts_raw: str,
                                    item_info: dict, global_rules: Optional[List] = None,
                                    item_rules: Optional[List] = None,
                                    published_knowledge: Optional[Dict] = None,
                                    expert_prompt: str = '') -> str:
        """构建分层提示词，当前商品事实不得被账号级话术覆盖。"""
        base_prompt = self._resolve_system_prompt(intent, custom_prompts_raw)
        global_text = self._rule_texts(global_rules)
        item_text = self._rule_texts(item_rules)
        global_section = "\n".join(f"- {rule}" for rule in global_text) or "- 无"
        item_section = "\n".join(f"- {rule}" for rule in item_text) or "- 无"
        title = str(item_info.get('title') or '未知商品').strip()
        price = str(item_info.get('price') or '未知').strip()
        desc = str(item_info.get('desc') or '暂无商品描述').strip()
        knowledge_text = self._format_item_knowledge(published_knowledge or {})
        expert_text = str(expert_prompt or '').strip() or '无额外专家策略'

        return f"""{base_prompt}

事实与规则优先级：
1. 只能围绕当前商品回答，不得套用其他商品的价格、流程、售后或业务术语。
2. 商品身份和当前展示价格以当前商品为准；已确认商品知识可补充或纠正详情中的业务流程。
3. 当前商品专属规则仅用于补充当前商品；全店规则只约束通用风格与安全。
4. 商品资料没有说明的内容不要猜测，可简短请买家确认或转人工。

全店通用规则：
{global_section}

当前商品事实（最高业务优先级）：
- 商品标题：{title}
- 当前展示价格：{price}
- 商品详情：{desc}

当前商品已确认知识档案：
{knowledge_text or '- 暂无已发布知识，请仅依据商品详情回答'}

当前商品专属规则：
{item_section}

专家回复策略（不得覆盖商品事实）：
{expert_text}"""

    @staticmethod
    def _format_item_knowledge(knowledge: Dict) -> str:
        if not isinstance(knowledge, dict) or not knowledge:
            return ''
        labels = {
            'overview': '商品概况',
            'pricing': '规格与价格',
            'process': '操作流程',
            'after_sales': '售后边界',
            'forbidden': '禁止说法',
            'faqs': '常见问答',
            'notes': '其他补充',
        }
        lines = []
        for key in ('overview', 'pricing', 'process', 'after_sales', 'forbidden', 'faqs', 'notes'):
            value = knowledge.get(key)
            entries = value if isinstance(value, list) else [value] if value else []
            rendered = []
            for entry in entries:
                if not isinstance(entry, dict) or entry.get('status') == 'pending':
                    continue
                if key == 'faqs':
                    question = str(entry.get('question') or '').strip()
                    answer = str(entry.get('answer') or '').strip()
                    text = f"问：{question} 答：{answer}" if question or answer else ''
                elif key == 'pricing':
                    label = str(entry.get('label') or '').strip()
                    amount = str(entry.get('amount') or '').strip()
                    note = str(entry.get('text') or entry.get('note') or '').strip()
                    text = '；'.join(part for part in (label, amount, note) if part)
                else:
                    text = str(entry.get('text') or '').strip()
                if text:
                    rendered.append(text)
            if rendered:
                lines.append(f"- {labels[key]}：" + '；'.join(rendered))
        return '\n'.join(lines)

    @staticmethod
    def parse_item_knowledge_draft(raw: str) -> Dict:
        """解析模型生成的知识档案，所有AI内容必须先人工确认。"""
        text = (raw or '').strip()
        fenced = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError('AI返回的商品知识不是对象')

        result = {
            'overview': {},
            'pricing': [],
            'process': [],
            'after_sales': [],
            'forbidden': [],
            'faqs': [],
            'notes': [],
        }
        overview = data.get('overview')
        if isinstance(overview, str):
            overview = {'text': overview}
        if isinstance(overview, dict) and str(overview.get('text') or '').strip():
            result['overview'] = {
                **overview,
                'id': overview.get('id') or uuid.uuid4().hex,
                'source': 'ai',
                'status': 'pending',
                'text': str(overview.get('text')).strip(),
            }

        for key in ('pricing', 'process', 'after_sales', 'forbidden', 'faqs', 'notes'):
            values = data.get(key) or []
            if isinstance(values, (str, dict)):
                values = [values]
            if not isinstance(values, list):
                continue
            for value in values:
                entry = {'text': value} if isinstance(value, str) else dict(value) if isinstance(value, dict) else {}
                has_content = any(str(entry.get(field) or '').strip() for field in ('text', 'label', 'amount', 'question', 'answer'))
                if not has_content:
                    continue
                entry.update({
                    'id': entry.get('id') or uuid.uuid4().hex,
                    'source': 'ai',
                    'status': 'pending',
                })
                result[key].append(entry)
        return result

    def generate_item_knowledge_draft(self, item_info: Dict, cookie_id: str) -> Dict:
        if not self.is_ai_enabled(cookie_id):
            raise ValueError('该账号未启用AI回复')
        settings = db_manager.get_ai_reply_settings(cookie_id)
        if not settings.get('api_key'):
            raise ValueError('未配置AI API Key')
        messages = [
            {
                'role': 'system',
                'content': '''你是商品知识整理助手。根据商品标题、展示价格和详情生成结构化JSON草稿。
只输出JSON，不要Markdown。不要把不确定内容写成确定事实；可以提出合理建议，但不要编造具体价格、售后承诺或交付流程。
JSON字段固定为 overview, pricing, process, after_sales, forbidden, faqs, notes。
overview是包含text的对象；pricing是包含label、amount、text的数组；faqs是包含question、answer的数组；其余字段是包含text的数组。''',
            },
            {
                'role': 'user',
                'content': f"商品标题：{item_info.get('title', '')}\n当前展示价格：{item_info.get('price', '')}\n商品详情：{item_info.get('desc', '')}",
            },
        ]
        if self._is_dashscope_api(settings):
            raw = self._call_dashscope_api(settings, messages, max_tokens=900, temperature=0.2)
        elif self._is_gemini_api(settings):
            raw = self._call_gemini_api(settings, messages, max_tokens=900, temperature=0.2)
        else:
            client = self._create_openai_client(cookie_id)
            if not client:
                raise ValueError('AI客户端创建失败')
            raw = self._call_openai_api(client, settings, messages, max_tokens=900, temperature=0.2)
        return self.parse_item_knowledge_draft(raw)

    def generate_lab_reply(self, message: str, item_info: dict, cookie_id: str,
                           context: Optional[List[Dict]] = None,
                           training_rules: Optional[List] = None,
                           item_id: str = "",
                           prompt_override: str = "") -> Optional[str]:
        """生成AI训练回复，不写入正式对话记录。"""
        if not self.is_ai_enabled(cookie_id):
            return None

        try:
            intent = self.detect_intent(message, cookie_id)
            settings = db_manager.get_ai_reply_settings(cookie_id)
            saved_rules = db_manager.get_ai_training_rules(cookie_id, item_id)
            knowledge_profile = db_manager.get_ai_item_knowledge_profile(cookie_id, item_id)
            training_knowledge = knowledge_profile.get('draft') or knowledge_profile.get('published') or {}
            global_rules = list(saved_rules['global_rules'])
            item_rules = list(saved_rules['item_rules'])
            for rule in training_rules or []:
                if isinstance(rule, dict) and rule.get('scope') == 'global':
                    global_rules.append(rule)
                else:
                    item_rules.append(rule)
            system_prompt = self.build_product_system_prompt(
                intent,
                settings.get('custom_prompts', ''),
                item_info,
                global_rules,
                item_rules,
                training_knowledge,
                self._get_expert_prompt(cookie_id, intent),
            )

            prompt_override = (prompt_override or '').strip()
            if prompt_override:
                system_prompt = f"{system_prompt}\n\n本次训练临时补充：\n{prompt_override}"

            context = context or []
            context_str = "\n".join([f"{msg.get('role', '')}: {msg.get('content', '')}" for msg in context[-12:]])

            user_prompt = f"""当前商品的训练对话历史：
{context_str}

用户消息：{message}

请根据以上信息生成回复："""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            if self._is_dashscope_api(settings):
                return self._call_dashscope_api(settings, messages, max_tokens=160, temperature=0.55)
            if self._is_gemini_api(settings):
                return self._call_gemini_api(settings, messages, max_tokens=160, temperature=0.55)

            client = self._create_openai_client(cookie_id)
            if not client:
                return None
            return self._call_openai_api(client, settings, messages, max_tokens=160, temperature=0.55)

        except Exception as e:
            logger.error(f"AI训练回复生成失败 {cookie_id}: {e}")
            return None

    def _call_dashscope_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用DashScope API"""
        base_url = settings['base_url']
        if '/apps/' in base_url:
            app_id = base_url.split('/apps/')[-1].split('/')[0]
        else:
            raise ValueError("DashScope API URL中未找到app_id")

        url = f"https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion"

        system_content = ""
        user_content = ""
        for msg in messages:
            if msg['role'] == 'system':
                system_content = msg['content']
            elif msg['role'] == 'user':
                user_content = msg['content'] # 假设 user prompt 已在 generate_reply 中构建好

        if system_content and user_content:
            prompt = f"{system_content}\n\n用户问题：{user_content}\n\n请直接回答用户的问题："
        elif user_content:
            prompt = user_content
        else:
            prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

        data = {
            "input": {"prompt": prompt},
            "parameters": {"max_tokens": max_tokens, "temperature": temperature},
            "debug": {}
        }
        headers = {
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json"
        }

        logger.info(f"DashScope API请求: {url}")
        logger.info(f"发送的prompt: {prompt[:100]}...") # 避免 prompt 过长
        logger.debug(f"请求数据: {json.dumps(data, ensure_ascii=False)}")

        response = requests.post(url, headers=headers, json=data, timeout=30)

        if response.status_code != 200:
            logger.error(f"DashScope API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"DashScope API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        logger.debug(f"DashScope API响应: {json.dumps(result, ensure_ascii=False)}")

        if 'output' in result and 'text' in result['output']:
            return result['output']['text'].strip()
        else:
            raise Exception(f"DashScope API响应格式错误: {result}")

    def _call_gemini_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """
        调用Google Gemini REST API (v1beta)
        """
        api_key = settings['api_key']
        model_name = settings['model_name']

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

        headers = {"Content-Type": "application/json"}

        # --- 转换消息格式 (修复 P1-3: 增强健壮性) ---
        system_instruction = ""
        user_content_parts = []

        # 遍历消息，找到 system 和所有的 user parts
        for msg in messages:
            if msg['role'] == 'system':
                system_instruction = msg['content']
            elif msg['role'] == 'user':
                # 我们只关心 user content
                user_content_parts.append(msg['content'])

        # 将所有 user parts 合并为最后的 user_content
        # 在我们的使用场景中 (generate_reply)，只会有一个 user part，但这样更安全
        user_content = "\n".join(user_content_parts)

        if not user_content:
            logger.warning(f"Gemini API 调用: 未在消息中找到 'user' 角色内容。Messages: {messages}")
            raise ValueError("未在消息中找到用户内容 (user content)")
        # --- 消息格式转换结束 ---

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_content}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        logger.info(f"Calling Gemini REST API: {url.split('?')[0]}")
        logger.debug(f"Gemini Payload: {json.dumps(payload, ensure_ascii=False)}")

        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code != 200:
            logger.error(f"Gemini API 请求失败: {response.status_code} - {response.text}")
            raise Exception(f"Gemini API 请求失败: {response.status_code} - {response.text}")

        result = response.json()
        logger.debug(f"Gemini API 响应: {json.dumps(result, ensure_ascii=False)}")

        try:
            reply_text = result['candidates'][0]['content']['parts'][0]['text']
            return reply_text.strip()
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Gemini API 响应格式错误: {result} - {e}")
            raise Exception(f"Gemini API 响应格式错误: {result}")

    def _call_openai_api(self, client: OpenAI, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用OpenAI兼容API"""
        try:
            logger.info(f"调用OpenAI API: model={settings['model_name']}, base_url={settings.get('base_url', 'default')}")
            kwargs = {
                "model": settings['model_name'],
                "messages": messages,
                "max_tokens": max(max_tokens, 160),
                "temperature": temperature,
            }
            if self._is_deepseek_api(settings):
                # DeepSeek V4 thinking mode defaults to enabled. With short customer-service
                # budgets it can return reasoning without final content, so force non-thinking.
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

            response = client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            content = (message.content or '').strip()
            if not content:
                finish_reason = getattr(response.choices[0], 'finish_reason', '')
                usage = getattr(response, 'usage', None)
                logger.warning(f"OpenAI兼容API返回空内容: finish_reason={finish_reason}, usage={usage}")
                return ''
            return content
        except Exception as e:
            logger.error(f"OpenAI API调用失败: {e}")
            # 如果有详细的错误信息，打印出来
            if hasattr(e, 'response'):
                logger.error(f"响应状态码: {getattr(e.response, 'status_code', 'unknown')}")
                logger.error(f"响应内容: {getattr(e.response, 'text', 'unknown')}")
            raise

    def is_ai_enabled(self, cookie_id: str) -> bool:
        """检查指定账号是否启用AI回复"""
        settings = db_manager.get_ai_reply_settings(cookie_id)
        return settings['ai_enabled']

    def detect_intent(self, message: str, cookie_id: str) -> str:
        """
        检测用户消息意图 (基于关键词的本地检测)
        修复 P1-1: 移除了AI调用，以降低成本和延迟。
        """
        try:
            # 检查AI是否启用，如果未启用，不应执行任何AI相关逻辑
            # 注意：此检查在 generate_reply 的开头已经做过，但保留此处作为第二道防线
            settings = db_manager.get_ai_reply_settings(cookie_id)
            if not settings['ai_enabled']:
                return 'default'

            msg_lower = message.lower()

            # 价格相关关键词
            price_keywords = [
                '便宜', '优惠', '刀', '降价', '包邮', '价格', '多少钱', '能少', '还能', '最低', '底价',
                '实诚价', '到100', '能到', '包个邮', '给个价', '什么价' # <-- 增加这些“口语化”的词
            ]

            # 同样，你也可以通过正则表达式来匹配纯数字，比如 "100" "80"
            # 但那可能有点复杂，先加关键词是最小改动
            if any(kw in msg_lower for kw in price_keywords):
                logger.debug(f"本地意图检测: price ({message})")
                return 'price'

            # 技术相关关键词
            tech_keywords = ['怎么用', '参数', '坏了', '故障', '设置', '说明书', '功能', '用法', '教程', '驱动']
            if any(kw in msg_lower for kw in tech_keywords):
                logger.debug(f"本地意图检测: tech ({message})")
                return 'tech'

            logger.debug(f"本地意图检测: default ({message})")
            return 'default'

        except Exception as e:
            logger.error(f"本地意图检测失败 {cookie_id}: {e}")
            return 'default'

    def _get_chat_lock(self, chat_id: str) -> threading.Lock:
        """获取指定chat_id的锁，如果不存在则创建"""
        with self._chat_locks_lock:
            if chat_id not in self._chat_locks:
                self._chat_locks[chat_id] = threading.Lock()
            return self._chat_locks[chat_id]

    def generate_reply(self, message: str, item_info: dict, chat_id: str,
                      cookie_id: str, user_id: str, item_id: str,
                      skip_wait: bool = False) -> Optional[str]:
        """生成AI回复"""
        if not self.is_ai_enabled(cookie_id):
            return None

        try:
            # 先检测意图（用于后续保存）
            intent = self.detect_intent(message, cookie_id)
            logger.info(f"检测到意图: {intent} (账号: {cookie_id})")

            # 在锁外先保存用户消息到数据库，让所有消息都能立即保存
            message_created_at = self.save_conversation(chat_id, cookie_id, user_id, item_id, "user", message, intent)

            # 如果调用方已经实现了去抖（debounce），可以通过 skip_wait=True 跳过内部等待
            if not skip_wait:
                logger.info(f"【{cookie_id}】消息已保存，等待10秒收集后续消息: {message[:20]}... (时间:{message_created_at})")
                # 固定等待10秒，等待可能的后续消息（在锁外延迟，避免阻塞其他消息保存）
                time.sleep(10)
            else:
                logger.info(f"【{cookie_id}】消息已保存（外部防抖已启用，跳过内部等待）: {message[:20]}... (时间:{message_created_at})")

            # 获取该chat_id的锁，确保同一对话的消息串行处理
            chat_lock = self._get_chat_lock(chat_id)

            # 使用锁确保同一chat_id的消息串行处理
            with chat_lock:
                # 获取最近时间窗口内的所有用户消息
                # 如果 skip_wait=True（外部防抖），查询窗口为6秒（1秒防抖 + 5秒缓冲）
                # 如果 skip_wait=False（内部等待），查询窗口为25秒（10秒等待 + 10秒消息间隔 + 5秒缓冲）
                query_seconds = 6 if skip_wait else 25
                recent_messages = self._get_recent_user_messages(chat_id, cookie_id, item_id, seconds=query_seconds)
                logger.info(f"【{cookie_id}】最近{query_seconds}秒内的消息: {[msg['content'][:20] for msg in recent_messages]}")

                if recent_messages and len(recent_messages) > 0:
                    # 只处理最后一条消息（时间戳最新的）
                    latest_message = recent_messages[-1]
                    if message_created_at != latest_message['created_at']:
                        logger.info(f"【{cookie_id}】检测到有更新的消息，跳过当前消息: {message[:20]}... (时间:{message_created_at})，最新消息: {latest_message['content'][:20]}... (时间:{latest_message['created_at']})")
                        return None
                    else:
                        logger.info(f"【{cookie_id}】当前消息是最新消息，开始处理: {message[:20]}... (时间:{message_created_at})")

                # 1. 获取AI回复设置
                settings = db_manager.get_ai_reply_settings(cookie_id)

                # 3. 获取对话历史
                context = self.get_conversation_context(chat_id, cookie_id, item_id)

                # 4. 获取议价次数
                bargain_count = self.get_bargain_count(chat_id, cookie_id, item_id)

                # 5. 检查议价轮数限制 (P0-1 竞争条件风险点 - 遵照指示未修改)
                if intent == "price":
                    max_bargain_rounds = settings.get('max_bargain_rounds', 3)
                    if bargain_count >= max_bargain_rounds:
                        logger.info(f"议价次数已达上限 ({bargain_count}/{max_bargain_rounds})，拒绝继续议价")
                        refuse_reply = f"抱歉，这个价格已经是最优惠的了，不能再便宜了哦！"
                        self.save_conversation(chat_id, cookie_id, user_id, item_id, "assistant", refuse_reply, intent)
                        return refuse_reply

                # 6. 构建提示词
                training_rules = db_manager.get_ai_training_rules(cookie_id, item_id)
                knowledge_profile = db_manager.get_ai_item_knowledge_profile(cookie_id, item_id)
                system_prompt = self.build_product_system_prompt(
                    intent,
                    settings.get('custom_prompts', ''),
                    item_info,
                    training_rules['global_rules'],
                    training_rules['item_rules'],
                    knowledge_profile.get('published') or {},
                    self._get_expert_prompt(cookie_id, intent),
                )

                # 7. 构建商品信息
                item_desc = f"商品标题: {item_info.get('title', '未知')}\n"
                item_desc += f"商品价格: {item_info.get('price', '未知')}元\n"
                item_desc += f"商品描述: {item_info.get('desc', '无')}"

                # 8. 构建对话历史
                context_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in context[-10:]])  # 最近10条

                # 9. 构建用户消息
                max_bargain_rounds = settings.get('max_bargain_rounds', 3)
                max_discount_percent = settings.get('max_discount_percent', 10)
                max_discount_amount = settings.get('max_discount_amount', 100)

                user_prompt = f"""商品信息：
{item_desc}

对话历史：
{context_str}

议价设置：
- 当前议价次数：{bargain_count}
- 最大议价轮数：{max_bargain_rounds}
- 最大优惠百分比：{max_discount_percent}%
- 最大优惠金额：{max_discount_amount}元

用户消息：{message}

请根据以上信息生成回复："""

                # 10. 调用AI生成回复
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]

                reply = None # 初始化 reply 变量

                if self._is_dashscope_api(settings):
                    logger.info(f"使用DashScope API生成回复")
                    reply = self._call_dashscope_api(settings, messages, max_tokens=100, temperature=0.7)

                elif self._is_gemini_api(settings):
                    logger.info(f"使用Gemini API生成回复")
                    reply = self._call_gemini_api(settings, messages, max_tokens=100, temperature=0.7)

                else:
                    logger.info(f"使用OpenAI兼容API生成回复")
                    # 修复 P0-2: 调用已修改的无状态客户端创建方法
                    client = self._create_openai_client(cookie_id)
                    if not client:
                        return None
                    logger.debug(f"AI消息已构建: count={len(messages)}, system_chars={len(system_prompt)}, user_chars={len(user_prompt)}")
                    reply = self._call_openai_api(client, settings, messages, max_tokens=100, temperature=0.7)

                # 11. 保存AI回复到对话记录
                self.save_conversation(chat_id, cookie_id, user_id, item_id, "assistant", reply, intent)

                # 12. 更新议价次数 (此方法已在 get_bargain_count 中通过 SQL COUNT(*) 隐式实现)
                if intent == "price":
                    # self.increment_bargain_count(chat_id, cookie_id) # 此行原先就没有，保持不变
                    pass

                logger.info(f"AI回复生成成功 (账号: {cookie_id}, 回复长度: {len(reply)})")
                return reply

        except Exception as e:
            logger.error(f"AI回复生成失败 {cookie_id}: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'url'):
                logger.error(f"请求URL: {e.response.url}")
            if hasattr(e, 'request') and hasattr(e.request, 'url'):
                logger.error(f"请求URL: {e.request.url}")
            return None

    async def generate_reply_async(self, message: str, item_info: dict, chat_id: str,
                                   cookie_id: str, user_id: str, item_id: str,
                                   skip_wait: bool = False) -> Optional[str]:
        """
        异步包装器：在独立线程池中执行同步的 `generate_reply`，并返回结果。
        这样可以在异步代码中直接 await，而不阻塞事件循环。
        """
        try:
            import asyncio as _asyncio
            return await _asyncio.to_thread(self.generate_reply, message, item_info, chat_id, cookie_id, user_id, item_id, skip_wait)
        except Exception as e:
            logger.error(f"异步生成回复失败: {e}")
            return None

    def get_conversation_context(self, chat_id: str, cookie_id: str, item_id: str, limit: int = 20) -> List[Dict]:
        """获取对话上下文"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT role, content FROM ai_conversations
                WHERE chat_id = ? AND cookie_id = ? AND item_id = ?
                ORDER BY created_at DESC LIMIT ?
                ''', (chat_id, cookie_id, item_id, limit))

                results = cursor.fetchall()
                context = [{"role": row[0], "content": row[1]} for row in reversed(results)]
                return context
        except Exception as e:
            logger.error(f"获取对话上下文失败: {e}")
            return []

    def save_conversation(self, chat_id: str, cookie_id: str, user_id: str,
                         item_id: str, role: str, content: str, intent: str = None) -> Optional[str]:
        """保存对话记录，返回创建时间"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                INSERT INTO ai_conversations
                (cookie_id, chat_id, user_id, item_id, role, content, intent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (cookie_id, chat_id, user_id, item_id, role, content, intent))
                db_manager.conn.commit()

                # 获取刚插入记录的created_at
                cursor.execute('''
                SELECT created_at FROM ai_conversations
                WHERE rowid = last_insert_rowid()
                ''')
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.error(f"保存对话记录失败: {e}")
            return None
    def get_bargain_count(self, chat_id: str, cookie_id: str, item_id: str) -> int:
        """获取议价次数"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT COUNT(*) FROM ai_conversations
                WHERE chat_id = ? AND cookie_id = ? AND item_id = ? AND intent = 'price' AND role = 'user'
                ''', (chat_id, cookie_id, item_id))

                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"获取议价次数失败: {e}")
            return 0

    def _get_recent_user_messages(self, chat_id: str, cookie_id: str, item_id: str, seconds: int = 2) -> List[Dict]:
        """获取最近seconds秒内的所有用户消息（包含内容和时间戳）"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                # 先查询所有该chat的user消息，用于调试
                cursor.execute('''
                SELECT content, created_at,
                       julianday('now') - julianday(created_at) as time_diff_days,
                       (julianday('now') - julianday(created_at)) * 86400.0 as time_diff_seconds
                FROM ai_conversations
                WHERE chat_id = ? AND cookie_id = ? AND item_id = ? AND role = 'user'
                ORDER BY created_at DESC LIMIT 10
                ''', (chat_id, cookie_id, item_id))

                all_messages = cursor.fetchall()
                logger.info(f"【调试】chat_id={chat_id} 最近10条user消息: {[(msg[0][:10], msg[1], f'{msg[3]:.2f}秒前') for msg in all_messages]}")

                # 正式查询
                cursor.execute('''
                SELECT content, created_at FROM ai_conversations
                WHERE chat_id = ? AND cookie_id = ? AND item_id = ? AND role = 'user'
                AND julianday('now') - julianday(created_at) < (? / 86400.0)
                ORDER BY created_at ASC
                ''', (chat_id, cookie_id, item_id, seconds))

                results = cursor.fetchall()
                return [{"content": row[0], "created_at": row[1]} for row in results]
        except Exception as e:
            logger.error(f"获取最近用户消息列表失败: {e}")
            return []

    def increment_bargain_count(self, chat_id: str, cookie_id: str):
        """(此方法已废弃，通过 get_bargain_count 的 SQL 查询实现)"""
        pass

    #
    # --- 修复 P0-2: 移除所有有状态的缓存管理方法 ---
    #

    # def clear_client_cache(self, cookie_id: str = None):
    #     """(已移除) 清理客户端缓存"""
    #     pass

    # def cleanup_unused_clients(self, max_idle_hours: int = 24):
    #     """(已移除) 清理长时间未使用的客户端"""
    #     pass


# 全局AI回复引擎实例
ai_reply_engine = AIReplyEngine()
