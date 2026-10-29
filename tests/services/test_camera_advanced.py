import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.services import camera_controls as controls, camera_vision as vision, cameras


RAW = '''
                     brightness 0x00980900 (int)    : min=-64 max=64 step=2 default=0 value=2
                  auto_exposure 0x009a0901 (menu)   : min=0 max=3 default=3 value=3
                                1: Manual Mode
                                3: Aperture Priority Mode
         exposure_time_absolute 0x009a0902 (int)    : min=1 max=1000 step=1 default=100 value=100 flags=inactive
                           gain 0x00980913 (int)    : min=0 max=20 step=1 default=10 value=10
                       pan_speed 0x009a0920 (int)    : min=-8 max=8 step=1 default=0 value=0
                      sharpness 0x0098091b (int)    : min=0 max=10 step=1 default=5 value=5 flags=read-only
'''


class Driver:
    def __init__(self):
        self.state = controls.parse_controls(RAW)
        self.writes = []
        self.fail_name = None

    def read(self, path):
        result = copy.deepcopy(self.state)
        result['exposure_time_absolute']['inactive'] = result['auto_exposure']['value'] != 1
        return result

    def command(self, path, argument):
        self.assert_path(path)
        self.writes.append(argument)
        name, value = argument.removeprefix('--set-ctrl=').split('=')
        if name == self.fail_name:
            self.fail_name = None
            raise cameras.CameraError(409, 'Driver busy')
        if self.read(path)[name]['inactive']:
            raise cameras.CameraError(409, 'Inactive')
        self.state[name]['value'] = int(value)
        return ''

    @staticmethod
    def assert_path(path):
        assert path == '/dev/video0'


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.driver = Driver()
        self.read = patch.object(controls, 'read_controls', side_effect=self.driver.read)
        self.command = patch.object(controls, 'command', side_effect=self.driver.command)
        self.read.start(); self.command.start()
        self.addCleanup(self.read.stop); self.addCleanup(self.command.stop)

    def test_parser_restricts_controls_and_preserves_sparse_menu(self):
        parsed = controls.parse_controls(RAW)
        self.assertNotIn('pan_speed', parsed)
        self.assertEqual(parsed['auto_exposure']['menu'], {'1': 'Manual Mode', '3': 'Aperture Priority Mode'})
        self.assertFalse(parsed['sharpness']['writable'])
        self.assertTrue(parsed['exposure_time_absolute']['inactive'])

    def test_manual_mode_applied_before_dependent_exposure(self):
        controls.set_values('/dev/video0', {'exposure_time_absolute': 30, 'auto_exposure': 1})
        self.assertEqual(self.driver.writes[0], '--set-ctrl=auto_exposure=1')
        self.assertEqual(self.driver.state['exposure_time_absolute']['value'], 30)

    def test_invalid_controls_are_rejected_before_writing(self):
        for values in ({'brightness': 3}, {'gain': 21}, {'auto_exposure': 2}, {'sharpness': 4},
                       {'gain': True}, {'gain;reboot': 1}, {'exposure_time_absolute': 30}):
            with self.subTest(values=values), self.assertRaises(cameras.CameraError):
                controls.set_values('/dev/video0', values)
        self.assertEqual(self.driver.writes, [])

    def test_restore_temporarily_disables_auto_then_restores_original_mode(self):
        controls.restore('/dev/video0', {'auto_exposure': 3, 'exposure_time_absolute': 33, 'gain': 10})
        self.assertEqual(self.driver.state['auto_exposure']['value'], 3)
        self.assertEqual(self.driver.state['exposure_time_absolute']['value'], 33)

    def test_candidates_obey_range_and_step(self):
        values = controls.candidates({'min': 3, 'max': 99, 'step': 4, 'value': 200})
        self.assertTrue(all(3 <= v <= 99 and (v - 3) % 4 == 0 for v in values))


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.driver = Driver()
        self.patches = [
            patch('app.config.DATA_DIR', Path(self.temporary.name)),
            patch.object(controls, 'read_controls', side_effect=self.driver.read),
            patch.object(controls, 'command', side_effect=self.driver.command),
            patch.object(controls, 'device', new_callable=AsyncMock, return_value=('camera-a', '/dev/video0')),
            patch.object(cameras, 'get_data', new_callable=AsyncMock, return_value={
                'revision': 'revision', 'settings': {'fps': 30}}),
            patch.object(cameras, '_settings_lock', asyncio.Lock()),
            patch.object(controls, '_job', None), patch.object(controls, '_task', None),
        ]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)

    async def test_failed_partial_write_rolls_back_and_clears_journal(self):
        self.driver.fail_name = 'gain'
        with self.assertRaises(cameras.CameraError):
            await controls.update(0, 'camera-a', {'auto_exposure': 1, 'exposure_time_absolute': 30, 'gain': 5})
        self.assertEqual(self.driver.state['auto_exposure']['value'], 3)
        self.assertEqual(self.driver.state['exposure_time_absolute']['value'], 100)
        self.assertIsNone(controls._read('camera-recovery.json', None))

    async def test_profiles_are_identity_bound_and_persisted(self):
        await controls.save_profile(0, 'camera-a', 'Evening')
        saved = controls.profiles()['Evening']
        self.assertEqual(saved['device'], 'camera-a')
        self.assertNotIn('exposure_time_absolute', saved['values'])
        with self.assertRaises(cameras.CameraError):
            await controls.apply_profile(0, 'different-camera', 'Evening')
        self.driver.state['gain']['value'] = 1
        with patch.object(controls, 'get_controls', new_callable=AsyncMock):
            await controls.apply_profile(0, 'camera-a', 'Evening')
        self.assertEqual(self.driver.state['gain']['value'], 10)
        controls.delete_profile('Evening')
        self.assertEqual(controls.profiles(), {})

    async def run_sweep(self, *, cancel=False, fail_frame=False, fail_restore=False, flat=False):
        event = asyncio.Event()
        job = {'id': 'job', 'index': 0, 'device': 'camera-a', 'state': 'running', 'samples': [], 'progress': 0}
        roi = {'x': .2, 'y': .2, 'width': .3, 'height': .3}

        async def frame(index):
            if fail_frame:
                raise cameras.CameraError(503, 'Camera disconnected')
            mode = self.driver.state['auto_exposure']['value']
            if mode == 1 and cancel:
                event.set()
            return b'frame', 'image/jpeg'

        def quality(body, selection):
            state = self.driver.state
            score = 0 if flat else (50 if state['auto_exposure']['value'] != 1 else
                                   max(0, 400 - (state['exposure_time_absolute']['value'] - 77) ** 2 - state['gain']['value']))
            return {'score': score, 'brightness': 128, 'clipped_dark': 0, 'clipped_bright': 0}

        patches = [patch.object(cameras, 'get_frame', side_effect=frame),
                   patch.object(vision, 'quality', side_effect=quality),
                   patch.object(controls.asyncio, 'sleep', new_callable=AsyncMock)]
        if fail_restore:
            patches.append(patch.object(controls, 'restore', side_effect=cameras.CameraError(503, 'disconnected')))
        for item in patches:
            item.start()
        try:
            await controls._sweep(job, roi, event)
        finally:
            for item in reversed(patches): item.stop()
        return job

    async def test_sweep_restores_before_review_and_never_exceeds_frame_period(self):
        job = await self.run_sweep()
        self.assertEqual(job['state'], 'review')
        self.assertTrue(job['proposal'])
        self.assertEqual(self.driver.state['auto_exposure']['value'], 3)
        self.assertEqual(self.driver.state['exposure_time_absolute']['value'], 100)
        self.assertEqual(self.driver.state['gain']['value'], 10)
        self.assertTrue(all(sample['values'].get('exposure_time_absolute', 0) <= 333 for sample in job['samples']))
        self.assertIsNone(controls._read('camera-recovery.json', None))
        self.assertFalse(cameras._settings_lock.locked())

    async def test_cancellation_and_frame_failure_restore_original_controls(self):
        for option in ({'cancel': True}, {'fail_frame': True}):
            job = await self.run_sweep(**option)
            self.assertIn(job['state'], ('cancelled', 'failed'))
            self.assertEqual(self.driver.state['auto_exposure']['value'], 3)
            self.assertEqual(self.driver.state['exposure_time_absolute']['value'], 100)
            self.assertIsNone(controls._read('camera-recovery.json', None))

    async def test_unusable_images_produce_no_proposal(self):
        job = await self.run_sweep(flat=True)
        self.assertEqual(job['state'], 'review')
        self.assertEqual(job['proposal'], {})

    async def test_failed_restoration_blocks_writes_until_explicit_recovery(self):
        job = await self.run_sweep(fail_restore=True)
        self.assertEqual(job['state'], 'recovery_required')
        self.assertIsNotNone(controls._read('camera-recovery.json', None))
        with self.assertRaises(cameras.CameraError):
            await controls.update(0, 'camera-a', {'gain': 1})
        controls._job = job
        await controls.recover()
        self.assertIsNone(controls._read('camera-recovery.json', None))
        self.assertEqual(controls.job_status()['state'], 'cancelled')

    async def test_concurrent_start_and_stale_proposal_rejected(self):
        blocker = asyncio.Event()
        async def hold(*args): await blocker.wait()
        with patch.object(controls, '_sweep', side_effect=hold):
            await controls.start(0, 'camera-a', {'x': 0, 'y': 0, 'width': .5, 'height': .5})
            with self.assertRaises(cameras.CameraError):
                await controls.start(1, 'camera-b', {'x': 0, 'y': 0, 'width': .5, 'height': .5})
            blocker.set()
            await controls._task
        controls._job = {'id': 'job', 'index': 0, 'device': 'camera-a', 'state': 'review',
                         'proposal': {'gain': 3}, 'camera_revision': 'old'}
        with self.assertRaises(cameras.CameraError):
            await controls.apply_calibration('job')
        self.assertEqual(self.driver.writes, [])

    async def test_shutdown_requests_cancellation_and_waits_for_restoration(self):
        restored = asyncio.Event()
        controls._cancel = asyncio.Event()

        async def running():
            await controls._cancel.wait()
            restored.set()

        controls._task = asyncio.create_task(running())
        await controls.shutdown()
        self.assertTrue(restored.is_set())
        self.assertTrue(controls._task.done())


