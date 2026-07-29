from __future__ import annotations
import asyncio
import hashlib
import time
from typing import Any, Dict, List, Tuple, Optional
from loguru import logger
from db_manager import db_manager

__all__ = ["CookieManager", "manager"]


def _mask_cookie_id(cookie_id: str) -> str:
    value = str(cookie_id or "")
    if value.startswith("account_") and len(value) == 18:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"account_{digest}"


class CookieManager:
    """管理多账号 Cookie 及其对应的 XianyuLive 任务和关键字"""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.cookies: Dict[str, str] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.keywords: Dict[str, List[Tuple[str, str]]] = {}
        self.cookie_status: Dict[str, bool] = {}  # 账号启用状态
        self.auto_confirm_settings: Dict[str, bool] = {}  # 自动确认发货设置
        self.cookie_user_ids: Dict[str, Optional[int]] = {}
        self.task_status: Dict[str, dict] = {}  # 账号运行状态诊断
        self._task_locks: Dict[str, asyncio.Lock] = {}  # 每个cookie_id的任务锁，防止重复创建
        self._runtime_action_locks: Dict[str, asyncio.Lock] = {}
        self._runtime_reconcile_lock = asyncio.Lock()
        self._task_generations: Dict[str, int] = {}
        self._load_from_db()

    @staticmethod
    def _database_runtime_snapshot() -> Dict[str, Any]:
        cookies = dict(db_manager.get_all_cookies())
        keywords = {
            cookie_id: list(values)
            for cookie_id, values in db_manager.get_all_keywords().items()
            if cookie_id in cookies
        }
        statuses = {
            cookie_id: bool(enabled)
            for cookie_id, enabled in db_manager.get_all_cookie_status().items()
            if cookie_id in cookies
        }
        auto_confirm: Dict[str, bool] = {}
        owners: Dict[str, Optional[int]] = {}
        for cookie_id in cookies:
            details = db_manager.get_cookie_details(cookie_id) or {}
            statuses.setdefault(cookie_id, True)
            auto_confirm[cookie_id] = bool(
                details.get("auto_confirm", db_manager.get_auto_confirm(cookie_id))
            )
            owners[cookie_id] = details.get("user_id")
        return {
            "cookies": cookies,
            "keywords": keywords,
            "statuses": statuses,
            "auto_confirm": auto_confirm,
            "owners": owners,
        }

    def _apply_runtime_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self.cookies = dict(snapshot["cookies"])
        self.keywords = {
            cookie_id: list(values)
            for cookie_id, values in snapshot["keywords"].items()
        }
        self.cookie_status = dict(snapshot["statuses"])
        self.auto_confirm_settings = dict(snapshot["auto_confirm"])
        self.cookie_user_ids = dict(snapshot["owners"])

    def _load_from_db(self) -> bool:
        """Load one complete database snapshot without mutating active tasks."""
        try:
            snapshot = self._database_runtime_snapshot()
            self._apply_runtime_snapshot(snapshot)
            logger.info(
                f"从数据库加载了 {len(self.cookies)} 个Cookie、"
                f"{len(self.keywords)} 组关键字、{len(self.cookie_status)} 个状态记录和 "
                f"{len(self.auto_confirm_settings)} 个自动确认设置"
            )
            return True
        except Exception as exc:
            logger.error(f"从数据库加载数据失败: error_type={type(exc).__name__}")
            return False

    async def reload_from_db(self, *, shutdown_timeout: float = 10.0) -> Dict[str, Any]:
        """Compatibility alias for the task-aware runtime reconciliation path."""
        return await self.reconcile_from_db(shutdown_timeout=shutdown_timeout)

    async def _stop_runtime_task(
        self,
        cookie_id: str,
        *,
        shutdown_timeout: float,
    ) -> Dict[str, Any]:
        account_ref = _mask_cookie_id(cookie_id)
        action_lock = self._runtime_action_locks.setdefault(cookie_id, asyncio.Lock())
        task_lock = self._task_locks.setdefault(cookie_id, asyncio.Lock())
        async with action_lock:
            async with task_lock:
                task = self.tasks.get(cookie_id)
                if task is None:
                    return {"success": True, "status": "not_running"}
                if task.done():
                    self.tasks.pop(cookie_id, None)
                    self._consume_task_result(task)
                    return {"success": True, "status": "already_stopped"}
                if task is asyncio.current_task():
                    return {"success": False, "status": "current_task_conflict"}

            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=shutdown_timeout)
            if task not in done:
                logger.error(
                    f"【{account_ref}】账号监听停止超时，保留旧任务并拒绝继续变更"
                )
                return {"success": False, "status": "shutdown_timeout"}

            self._consume_task_result(task)
            async with task_lock:
                if self.tasks.get(cookie_id) is task:
                    self.tasks.pop(cookie_id, None)
            return {"success": True, "status": "stopped"}

    async def reconcile_from_db(
        self,
        *,
        shutdown_timeout: float = 10.0,
    ) -> Dict[str, Any]:
        """Reconcile the database snapshot with listeners on the owning event loop."""
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("runtime_reconcile_wrong_event_loop")
        snapshot = await asyncio.to_thread(self._database_runtime_snapshot)

        async with self._runtime_reconcile_lock:
            old_cookies = dict(self.cookies)
            old_owners = dict(self.cookie_user_ids)
            new_cookies = snapshot["cookies"]
            new_owners = snapshot["owners"]
            new_statuses = snapshot["statuses"]

            removed_ids = set(old_cookies) - set(new_cookies)
            changed_ids = {
                cookie_id
                for cookie_id in set(old_cookies) & set(new_cookies)
                if (
                    old_cookies[cookie_id] != new_cookies[cookie_id]
                    or old_owners.get(cookie_id) != new_owners.get(cookie_id)
                )
            }
            disabled_ids = {
                cookie_id
                for cookie_id in new_cookies
                if not new_statuses.get(cookie_id, True)
            }
            stop_targets = removed_ids | changed_ids | disabled_ids
            failed_ids = set()
            stopped_ids = set()

            for cookie_id in sorted(stop_targets):
                stop_result = await self._stop_runtime_task(
                    cookie_id,
                    shutdown_timeout=shutdown_timeout,
                )
                if stop_result["success"]:
                    stopped_ids.add(cookie_id)
                else:
                    failed_ids.add(cookie_id)

            self._apply_runtime_snapshot(snapshot)

            started_ids = set()
            restarted_ids = set()
            for cookie_id, cookie_value in new_cookies.items():
                if not new_statuses.get(cookie_id, True):
                    continue
                existing = self.tasks.get(cookie_id)
                if existing is not None and not existing.done():
                    continue
                if cookie_id in failed_ids:
                    continue
                try:
                    task = self.loop.create_task(
                        self._run_xianyu(
                            cookie_id,
                            cookie_value,
                            new_owners.get(cookie_id),
                        ),
                        name=f"xianyu-listener:{_mask_cookie_id(cookie_id)}",
                    )
                    self.tasks[cookie_id] = task
                    if cookie_id in changed_ids:
                        restarted_ids.add(cookie_id)
                    elif cookie_id not in old_cookies:
                        started_ids.add(cookie_id)
                except Exception as exc:
                    failed_ids.add(cookie_id)
                    logger.error(
                        f"【{_mask_cookie_id(cookie_id)}】启动监听失败: "
                        f"error_type={type(exc).__name__}"
                    )

            for cookie_id in removed_ids:
                if cookie_id not in self.tasks:
                    self._task_locks.pop(cookie_id, None)
                    self._runtime_action_locks.pop(cookie_id, None)
                    self._task_generations.pop(cookie_id, None)
                    self.task_status.pop(cookie_id, None)

            result = {
                "success": not failed_ids,
                "removed": len(removed_ids - failed_ids),
                "restarted": len(restarted_ids),
                "started": len(started_ids),
                "stopped": len((disabled_ids & stopped_ids) - failed_ids),
                "failed": len(failed_ids),
                "error_code": (
                    None if not failed_ids else "runtime_reconcile_incomplete"
                ),
            }
            logger.info(
                "账号监听运行态对账完成: "
                f"removed={result['removed']}, restarted={result['restarted']}, "
                f"started={result['started']}, stopped={result['stopped']}, "
                f"failed={result['failed']}"
            )
            return result

    async def shutdown(self) -> None:
        """Cancel every account listener on the manager's owning event loop."""
        tasks = [task for task in self.tasks.values() if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        logger.info("CookieManager 账号监听任务已全部停止")

    # ------------------------ 内部协程 ------------------------
    async def _run_xianyu(
        self,
        cookie_id: str,
        cookie_value: str,
        user_id: int = None,
        runtime_state: dict = None,
    ):
        """在事件循环中启动 XianyuLive.main"""
        account_ref = _mask_cookie_id(cookie_id)
        logger.info(f"【{account_ref}】_run_xianyu方法开始执行...")
        self.task_status[cookie_id] = {
            "running": True,
            "last_start_time": time.time(),
            "last_end_time": None,
            "last_error": "",
            "last_exit_reason": "",
        }

        try:
            logger.info(f"【{account_ref}】正在导入XianyuLive...")
            from XianyuAutoAsync import XianyuLive  # 延迟导入，避免循环
            logger.info(f"【{account_ref}】XianyuLive导入成功")

            logger.info(f"【{account_ref}】开始创建XianyuLive实例...")
            logger.info(f"【{account_ref}】Cookie值长度: {len(cookie_value)}")
            live = XianyuLive(
                cookie_value,
                cookie_id=cookie_id,
                user_id=user_id,
                runtime_state=runtime_state,
            )
            logger.info(f"【{account_ref}】XianyuLive实例创建成功，开始调用main()...")

            # 强制刷新日志，确保日志被写入
            try:
                import sys
                sys.stdout.flush()
            except:
                pass

            await live.main()

            # main() 正常退出（不应该发生，因为main()内部有无限循环）
            logger.warning(f"【{account_ref}】XianyuLive.main() 正常退出（这通常不应该发生）")
            self.task_status[cookie_id].update({
                "last_error": "XianyuLive 任务已退出，可能 Token 获取失败或 WebSocket 初始化失败",
                "last_exit_reason": "main_returned",
            })
        except asyncio.CancelledError:
            logger.info(f"【{account_ref}】XianyuLive 任务已取消")
            self.task_status.setdefault(cookie_id, {}).update({
                "last_error": "",
                "last_exit_reason": "cancelled",
            })
            # 强制刷新日志
            try:
                import sys
                sys.stdout.flush()
            except:
                pass
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            logger.error(f"【{account_ref}】XianyuLive 任务异常: error_type={error_type}")
            self.task_status.setdefault(cookie_id, {}).update({
                "last_error": f"runtime_error:{error_type}",
                "last_exit_reason": "exception",
            })
            # 强制刷新日志
            try:
                import sys
                sys.stdout.flush()
            except:
                pass
        finally:
            logger.info(f"【{account_ref}】_run_xianyu方法执行结束")
            self.task_status.setdefault(cookie_id, {}).update({
                "running": False,
                "last_end_time": time.time(),
            })
            # 确保日志被刷新
            try:
                import sys
                sys.stdout.flush()
            except:
                pass

    async def _add_cookie_async(
        self,
        cookie_id: str,
        cookie_value: str,
        user_id: int = None,
        runtime_state: dict = None,
    ):
        return await self.replace_cookie(
            cookie_id,
            cookie_value,
            save_to_db=True,
            user_id=user_id,
            runtime_state=runtime_state,
        )

    async def _remove_cookie_async(self, cookie_id: str):
        # 获取或创建该cookie_id的锁
        if cookie_id not in self._task_locks:
            self._task_locks[cookie_id] = asyncio.Lock()
        account_ref = _mask_cookie_id(cookie_id)

        async with self._task_locks[cookie_id]:
            task = self.tasks.pop(cookie_id, None)
            if task:
                task.cancel()
                try:
                    # 等待任务完全清理，确保资源释放
                    await asyncio.wait_for(task, timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning(f"【{account_ref}】等待任务停止超时（10秒），强制继续")
                except asyncio.CancelledError:
                    # 任务被取消是预期行为
                    pass
                except Exception as exc:
                    logger.error(
                        f"【{account_ref}】等待任务清理时出错: "
                        f"error_type={type(exc).__name__}"
                    )

            self.cookies.pop(cookie_id, None)
            self.keywords.pop(cookie_id, None)
            # 清理锁
            self._task_locks.pop(cookie_id, None)
            # 从数据库删除
            db_manager.delete_cookie(cookie_id)
            logger.info(f"已移除账号: {account_ref}")

    # ------------------------ 对外线程安全接口 ------------------------
    def add_cookie(
        self,
        cookie_id: str,
        cookie_value: str,
        kw_list: Optional[List[Tuple[str, str]]] = None,
        user_id: int = None,
        runtime_state: dict = None,
    ):
        """线程安全新增 Cookie 并启动任务"""
        if kw_list is not None:
            self.keywords[cookie_id] = kw_list
        else:
            self.keywords.setdefault(cookie_id, [])
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop and current_loop == self.loop:
            # 同一事件循环中，直接调度
            return self.loop.create_task(
                self._add_cookie_async(
                    cookie_id,
                    cookie_value,
                    user_id,
                    runtime_state=runtime_state,
                )
            )
        else:
            fut = asyncio.run_coroutine_threadsafe(
                self._add_cookie_async(
                    cookie_id,
                    cookie_value,
                    user_id,
                    runtime_state=runtime_state,
                ),
                self.loop,
            )
            return fut.result()

    def remove_cookie(self, cookie_id: str):
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop and current_loop == self.loop:
            return self.loop.create_task(self._remove_cookie_async(cookie_id))
        else:
            fut = asyncio.run_coroutine_threadsafe(self._remove_cookie_async(cookie_id), self.loop)
            return fut.result()

    async def replace_cookie(
        self,
        cookie_id: str,
        new_value: str,
        *,
        save_to_db: bool = True,
        user_id: int = None,
        runtime_state: dict = None,
        expected_cookie_revision: int = None,
        expected_cookie_value: str = None,
        shutdown_timeout: float = 10.0,
    ) -> dict:
        """Replace one listener without holding its account lock during shutdown."""
        account_ref = _mask_cookie_id(cookie_id)
        lock = self._task_locks.setdefault(cookie_id, asyncio.Lock())
        cookie_info = await asyncio.to_thread(db_manager.get_cookie_details, cookie_id)
        original_user_id = user_id if user_id is not None else (
            cookie_info.get("user_id") if cookie_info else None
        )
        original_keywords = list(self.keywords.get(cookie_id, []))
        original_status = self.cookie_status.get(cookie_id, True)

        async with lock:
            generation = self._task_generations.get(cookie_id, 0) + 1
            self._task_generations[cookie_id] = generation
            old_task = self.tasks.pop(cookie_id, None)

        if old_task and old_task is not asyncio.current_task():
            logger.info(f"【{account_ref}】正在停止旧任务...")
            old_task.cancel()
            done, _ = await asyncio.wait({old_task}, timeout=shutdown_timeout)
            if old_task not in done:
                logger.warning(f"【{account_ref}】等待旧任务停止超时，继续安装最新监听")
                old_task.add_done_callback(self._consume_task_result)
            else:
                try:
                    old_task.result()
                except asyncio.CancelledError:
                    logger.debug(f"【{account_ref}】旧任务已取消")
                except Exception as exc:
                    logger.error(
                        f"【{account_ref}】等待旧任务清理时出错: "
                        f"error_type={type(exc).__name__}"
                    )

        async with lock:
            if self._task_generations.get(cookie_id) != generation:
                return {"status": "superseded", "cookie_id": cookie_id}

            if expected_cookie_revision is not None:
                latest_cookie_info = db_manager.get_cookie_details(cookie_id)
                if (
                    not latest_cookie_info
                    or int(latest_cookie_info.get("cookie_revision", -1))
                    != int(expected_cookie_revision)
                    or (
                        expected_cookie_value is not None
                        and str(latest_cookie_info.get("value") or "")
                        != str(expected_cookie_value)
                    )
                ):
                    return {"status": "superseded", "cookie_id": cookie_id}

            if save_to_db:
                saved = await asyncio.to_thread(
                    db_manager.save_cookie,
                    cookie_id,
                    new_value,
                    original_user_id,
                )
                if not saved:
                    return {"status": "rejected", "cookie_id": cookie_id}

            self.cookies[cookie_id] = new_value
            self.keywords[cookie_id] = original_keywords
            self.cookie_status[cookie_id] = original_status
            replacement = self.loop.create_task(
                self._run_xianyu(
                    cookie_id,
                    new_value,
                    original_user_id,
                    runtime_state=runtime_state,
                )
            )
            self.tasks[cookie_id] = replacement

        logger.info(
            f"【{account_ref}】已更新Cookie并重启任务 "
            f"owner_attached={original_user_id is not None}"
        )
        return {"status": "restarted", "cookie_id": cookie_id}

    @staticmethod
    def _consume_task_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            pass

    # 更新 Cookie 值
    def update_cookie(
        self,
        cookie_id: str,
        new_value: str,
        save_to_db: bool = True,
        runtime_state: dict = None,
    ):
        """替换指定账号的 Cookie 并重启任务

        Args:
            cookie_id: Cookie ID
            new_value: 新的Cookie值
            save_to_db: 是否保存到数据库（默认True）。当API层已经更新数据库时应设为False，避免覆盖其他字段
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop and current_loop == self.loop:
            return self.loop.create_task(
                self.replace_cookie(
                    cookie_id,
                    new_value,
                    save_to_db=save_to_db,
                    runtime_state=runtime_state,
                )
            )
        else:
            fut = asyncio.run_coroutine_threadsafe(
                self.replace_cookie(
                    cookie_id,
                    new_value,
                    save_to_db=save_to_db,
                    runtime_state=runtime_state,
                ),
                self.loop,
            )
            return fut.result()

    def update_keywords(self, cookie_id: str, kw_list: List[Tuple[str, str]]):
        """线程安全更新关键字"""
        self.keywords[cookie_id] = kw_list
        # 保存到数据库
        db_manager.save_keywords(cookie_id, kw_list)
        logger.info(f"更新关键字: {_mask_cookie_id(cookie_id)} -> {len(kw_list)} 条")

    # 查询接口
    def list_cookies(self):
        return list(self.cookies.keys())

    def get_keywords(self, cookie_id: str) -> List[Tuple[str, str]]:
        return self.keywords.get(cookie_id, [])

    def update_cookie_status(self, cookie_id: str, enabled: bool):
        """更新Cookie的启用/禁用状态"""
        if cookie_id not in self.cookies:
            raise ValueError(f"Cookie ID {cookie_id} 不存在")

        old_status = self.cookie_status.get(cookie_id, True)
        self.cookie_status[cookie_id] = enabled
        # 保存到数据库
        db_manager.save_cookie_status(cookie_id, enabled)
        logger.info(
            f"更新Cookie状态: {_mask_cookie_id(cookie_id)} -> "
            f"{'启用' if enabled else '禁用'}"
        )

        # 如果状态发生变化，需要启动或停止任务
        if old_status != enabled:
            if enabled:
                # 启用账号：启动任务
                self._start_cookie_task(cookie_id)
            else:
                # 禁用账号：停止任务
                self._stop_cookie_task(cookie_id)

    def get_cookie_status(self, cookie_id: str) -> bool:
        """获取Cookie的启用状态"""
        return self.cookie_status.get(cookie_id, True)  # 默认启用

    def get_enabled_cookies(self) -> Dict[str, str]:
        """获取所有启用的Cookie"""
        return {cid: value for cid, value in self.cookies.items()
                if self.cookie_status.get(cid, True)}

    def _start_cookie_task(self, cookie_id: str):
        """启动指定Cookie的任务"""
        account_ref = _mask_cookie_id(cookie_id)
        if cookie_id in self.tasks:
            logger.warning(f"Cookie任务已存在，跳过启动: {account_ref}")
            return

        cookie_value = self.cookies.get(cookie_id)
        if not cookie_value:
            logger.error(f"Cookie值不存在，无法启动任务: {account_ref}")
            return

        try:
            # 获取Cookie对应的user_id
            cookie_info = db_manager.get_cookie_details(cookie_id)
            user_id = cookie_info.get('user_id') if cookie_info else None

            # 使用异步方式启动任务
            if hasattr(self.loop, 'is_running') and self.loop.is_running():
                # 事件循环正在运行，使用run_coroutine_threadsafe
                fut = asyncio.run_coroutine_threadsafe(
                    self._add_cookie_async(cookie_id, cookie_value, user_id),
                    self.loop
                )
                fut.result(timeout=5)  # 等待最多5秒
            else:
                # 事件循环未运行，直接创建任务
                task = self.loop.create_task(self._run_xianyu(cookie_id, cookie_value, user_id))
                self.tasks[cookie_id] = task

            logger.info(f"成功启动Cookie任务: {account_ref}")
        except Exception as exc:
            logger.error(
                f"启动Cookie任务失败: {account_ref}, "
                f"error_type={type(exc).__name__}"
            )

    def _stop_cookie_task(self, cookie_id: str):
        """停止指定Cookie的任务"""
        account_ref = _mask_cookie_id(cookie_id)
        if cookie_id not in self.tasks:
            logger.warning(f"Cookie任务不存在，跳过停止: {account_ref}")
            return

        async def _stop_task_async():
            """异步停止任务并等待清理"""
            try:
                task = self.tasks[cookie_id]
                if not task.done():
                    task.cancel()
                    try:
                        # 等待任务完全清理，确保资源释放
                        await task
                    except asyncio.CancelledError:
                        # 任务被取消是预期行为
                        pass
                    except Exception as exc:
                        logger.error(
                            f"等待任务清理时出错: {account_ref}, "
                            f"error_type={type(exc).__name__}"
                        )
                    logger.info(f"已取消Cookie任务: {account_ref}")
                del self.tasks[cookie_id]
                logger.info(f"成功停止Cookie任务: {account_ref}")
            except Exception as exc:
                logger.error(
                    f"停止Cookie任务失败: {account_ref}, "
                    f"error_type={type(exc).__name__}"
                )

        try:
            # 在事件循环中执行异步停止
            if hasattr(self.loop, 'is_running') and self.loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(_stop_task_async(), self.loop)
                fut.result(timeout=10)  # 等待最多10秒
            else:
                logger.warning(f"事件循环未运行，无法正常等待任务清理: {account_ref}")
                # 直接取消任务（非最佳方案）
                task = self.tasks[cookie_id]
                if not task.done():
                    task.cancel()
                del self.tasks[cookie_id]
        except Exception as exc:
            logger.error(
                f"停止Cookie任务失败: {account_ref}, "
                f"error_type={type(exc).__name__}"
            )

    def update_auto_confirm_setting(self, cookie_id: str, auto_confirm: bool):
        """实时更新账号的自动确认发货设置"""
        account_ref = _mask_cookie_id(cookie_id)
        try:
            # 更新内存中的设置
            self.auto_confirm_settings[cookie_id] = auto_confirm
            logger.info(
                f"更新账号 {account_ref} 自动确认发货设置: "
                f"{'开启' if auto_confirm else '关闭'}"
            )

            # 如果账号正在运行，通知XianyuLive实例更新设置
            if cookie_id in self.tasks and not self.tasks[cookie_id].done():
                # 这里可以通过某种方式通知正在运行的XianyuLive实例
                # 由于XianyuLive会从数据库读取设置，所以数据库已经更新就足够了
                logger.info(f"账号 {account_ref} 正在运行，自动确认发货设置已实时生效")
        except Exception as exc:
            logger.error(
                f"更新自动确认发货设置失败: {account_ref}, "
                f"error_type={type(exc).__name__}"
            )

    def get_auto_confirm_setting(self, cookie_id: str) -> bool:
        """获取账号的自动确认发货设置"""
        return self.auto_confirm_settings.get(cookie_id, True)  # 默认开启


# 在 Start.py 中会把此变量赋值为具体实例
manager: Optional[CookieManager] = None
