import asyncio
import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web

from app.services import cameras


class CameraPortTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.link, self.host, self.local = [self.root / name for name in ("selected", "host", "local")]
        for directory in (self.link, self.host, self.local):
            directory.mkdir()
        for name, value in (("CONFIG_LINK", self.link), ("HOST_CONFIG_DIR", self.host), ("AUTODARTS_DIR", self.local)):
            self.enterContext(patch.object(cameras.autodarts, name, value))
        self.status = self.enterContext(patch.object(cameras.autodarts, "get_status", return_value={"status": "running"}))

    def test_selected_configuration_and_loopback_only(self):
        (self.link / "config.toml").write_text('[api]\nport=3281\nhost="example.org"\n[host]\nport="3282"\n')
        self.assertEqual(cameras._api_base(), "http://127.0.0.1:3281")

    def test_host_config_and_explicit_separate_storage(self):
        (self.host / "config.toml").write_text('[host]\nport="3282"\n')
        (self.local / "config.toml").write_text('[host]\nport="3283"\n')
        with patch.dict("os.environ", {"OCHE_REUSE_AUTODARTS_CONFIG": "true"}):
            self.assertEqual(cameras._api_base(), "http://127.0.0.1:3282")
        with patch.dict("os.environ", {"OCHE_REUSE_AUTODARTS_CONFIG": "false"}):
            self.assertEqual(cameras._api_base(), "http://127.0.0.1:3283")

    def test_actual_v2_default(self):
        self.assertEqual(cameras._api_base(), "http://127.0.0.1:3180")

    def test_invalid_configuration_never_guesses_another_service(self):
        for content in ('[api]\nport="https://evil.test"', '[host]\nport=0', '[api]\nport=true', 'secret malformed toml'):
            with self.subTest(content=content):
                (self.link / "config.toml").write_text(content)
                with self.assertRaises(cameras.CameraError) as raised:
                    cameras._api_base()
                self.assertEqual(raised.exception.status_code, 503)
                self.assertNotIn(content, raised.exception.detail)

    def test_stopped_process_is_not_started(self):
        self.status.return_value = {"status": "stopped"}
        with patch.object(cameras.autodarts, "start") as start, self.assertRaises(cameras.CameraError) as raised:
            cameras._api_base()
        self.assertEqual(raised.exception.status_code, 503)
        start.assert_not_called()


class CameraServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = {
            "auth": {"api_key": "test-secret-do-not-return", "board_id": "private-board"},
            "cam": {"cams": ["/dev/video0", "/dev/video2", "/dev/video4"],
                    "width": 1280, "height": 720, "fps": 30, "fps_max": 30,
                    "auto_calibrate": True, "auto_distortion": False},
            "motion": {"threshold": 16},
        }
        # Populated capability shape is a synthetic fixture. Hardware discovery
        # cannot be verified on the development machine without attached cameras.
        self.devices = [{"card": f"USB Camera {index + 1}", "formats": [{
            "path": identifier, "resolutions": [
                {"width": 1280, "height": 720, "framerates": [30]},
                {"width": 640, "height": 480, "framerates": [15, 20, 30]},
            ]}]} for index, identifier in enumerate(self.config["cam"]["cams"])]
        self.stats = {"fps": [29.5, 30, 0], "resolution": {"width": 1280, "height": 720}}
        self.state = {"isOpened": True, "isRunning": True}
        self.requests = []
        self.patches = []
        self.overrides = {}
        self.patch_mode = "save"
        self.patch_delay = 0
        self.frame = b"\xff\xd8\xfffixture-jpeg"
        self.frame_type = "image/jpeg"
        self.frame_chunked = False
        self.frame_delay = 0
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(self.runner.cleanup)
        port = site._server.sockets[0].getsockname()[1]
        self.enterContext(patch.object(cameras, "_api_base", return_value=f"http://127.0.0.1:{port}"))
        self.enterContext(patch.object(cameras, "_settings_lock", asyncio.Lock()))
        self.enumerate = self.enterContext(patch.object(cameras, "enumerate_modes", return_value=[]))

    async def handle(self, request):
        self.requests.append((request.method, request.path))
        if request.path in self.overrides:
            status, content_type, body = self.overrides[request.path]
            return web.Response(status=status, content_type=content_type, body=body)
        if request.path == "/api/config":
            if request.method == "PATCH":
                body = await request.json()
                self.patches.append(body)
                await asyncio.sleep(self.patch_delay)
                if self.patch_mode == "save":
                    self.config["cam"].update(body["cam"])
                    self.config["cam"]["fps_max"] = self.config["cam"]["fps"]
                if self.patch_mode == "echo_only":
                    echoed = copy.deepcopy(self.config)
                    echoed["cam"].update(body["cam"])
                    return web.json_response(echoed)
            return web.json_response(self.config)
        if request.path == "/api/devices":
            return web.json_response(self.devices)
        if request.path == "/api/cams/stats":
            return web.json_response(self.stats)
        if request.path == "/api/cams/state":
            return web.json_response(self.state)
        if request.path.startswith("/api/img/cams/"):
            await asyncio.sleep(self.frame_delay)
            if self.frame_chunked:
                response = web.StreamResponse(headers={"Content-Type": self.frame_type})
                await response.prepare(request)
                await response.write(self.frame)
                await response.write_eof()
                return response
            return web.Response(body=self.frame, content_type=self.frame_type)
        return web.Response(status=404)

    async def payload(self, **changes):
        data = await cameras.get_data()
        return dict(data["settings"], revision=data["revision"], **changes)

    async def test_data_sanitizes_credentials_and_preserves_zero_fps(self):
        data = await cameras.get_data()
        self.assertNotIn("test-secret", json.dumps(data))
        self.assertNotIn("private-board", json.dumps(data))
        self.assertNotIn("auth", data)
        self.assertEqual(data["cameras"][0]["fps"], 29.5)
        self.assertEqual(data["cameras"][2]["fps"], 0)
        self.assertTrue(data["running"])
        self.assertTrue(data["capturing"])
        self.assertEqual(data["capture_resolution"], {"width": 1280, "height": 720})
        self.enumerate.assert_not_called()

    async def test_actual_capture_dimensions_are_distinct_from_requested_settings(self):
        self.stats["resolution"] = {"width": 640, "height": 480, "private": "test-secret"}
        data = await cameras.get_data()
        self.assertEqual(data["capture_resolution"], {"width": 640, "height": 480})
        self.assertEqual(data["settings"]["width"], 1280)
        self.stats["resolution"] = {"width": True, "height": "720"}
        self.assertIsNone((await cameras.get_data())["capture_resolution"])

    async def test_empty_cameras_do_not_mean_service_offline(self):
        self.devices = []
        self.config["cam"]["cams"] = ["", "", ""]
        self.stats["fps"] = [0]
        self.state = {"isOpened": False, "isRunning": False}
        data = await cameras.get_data()
        self.assertTrue(data["running"])
        self.assertFalse(data["capturing"])
        self.assertFalse(data["opened"])
        self.assertIsNone(data["cameras"][1]["fps"])
        self.assertTrue(data["warnings"])

    async def test_save_sends_camera_subset_and_preserves_other_settings(self):
        before = copy.deepcopy(self.config)
        payload = await self.payload()
        payload.update(width=640, height=480, fps=20)
        data = await cameras.apply_settings(payload)
        self.assertEqual(self.patches, [{"cam": {"cams": before["cam"]["cams"], "width": 640, "height": 480, "fps": 20}}])
        self.assertEqual(self.config["auth"], before["auth"])
        self.assertEqual(self.config["motion"], before["motion"])
        self.assertEqual(self.config["cam"]["auto_distortion"], before["cam"]["auto_distortion"])
        self.assertEqual(data["settings"]["fps"], 20)
        self.assertNotEqual(data["revision"], payload["revision"])
        self.assertNotIn("test-secret", json.dumps(data))

    async def test_conflict_refuses_to_overwrite_newer_settings(self):
        payload = await self.payload()
        self.config["cam"]["fps"] = 20
        with self.assertRaises(cameras.CameraError) as raised:
            await cameras.apply_settings(payload)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.patches, [])

    async def test_parallel_saves_are_serialized_with_revision_check(self):
        payload = await self.payload()
        payload.update(width=640, height=480, fps=20)
        self.patch_delay = .03
        results = await asyncio.gather(cameras.apply_settings(payload), cameras.apply_settings(payload), return_exceptions=True)
        self.assertEqual(len(self.patches), 1)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        failures = [result for result in results if isinstance(result, cameras.CameraError)]
        self.assertEqual([result.status_code for result in failures], [409])

    async def test_false_success_is_detected_from_echo_and_readback(self):
        for mode in ("ignore", "echo_only"):
            with self.subTest(mode=mode):
                self.patch_mode = mode
                payload = await self.payload()
                payload.update(width=640, height=480, fps=20)
                with self.assertRaises(cameras.CameraError) as raised:
                    await cameras.apply_settings(payload)
                self.assertEqual(raised.exception.status_code, 409)

    async def test_unknown_device_and_unsupported_common_mode_never_patch(self):
        for changes in ({"devices": ["/dev/video0", "/dev/video2", "http://evil.test/video"]},
                        {"devices": ["/dev/video0"] * 3}, {"fps": 25},
                        {"width": 800, "height": 600}, {"fps": True}, {"fps": 29.97}, {"width": 640.0}):
            with self.subTest(changes=changes):
                payload = await self.payload()
                payload.update(changes)
                with self.assertRaises(cameras.CameraError) as raised:
                    await cameras.apply_settings(payload)
                self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.patches, [])

    async def test_same_physical_camera_cannot_fill_two_slots(self):
        first = self.devices[0]["formats"][0]
        first["path"] = "native=/dev/video0&vid=test"
        second = self.devices[1]["formats"][0]
        second["path"] = "native=/dev/video0&vid=alternate"
        payload = await self.payload()
        payload["devices"] = [first["path"], second["path"], "/dev/video4"]
        with self.assertRaises(cameras.CameraError) as raised:
            await cameras.apply_settings(payload)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.patches, [])

    async def test_two_video_nodes_on_same_upstream_camera_cannot_fill_two_slots(self):
        self.devices = [
            {"card": "Multi-node camera", "formats": self.devices[0]["formats"] + self.devices[1]["formats"]},
            self.devices[2],
        ]
        payload = await self.payload()
        with self.assertRaises(cameras.CameraError) as raised:
            await cameras.apply_settings(payload)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("physical", raised.exception.detail)
        self.assertEqual(self.patches, [])

    async def test_unknown_rates_are_not_invented(self):
        self.devices[0]["formats"][0]["resolutions"] = [{"width": 1280, "height": 720, "framerates": [{"num": 1, "den": 30}]}]
        data = await cameras.get_data()
        self.assertEqual(data["devices"][0]["modes"][0]["fps"], [])
        self.assertTrue(data["warnings"])
        with self.assertRaises(cameras.CameraError) as raised:
            await cameras.apply_settings(dict(data["settings"], revision=data["revision"]))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.patches, [])

    async def test_local_readonly_capabilities_fill_missing_rates(self):
        identifier = "native=/dev/video0&vid=test&location=1-2"
        self.devices[0]["formats"][0].update(path=identifier, resolutions=[{"width": 1280, "height": 720}])
        self.enumerate.return_value = [{"width": 1280, "height": 720, "fps": [30]}]
        data = await cameras.get_data()
        self.assertEqual(data["devices"][0]["id"], identifier)
        self.assertEqual(data["devices"][0]["modes"][0]["fps"], [30])
        self.enumerate.assert_called_once_with("/dev/video0")

    async def test_fallback_does_not_add_sizes_outside_api_capabilities(self):
        self.devices[0]["formats"][0]["resolutions"] = [{"width": 1280, "height": 720}]
        self.enumerate.return_value = [{"width": 1280, "height": 720, "fps": [30]},
                                       {"width": 3840, "height": 2160, "fps": [30]}]
        data = await cameras.get_data()
        self.assertEqual(data["devices"][0]["modes"], [{"width": 1280, "height": 720, "fps": [30]}])

    async def test_fallback_never_opens_remote_or_arbitrary_paths(self):
        self.devices = [{"card": "Camera", "formats": [{"path": path, "resolutions": []}]} for path in
                        ("/etc/passwd", "native=/etc/passwd", "https://example.com/dev/video0", "native=/dev/video0/../../etc/passwd")]
        await cameras.get_data()
        self.enumerate.assert_not_called()

    async def test_malformed_upstream_data_has_safe_error(self):
        for body, content_type in ((b'{"secret":"test-secret"', "application/json"),
                                   (b"test-secret", "text/html"),
                                   (b'{"auth":{"api_key":"test-secret"}}', "application/json")):
            with self.subTest(body=body):
                self.overrides["/api/config"] = (200, content_type, body)
                with self.assertRaises(cameras.CameraError) as raised:
                    await cameras.get_data()
                self.assertEqual(raised.exception.status_code, 502)
                self.assertNotIn("test-secret", str(raised.exception))

    async def test_unavailable_api_has_safe_error(self):
        self.overrides["/api/config"] = (500, "text/plain", b"test-secret")
        with self.assertRaises(cameras.CameraError) as raised:
            await cameras.get_data()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("test-secret", str(raised.exception))

    async def test_save_timeout_requires_reload_instead_of_claiming_failure(self):
        payload = await self.payload()
        payload.update(width=640, height=480, fps=20)
        self.patch_delay = .1
        with patch.object(cameras, "REQUEST_TIMEOUT", aiohttp.ClientTimeout(total=.04)), self.assertRaises(cameras.CameraError) as raised:
            await cameras.apply_settings(payload)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("Reload", raised.exception.detail)
        self.assertEqual(len(self.patches), 1)

    async def test_json_body_limit(self):
        with patch.object(cameras, "MAX_JSON_BYTES", 20), self.assertRaises(cameras.CameraError) as raised:
            await cameras.get_data()
        self.assertEqual(raised.exception.status_code, 502)

    async def test_frame_is_read_without_capture_commands(self):
        data, content_type = await cameras.get_frame(1)
        self.assertEqual((data, content_type), (self.frame, "image/jpeg"))
        self.assertEqual(self.requests, [("GET", "/api/img/cams/1")])

    async def test_frame_index_rejects_arbitrary_paths(self):
        for index in (-1, 3, True, "0", "../../config"):
            with self.subTest(index=index), self.assertRaises(cameras.CameraError) as raised:
                await cameras.get_frame(index)
            self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.requests, [])

    async def test_frame_limits_both_fixed_and_chunked_responses(self):
        for chunked in (False, True):
            self.frame_chunked = chunked
            with self.subTest(chunked=chunked), patch.object(cameras, "MAX_FRAME_BYTES", 5), self.assertRaises(cameras.CameraError) as raised:
                await cameras.get_frame(0)
            self.assertEqual(raised.exception.status_code, 502)

    async def test_frame_mime_and_magic_are_checked(self):
        for content_type, body in (("text/html", b"test-secret"), ("image/jpeg", b"test-secret"), ("image/png", self.frame)):
            self.frame_type, self.frame = content_type, body
            with self.subTest(content_type=content_type), self.assertRaises(cameras.CameraError) as raised:
                await cameras.get_frame(0)
            self.assertEqual(raised.exception.status_code, 502)
            self.assertNotIn("test-secret", str(raised.exception))

    async def test_frame_redirect_is_not_followed(self):
        self.overrides["/api/img/cams/0"] = (302, "text/plain", b"test-secret")
        with self.assertRaises(cameras.CameraError) as raised:
            await cameras.get_frame(0)
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(self.requests, [("GET", "/api/img/cams/0")])

    async def test_no_frame_and_timeout_are_distinguished(self):
        self.overrides["/api/img/cams/0"] = (404, "text/plain", b"")
        with self.assertRaises(cameras.CameraError) as raised:
            await cameras.get_frame(0)
        self.assertEqual(raised.exception.status_code, 404)
        self.overrides.clear()
        self.frame_delay = .08
        with patch.object(cameras, "REQUEST_TIMEOUT", aiohttp.ClientTimeout(total=.01)), self.assertRaises(cameras.CameraError) as raised:
            await cameras.get_frame(0)
        self.assertEqual(raised.exception.status_code, 503)


