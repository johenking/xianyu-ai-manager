import unittest

try:
    from utils.browser_interaction import (
        BrowserInteractionChannel,
        InteractionRateLimited,
        InteractionValidationError,
        StaleFrameRevision,
    )
except ImportError:
    BrowserInteractionChannel = None
    InteractionRateLimited = RuntimeError
    InteractionValidationError = RuntimeError
    StaleFrameRevision = RuntimeError


class _FakeMouse:
    def __init__(self):
        self.calls = []

    def move(self, x, y, **kwargs):
        self.calls.append(("move", x, y, kwargs))

    def down(self):
        self.calls.append(("down",))

    def up(self):
        self.calls.append(("up",))

    def wheel(self, delta_x, delta_y):
        self.calls.append(("wheel", delta_x, delta_y))


class _FakeKeyboard:
    def __init__(self):
        self.calls = []

    def insert_text(self, value):
        self.calls.append(("insert_text", value))

    def press(self, key):
        self.calls.append(("press", key))


class _FakePage:
    def __init__(self):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()

    def wait_for_timeout(self, duration):
        del duration


class BrowserInteractionChannelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            BrowserInteractionChannel,
            "BrowserInteractionChannel 尚未实现",
        )

    def test_executes_normalized_gesture_text_key_and_wheel_on_browser_thread(self):
        channel = BrowserInteractionChannel()
        revision = channel.publish_frame(
            b"synthetic-png",
            viewport_width=1000,
            viewport_height=500,
        )
        channel.submit({
            "kind": "gesture",
            "frame_revision": revision,
            "points": [
                {"x": 0.1, "y": 0.2},
                {"x": 0.7, "y": 0.2},
            ],
            "duration_ms": 240,
        })
        channel.submit({
            "kind": "text",
            "frame_revision": revision,
            "text": "482615",
        })
        channel.submit({
            "kind": "key",
            "frame_revision": revision,
            "key": "Enter",
        })
        channel.submit({
            "kind": "wheel",
            "frame_revision": revision,
            "delta_x": 0,
            "delta_y": 360,
        })

        page = _FakePage()
        executed = channel.drain(page)

        self.assertEqual(executed, 4)
        self.assertEqual(page.mouse.calls[0][:3], ("move", 100.0, 100.0))
        self.assertIn(("down",), page.mouse.calls)
        self.assertIn(("up",), page.mouse.calls)
        self.assertIn(("wheel", 0.0, 360.0), page.mouse.calls)
        self.assertEqual(page.keyboard.calls, [
            ("insert_text", "482615"),
            ("press", "Enter"),
        ])

    def test_rejects_stale_frames_unsafe_keys_and_oversized_text(self):
        channel = BrowserInteractionChannel()
        revision = channel.publish_frame(
            b"synthetic-png",
            viewport_width=1280,
            viewport_height=860,
        )

        with self.assertRaises(StaleFrameRevision):
            channel.submit({
                "kind": "key",
                "frame_revision": revision - 1,
                "key": "Enter",
            })
        with self.assertRaises(InteractionValidationError):
            channel.submit({
                "kind": "key",
                "frame_revision": revision,
                "key": "Meta+L",
            })
        with self.assertRaises(InteractionValidationError):
            channel.submit({
                "kind": "text",
                "frame_revision": revision,
                "text": "x" * 129,
            })

    def test_enforces_token_bucket_and_purges_private_state_on_close(self):
        now = [10.0]
        channel = BrowserInteractionChannel(
            rate_per_second=1,
            burst=2,
            clock=lambda: now[0],
        )
        revision = channel.publish_frame(
            b"synthetic-png",
            viewport_width=1280,
            viewport_height=860,
        )
        payload = {
            "kind": "key",
            "frame_revision": revision,
            "key": "Enter",
        }
        channel.submit(payload)
        channel.submit(payload)
        with self.assertRaises(InteractionRateLimited):
            channel.submit(payload)

        channel.close()

        self.assertIsNone(channel.latest_frame())
        self.assertEqual(channel.pending_count, 0)
        self.assertFalse(channel.snapshot()["interaction_supported"])

    def test_accepts_a_recent_frame_on_the_same_page_but_rejects_navigation(self):
        now = [10.0]
        channel = BrowserInteractionChannel(clock=lambda: now[0])
        first = channel.publish_frame(
            b"frame-one",
            viewport_width=1280,
            viewport_height=860,
            surface_key="https://passport.goofish.com/verify",
        )
        now[0] += 1
        channel.publish_frame(
            b"frame-two",
            viewport_width=1280,
            viewport_height=860,
            surface_key="https://passport.goofish.com/verify",
        )

        channel.submit({
            "kind": "key",
            "frame_revision": first,
            "key": "Enter",
        })

        now[0] += 1
        channel.publish_frame(
            b"frame-three",
            viewport_width=1280,
            viewport_height=860,
            surface_key="https://passport.goofish.com/face-check",
        )
        with self.assertRaises(StaleFrameRevision):
            channel.submit({
                "kind": "key",
                "frame_revision": first,
                "key": "Enter",
            })


if __name__ == "__main__":
    unittest.main()
