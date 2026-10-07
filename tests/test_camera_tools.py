import errno
import struct
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import camera_modes, cameras


class CameraModesTests(unittest.TestCase):
    @staticmethod
    def _format_fixture(capabilities):
        """Map pixel format -> resolution -> advertised (numerator, denominator)."""
        def ioctl(fd, command, buffer):
            index = struct.unpack_from('I', buffer)[0]
            if command == camera_modes.ENUM_FMT:
                capture_type = struct.unpack_from('I', buffer, 4)[0]
                if capture_type != 1 or index >= len(capabilities):
                    raise OSError(errno.EINVAL, 'end')
                pixel_format = list(capabilities)[index]
                struct.pack_into('I', buffer, 44, pixel_format)
                return
            pixel_format = struct.unpack_from('I', buffer, 4)[0]
            if command == camera_modes.ENUM_SIZES:
                if index >= len(capabilities[pixel_format]):
                    raise OSError(errno.EINVAL, 'end')
                width, height = list(capabilities[pixel_format])[index]
                struct.pack_into('3I', buffer, 8, 1, width, height)
                return
            if command == camera_modes.ENUM_INTERVALS:
                size = struct.unpack_from('2I', buffer, 8)
                intervals = capabilities[pixel_format][size]
                if index >= len(intervals):
                    raise OSError(errno.EINVAL, 'end')
                numerator, denominator = intervals[index]
                struct.pack_into('3I', buffer, 16, 1, numerator, denominator)
                return
            raise AssertionError('Unexpected configuration/capture ioctl')
        return ioctl

    def test_unknown_pixel_format_uses_only_rates_shared_for_each_size(self):
        mjpg = int.from_bytes(b'MJPG', 'little')
        yuyv = int.from_bytes(b'YUYV', 'little')
        ioctl = self._format_fixture({
            mjpg: {(1280, 720): [(1, 15), (1, 30)], (1920, 1080): [(1, 30)]},
            yuyv: {(1280, 720): [(1, 15)]},
        })
        self.assertEqual(camera_modes._read_modes(7, ioctl), [
            {'width': 1280, 'height': 720, 'fps': [15]},
            {'width': 1920, 'height': 1080, 'fps': [30]},
        ])

    def test_unknown_intervals_in_one_format_do_not_borrow_another_formats_rates(self):
        ioctl = self._format_fixture({
            1: {(1280, 720): [(1, 30)]},
            2: {(1280, 720): []},
        })
        self.assertEqual(camera_modes._read_modes(7, ioctl), [
            {'width': 1280, 'height': 720, 'fps': []},
        ])

    def test_quantized_interval_matches_same_whole_fps_in_another_format(self):
        ioctl = self._format_fixture({
            1: {(1280, 720): [(333333, 10000000)]},
            2: {(1280, 720): [(1, 30)]},
        })
        self.assertEqual(camera_modes._read_modes(7, ioctl), [
            {'width': 1280, 'height': 720, 'fps': [30]},
        ])

    def test_discrete_modes_keep_rates_matched_to_each_resolution(self):
        calls = []

        def ioctl(fd, command, buffer):
            calls.append(command)
            index = struct.unpack_from('I', buffer)[0]
            if command == camera_modes.ENUM_FMT:
                capture_type = struct.unpack_from('I', buffer, 4)[0]
                if index or capture_type != 1:
                    raise OSError(errno.EINVAL, 'end')
                struct.pack_into('I', buffer, 44, int.from_bytes(b'MJPG', 'little'))
            elif command == camera_modes.ENUM_SIZES:
                if index >= 2:
                    raise OSError(errno.EINVAL, 'end')
                width, height = [(640, 480), (1920, 1080)][index]
                struct.pack_into('3I', buffer, 8, 1, width, height)
            elif command == camera_modes.ENUM_INTERVALS:
                if index:
                    raise OSError(errno.EINVAL, 'end')
                width = struct.unpack_from('I', buffer, 8)[0]
                struct.pack_into('3I', buffer, 16, 1, 1, 30 if width == 640 else 15)
            else:
                self.fail('Capture or configuration ioctl must never be issued.')

        self.assertEqual(camera_modes._read_modes(7, ioctl), [
            {'width': 640, 'height': 480, 'fps': [30]},
            {'width': 1920, 'height': 1080, 'fps': [15]},
        ])
        self.assertTrue(calls)

    def test_range_candidates_respect_reported_steps(self):
        buffer = bytearray(44)
        struct.pack_into('7I', buffer, 8, 3, 640, 1920, 640, 480, 1080, 120)
        sizes = camera_modes._frame_sizes(buffer)
        self.assertIn((1280, 720), sizes)
        self.assertNotIn((800, 600), sizes)
        self.assertTrue(all((w - 640) % 640 == 0 and (h - 480) % 120 == 0 for w, h in sizes))
        intervals = bytearray(52)
        struct.pack_into('7I', intervals, 16, 3, 1, 30, 1, 10, 1, 30)
        self.assertEqual(set(camera_modes._interval_rates(intervals)), {10, 15, 30})

    def test_continuous_interval_range_keeps_unusual_whole_fps(self):
        intervals = bytearray(52)
        struct.pack_into('7I', intervals, 16, 2, 51, 1000, 61, 1000, 0, 0)
        rates = cameras._rates(camera_modes._interval_rates(intervals))
        self.assertEqual(rates, [17, 18, 19])

    def test_unreadable_capabilities_are_unknown(self):
        def denied(*args):
            raise PermissionError('not exposed')
        self.assertEqual(camera_modes._read_modes(7, denied), [])
        self.assertEqual(camera_modes.enumerate_modes('/etc/passwd'), [])
        self.assertEqual(camera_modes.enumerate_modes('http://remote/camera'), [])


class CameraRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.payload = {'revision': 'test', 'devices': ['cam-a', 'cam-b', 'cam-c'],
                        'width': 1280, 'height': 720, 'fps': 30}

    def test_settings_only_accept_camera_fields_and_same_origin(self):
        with patch.object(cameras, 'apply_settings', new_callable=AsyncMock, return_value={'saved': True}) as save:
            response = self.client.patch('/autodarts/cameras/settings', json=self.payload,
                                         headers={'origin': 'http://testserver'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(save.await_count, 1)
            for extra in ({'url': 'http://remote'}, {'auth': {'token': 'fake'}}, {'fps': True}, {'fps': 29.97},
                          {'devices': ['cam-a']}, {'width': 0}, {'width': '1280'}):
                with self.subTest(extra=extra):
                    response = self.client.patch('/autodarts/cameras/settings', json={**self.payload, **extra})
                    self.assertEqual(response.status_code, 422)
            self.assertEqual(save.await_count, 1)
            response = self.client.patch('/autodarts/cameras/settings', json=self.payload,
                                         headers={'origin': 'https://elsewhere.test'})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(save.await_count, 1)

    def test_frame_is_non_cached_and_indices_are_bounded(self):
        with patch.object(cameras, 'get_frame', new_callable=AsyncMock,
                          return_value=(b'jpeg', 'image/jpeg')) as frame:
            response = self.client.get('/autodarts/cameras/frame/1')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            for index in (-1, 3, 100):
                self.assertEqual(self.client.get(f'/autodarts/cameras/frame/{index}').status_code, 404)
            self.assertEqual(frame.await_count, 1)

    def test_service_errors_are_reported_without_starting_a_service(self):
        with patch.object(cameras, 'get_data', new_callable=AsyncMock,
                          side_effect=cameras.CameraError(503, 'Start Autodarts in Supervisor.')):
            response = self.client.get('/autodarts/cameras/data')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {'detail': 'Start Autodarts in Supervisor.'})


if __name__ == '__main__':
    unittest.main()
