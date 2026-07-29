"""Regression tests for bounded image I/O and managed-path enforcement."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from starlette.datastructures import Headers, UploadFile

import reply_server
import utils.image_uploader as image_uploader_module
from utils.image_uploader import ImageUploader
from utils.image_utils import ImageManager


def _upload(filename: str, body: bytes, content_type: str = "image/png") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(body),
        headers=Headers({"content-type": content_type}),
    )


class BoundedImageUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_limited_reader_stops_at_first_over_limit_byte(self):
        upload = _upload("large.png", b"x" * 100)

        with patch.object(reply_server, "UPLOAD_READ_CHUNK_BYTES", 4):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server._read_upload_with_limit(
                    upload,
                    max_bytes=8,
                    label="图片文件",
                )

        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(upload.file.tell(), 9)

    async def test_generic_upload_rejects_oversize_before_image_decode(self):
        upload = _upload("large.png", b"x" * 9)

        with (
            patch.object(reply_server, "IMAGE_UPLOAD_MAX_BYTES", 8),
            patch.object(reply_server.image_manager, "save_image") as save_image,
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.upload_image(
                    upload,
                    current_user={"user_id": 7, "username": "operator"},
                )

        self.assertEqual(raised.exception.status_code, 413)
        save_image.assert_not_called()

    async def test_foreign_card_is_rejected_before_file_body_read(self):
        upload = Mock()
        upload.filename = "secret.png"
        upload.content_type = "image/png"
        upload.read = AsyncMock()
        database = Mock()
        database.get_card_by_id.return_value = None

        with patch("db_manager.db_manager", database):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.update_card_with_image(
                    99,
                    upload,
                    name="foreign",
                    type="image",
                    current_user={"user_id": 7, "username": "operator"},
                )

        self.assertEqual(raised.exception.status_code, 404)
        database.get_card_by_id.assert_called_once_with(99, 7)
        upload.read.assert_not_awaited()

    async def test_foreign_keyword_account_is_rejected_before_file_body_read(self):
        upload = Mock()
        upload.filename = "secret.png"
        upload.content_type = "image/png"
        upload.read = AsyncMock()
        database = Mock()
        database.get_cookie_details.return_value = {"user_id": 8}

        with (
            patch.object(reply_server, "db_manager", database),
            patch.object(reply_server.cookie_manager, "manager", Mock()),
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.add_image_keyword(
                    "foreign-account",
                    "hello",
                    "",
                    upload,
                    current_user={"user_id": 7, "username": "operator"},
                )

        self.assertEqual(raised.exception.status_code, 404)
        upload.read.assert_not_awaited()

    async def test_owned_keyword_upload_is_size_limited(self):
        upload = _upload("large.png", b"x" * 9)
        database = Mock()
        database.get_cookie_details.return_value = {"user_id": 7}

        with (
            patch.object(reply_server, "db_manager", database),
            patch.object(reply_server.cookie_manager, "manager", Mock()),
            patch.object(reply_server, "IMAGE_UPLOAD_MAX_BYTES", 8),
            patch.object(reply_server.image_manager, "save_image") as save_image,
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.add_image_keyword(
                    "owned-account",
                    "hello",
                    "",
                    upload,
                    current_user={"user_id": 7, "username": "operator"},
                )

        self.assertEqual(raised.exception.status_code, 413)
        save_image.assert_not_called()

    async def test_owned_card_upload_is_size_limited(self):
        upload = _upload("large.png", b"x" * 9)
        database = Mock()
        database.get_card_by_id.return_value = {"id": 1, "user_id": 7}

        with (
            patch("db_manager.db_manager", database),
            patch.object(reply_server, "IMAGE_UPLOAD_MAX_BYTES", 8),
            patch.object(reply_server.image_manager, "save_image") as save_image,
        ):
            with self.assertRaises(reply_server.HTTPException) as raised:
                await reply_server.update_card_with_image(
                    1,
                    upload,
                    name="owned",
                    type="image",
                    current_user={"user_id": 7, "username": "operator"},
                )

        self.assertEqual(raised.exception.status_code, 413)
        save_image.assert_not_called()
        database.update_card.assert_not_called()


class ManagedImagePathTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.upload_root = self.root / "static" / "uploads" / "images"
        self.manager = ImageManager(str(self.upload_root))
        self.image = self.upload_root / "owned.png"
        self.image.write_bytes(b"synthetic")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_resolver_allows_regular_file_under_managed_root(self):
        with patch("os.getcwd", return_value=str(self.root)):
            resolved = self.manager.resolve_image_path(
                "static/uploads/images/owned.png"
            )

        self.assertEqual(resolved, self.image.resolve())

    def test_resolver_rejects_traversal_and_symlink(self):
        outside = self.root / "private.txt"
        outside.write_text("private", encoding="utf-8")
        link = self.upload_root / "linked.png"
        link.symlink_to(outside)

        with patch("os.getcwd", return_value=str(self.root)):
            traversal = self.manager.resolve_image_path(
                "static/uploads/images/../../../private.txt"
            )
            symlink = self.manager.resolve_image_path(
                "static/uploads/images/linked.png"
            )

        self.assertIsNone(traversal)
        self.assertIsNone(symlink)

    def test_delete_rejects_path_outside_managed_root(self):
        outside = self.root / "private.txt"
        outside.write_text("private", encoding="utf-8")

        with patch("os.getcwd", return_value=str(self.root)):
            deleted = self.manager.delete_image(
                "static/uploads/images/../../../private.txt"
            )

        self.assertFalse(deleted)
        self.assertTrue(outside.exists())


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, status, chunks=(), headers=None):
        self.status = status
        self.content = _FakeContent(chunks)
        self.headers = dict(headers or {})
        self.charset = "utf-8"


class _ResponseContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return None


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def post(self, *_args, **kwargs):
        self.kwargs = kwargs
        return _ResponseContext(self.response)


class ImageUploaderNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def _run_upload(self, response):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        source = Path(tempdir.name) / "source.jpg"
        compressed = Path(tempdir.name) / "compressed.jpg"
        source.write_bytes(b"source")
        compressed.write_bytes(b"compressed")
        session = _FakeSession(response)
        uploader = ImageUploader("redacted")
        uploader.session = session
        with (
            patch.object(
                image_uploader_module.image_manager,
                "resolve_image_path",
                return_value=source,
            ),
            patch.object(
                uploader,
                "_compress_image",
                return_value=str(compressed),
            ),
        ):
            result = await uploader.upload_image(str(source))
        return result, session

    async def test_upload_does_not_follow_redirects(self):
        result, session = await self._run_upload(
            _FakeResponse(302, headers={"Location": "http://127.0.0.1/private"})
        )

        self.assertIsNone(result)
        self.assertIs(session.kwargs["allow_redirects"], False)

    async def test_upload_response_stream_has_hard_byte_limit(self):
        with patch.object(
            image_uploader_module,
            "IMAGE_UPLOAD_RESPONSE_MAX_BYTES",
            8,
        ):
            result, _session = await self._run_upload(
                _FakeResponse(200, chunks=(b"1234", b"56789"))
            )

        self.assertIsNone(result)

    async def test_small_json_response_remains_compatible(self):
        result, session = await self._run_upload(
            _FakeResponse(
                200,
                chunks=(b'{"data":{"url":"https://gw.alicdn.com/image.jpg"}}',),
            )
        )

        self.assertEqual(result, "https://gw.alicdn.com/image.jpg")
        self.assertIs(session.kwargs["allow_redirects"], False)

    async def test_upload_rejects_unmanaged_path_before_opening(self):
        uploader = ImageUploader("redacted")
        with (
            patch.object(
                image_uploader_module.image_manager,
                "resolve_image_path",
                return_value=None,
            ),
            patch.object(uploader, "_compress_image") as compress,
        ):
            result = await uploader.upload_image("/etc/passwd")

        self.assertIsNone(result)
        compress.assert_not_called()


if __name__ == "__main__":
    unittest.main()