class VisionTests(unittest.TestCase):
    @staticmethod
    def encode(image):
        return cv2.imencode('.png', image)[1].tobytes()

    def test_calibrated_t20_and_double_and_missing_orientation(self):
        body = self.encode(np.full((1000, 1000, 3), 128, dtype=np.uint8))
        with patch.object(vision, '_homography', return_value=np.eye(3)):
            t20 = vision.target(body, 'camera-a', 't20')
            double = vision.target(body, 'camera-a', 'furthest_double')
        t20_y = t20['roi']['y'] + t20['roi']['height'] / 2
        double_y = double['roi']['y'] + double['roi']['height'] / 2
        self.assertAlmostEqual(t20_y, (500 - 323 * 103 / 170) / 1000)
        self.assertAlmostEqual(double_y, .177, places=3)
        with patch.object(vision, '_homography', return_value=None), self.assertRaises(cameras.CameraError):
            vision.target(body, 'camera-a', 't20')

    def test_colored_ring_fallback_rejects_blank_frames(self):
        image = np.full((480, 640, 3), 128, dtype=np.uint8)
        cv2.ellipse(image, (320, 260), (220, 100), 0, 0, 360, (0, 180, 0), 8)
        with patch.object(vision, '_homography', return_value=None):
            result = vision.target(self.encode(image), 'camera-a', 'furthest_double')
            self.assertAlmostEqual(result['roi']['y'] + result['roi']['height'] / 2, 156 / 480, delta=.03)
            with self.assertRaises(cameras.CameraError):
                vision.target(self.encode(np.full_like(image, 128)), 'camera-a', 'furthest_double')

    def test_quality_penalizes_clipping_and_invalid_images(self):
        roi = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
        pattern = np.indices((96, 128)).sum(axis=0) // 6 % 2
        image = np.repeat(np.where(pattern, 205, 50).astype(np.uint8)[..., None], 3, axis=2)
        self.assertGreater(vision.quality(self.encode(image), roi)['score'], 0)
        for value in (0, 255):
            self.assertEqual(vision.quality(self.encode(np.full_like(image, value)), roi)['score'], 0)
        with self.assertRaises(cameras.CameraError): vision.decode(b'not an image')

    def test_geometry_requires_exact_camera_and_resolution_in_active_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'calibration.json').write_text(json.dumps({'cameras': {
                'camera-a': {'640x480': {'homography': np.eye(3).tolist()}}}}))
            with patch.object(cameras, '_config_path', return_value=root / 'config.toml'):
                self.assertIsNotNone(vision._homography('camera-a', 640, 480))
                self.assertIsNone(vision._homography('camera-b', 640, 480))
                self.assertIsNone(vision._homography('camera-a', 1280, 720))


class AdvancedRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_validation_and_same_origin_for_new_mutations(self):
        client = self.client
        with patch.object(controls, 'update', new_callable=AsyncMock) as update:
            update.return_value = {'controls': []}
            payload = {'device': 'camera-a', 'values': {'gain': 2}}
            self.assertEqual(client.patch('/autodarts/cameras/controls/0', json=payload).status_code, 200)
            for extra in ({'values': {'gain': True}}, {'values': {'gain': 2.5}}, {'url': 'http://remote'}):
                self.assertEqual(client.patch('/autodarts/cameras/controls/0', json={**payload, **extra}).status_code, 422)
            self.assertEqual(client.patch('/autodarts/cameras/controls/3', json=payload).status_code, 422)
            self.assertEqual(client.patch('/autodarts/cameras/controls/0', json=payload,
                                         headers={'origin': 'https://elsewhere.test'}).status_code, 403)
            self.assertEqual(update.await_count, 1)
            self.assertEqual(client.post('/autodarts/cameras/calibration/start/0', json={
                'device': 'camera-a', 'roi': {'x': .9, 'y': 0, 'width': .2, 'height': .2}}).status_code, 422)
        client = self.client
        with patch.object(controls, 'cancel', new_callable=AsyncMock, return_value={'state': 'cancelled'}) as cancel:
            self.assertEqual(client.post('/autodarts/cameras/calibration/cancel').status_code, 200)
            self.assertEqual(cancel.await_count, 1)


if __name__ == '__main__':
    unittest.main()