class CameraCapabilityParsingTests(unittest.TestCase):
    def test_untrusted_numeric_capabilities_are_bounded(self):
        raw = [{"width": 1280, "height": 720, "framerates": [30, 30.0, True, -1, 0, float("nan"), float("inf"), 10 ** 500, {}, "60"]}]
        self.assertEqual(cameras._modes(raw), [{"width": 1280, "height": 720, "fps": [30]}])

    def test_interval_quantization_is_normalized_without_rounding_real_fractional_fps(self):
        raw = [{"width": 1280, "height": 720, "framerates": [30.00003, 29.99997, 29.97, 59.94]}]
        self.assertEqual(cameras._modes(raw), [{"width": 1280, "height": 720, "fps": [30]}])

    def test_explicit_local_uri_paths(self):
        self.assertEqual(cameras._native_path("v4l2://?location=/dev/video0"), "/dev/video0")
        self.assertEqual(cameras._native_path("native=/dev/video2&serial=example"), "/dev/video2")
        self.assertIsNone(cameras._native_path("native=/dev/video0&native=/dev/video2"))

    def test_revision_treats_integer_and_float_fps_as_same(self):
        settings = {"devices": ["a", "b", "c"], "width": 1280, "height": 720, "fps": 30}
        self.assertEqual(cameras._revision(settings), cameras._revision(dict(settings, fps=30.0)))
