import asyncio
import unittest

from official_login_sessions import OfficialLoginSessionCoordinator
from utils.xianyu_official_login import OfficialLoginResult


class FakeRegistry:
    def __init__(self):
        self.records = {}

    def register(self, session_id, session_type, owner_user_id, **kwargs):
        self.records[session_id] = {
            "session_id": session_id,
            "session_type": session_type,
            "owner_user_id": owner_user_id,
            "status": kwargs.get("status", "created"),
        }

    def update(self, session_id, **kwargs):
        self.records[session_id].update(kwargs)


class FakeWorker:
    def __init__(self):
        self.cancelled = False
        self.visible_requested = False

    def close_browser(self):
        self.cancelled = True

    def request_visible(self):
        self.visible_requested = True


class SuccessfulQrService:
    def login_with_qr(self, **kwargs):
        kwargs["on_status"](
            OfficialLoginResult(
                status="waiting_user",
                message="请使用闲鱼 App 扫码",
                verification_image_path="static/uploads/images/login.png",
                requires_manual_action=True,
            )
        )
        return OfficialLoginResult(
            status="success",
            cookies={"unb": "stable-unb", "cookie2": "session"},
            unb="stable-unb",
        )


class BlockingQrService:
    def __init__(self, gate):
        self.gate = gate

    def login_with_qr(self, **kwargs):
        kwargs["on_status"](
            OfficialLoginResult(status="waiting_user", message="等待扫码")
        )
        self.gate.wait(1)
        if kwargs["worker"].cancelled:
            return OfficialLoginResult(status="cancelled", error_code="cancelled")
        return OfficialLoginResult(status="failed", error_code="timeout")


class SensitiveFailureService:
    def login_with_qr(self, **kwargs):
        del kwargs
        raise RuntimeError(
            "cookie2=COOKIE_SECRET token=TOKEN_SECRET password=PASSWORD_SECRET "
            "https://passport.goofish.com/verify?id=VERIFY_SECRET"
        )


class ValidatedHandoffService:
    def __init__(self, events):
        self.events = events

    def login_with_qr(self, **kwargs):
        result = OfficialLoginResult(
            status="success",
            cookies={"unb": "stable-unb", "cookie2": "session"},
            unb="stable-unb",
            access_token="synthetic-token",
            browser_user_agent="Mozilla/5.0 Synthetic Chrome/150.0.0.0",
        )
        kwargs["on_validated"](result)
        self.events.append("browser_returned")
        return result


class OfficialLoginSessionCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_cookie_and_listener_handoff_precedes_browser_return(self):
        events = []

        async def complete(_record, _result, _account, _password):
            events.append("handoff_completed")
            return {"account_id": "stable-unb", "is_new_account": False}

        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=complete,
            service_factory=lambda: ValidatedHandoffService(events),
            worker_factory=FakeWorker,
            registry=FakeRegistry(),
        )

        created = await coordinator.start(owner_user_id=7, mode="qr")
        terminal = await coordinator.wait_for_terminal(
            created["session_id"],
            7,
            timeout=1,
        )

        self.assertEqual(terminal["state"], "success")
        self.assertEqual(events, ["handoff_completed", "browser_returned"])

    async def test_status_polling_is_read_only_and_completion_runs_once(self):
        registry = FakeRegistry()
        completion_calls = []

        async def complete(record, result, account, password):
            completion_calls.append((record.session_id, result.unb, account, password))
            return {"account_id": "existing-row", "is_new_account": False}

        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=complete,
            service_factory=SuccessfulQrService,
            worker_factory=FakeWorker,
            registry=registry,
        )
        created = await coordinator.start(owner_user_id=7, mode="qr")
        session_id = created["session_id"]
        terminal = await coordinator.wait_for_terminal(session_id, 7, timeout=1)

        statuses = await asyncio.gather(
            *(coordinator.get_status(session_id, 7) for _ in range(10))
        )

        self.assertEqual(terminal["state"], "success")
        self.assertTrue(all(item["state"] == "success" for item in statuses))
        self.assertEqual(len(completion_calls), 1)
        self.assertEqual(statuses[0]["account_id"], "existing-row")
        self.assertNotIn("password", statuses[0])
        self.assertNotIn("cookies", statuses[0])
        self.assertNotIn("password", registry.records[session_id])

    async def test_owner_isolation_show_browser_and_cancel(self):
        import threading

        gate = threading.Event()
        worker = FakeWorker()
        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=lambda *args: None,
            service_factory=lambda: BlockingQrService(gate),
            worker_factory=lambda: worker,
            registry=FakeRegistry(),
        )
        created = await coordinator.start(owner_user_id=7, mode="qr")
        session_id = created["session_id"]
        await coordinator.wait_until_ready(session_id, 7, timeout=1)

        self.assertIsNone(await coordinator.get_status(session_id, 8))
        self.assertTrue(await coordinator.show_browser(session_id, 7))
        self.assertTrue(worker.visible_requested)
        self.assertTrue(await coordinator.cancel(session_id, 7))
        gate.set()
        terminal = await coordinator.wait_for_terminal(session_id, 7, timeout=1)
        self.assertEqual(terminal["state"], "cancelled")

    async def test_active_status_polling_has_no_lifecycle_side_effects(self):
        import threading

        gate = threading.Event()
        worker = FakeWorker()
        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=lambda *args: None,
            service_factory=lambda: BlockingQrService(gate),
            worker_factory=lambda: worker,
            registry=FakeRegistry(),
        )
        created = await coordinator.start(owner_user_id=7, mode="qr")
        session_id = created["session_id"]
        await coordinator.wait_until_ready(session_id, 7, timeout=1)

        statuses = await asyncio.gather(
            *(coordinator.get_status(session_id, 7) for _ in range(20))
        )

        self.assertTrue(all(item["state"] == "waiting_user" for item in statuses))
        self.assertFalse(worker.cancelled)
        await coordinator.cancel(session_id, 7)
        gate.set()

    async def test_same_owner_and_mode_reuses_one_active_session(self):
        import threading

        gate = threading.Event()
        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=lambda *args: None,
            service_factory=lambda: BlockingQrService(gate),
            worker_factory=FakeWorker,
            registry=FakeRegistry(),
        )

        first, second = await asyncio.gather(
            coordinator.start(owner_user_id=7, mode="qr"),
            coordinator.start(owner_user_id=7, mode="qr"),
        )

        self.assertEqual(first["session_id"], second["session_id"])
        await coordinator.cancel(first["session_id"], 7)
        gate.set()

    async def test_expired_session_stops_worker_and_reports_expired(self):
        import threading

        gate = threading.Event()
        worker = FakeWorker()
        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=lambda *args: None,
            service_factory=lambda: BlockingQrService(gate),
            worker_factory=lambda: worker,
            registry=FakeRegistry(),
            session_ttl_seconds=0.03,
        )
        created = await coordinator.start(owner_user_id=7, mode="qr")
        await asyncio.sleep(0.05)

        status = await coordinator.get_status(created["session_id"], 7)

        self.assertEqual(status["state"], "expired")
        self.assertTrue(worker.cancelled)
        gate.set()

    async def test_sensitive_exception_material_is_removed_from_state_and_registry(self):
        registry = FakeRegistry()
        coordinator = OfficialLoginSessionCoordinator(
            completion_handler=lambda *args: None,
            service_factory=SensitiveFailureService,
            worker_factory=FakeWorker,
            registry=registry,
        )
        created = await coordinator.start(owner_user_id=7, mode="qr")
        status = await coordinator.wait_for_terminal(created["session_id"], 7, timeout=1)
        persisted = registry.records[created["session_id"]]
        combined = f"{status} {persisted}"

        for secret in (
            "COOKIE_SECRET",
            "TOKEN_SECRET",
            "PASSWORD_SECRET",
            "VERIFY_SECRET",
            "passport.goofish.com",
        ):
            self.assertNotIn(secret, combined)
