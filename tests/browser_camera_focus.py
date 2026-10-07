"""Synthetic focus-analysis checks: python tests/browser_camera_focus.py.

Uses the existing Playwright/Microsoft Edge test environment. No camera,
server, image packages or network connection is required.
"""
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright


class CameraFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(channel='msedge', headless=True)
        cls.addClassCleanup(cls.browser.close)
        cls.page = cls.browser.new_page()
        cls.page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'app/static/js/camera-focus.js'))
        cls.page.add_script_tag(content='''
            window.makeImage = (width, height, valueAt) => {
                const image = new ImageData(width, height);
                for (let y = 0; y < height; y++) {
                    for (let x = 0; x < width; x++) {
                        const value = valueAt(x, y);
                        const offset = (y * width + x) * 4;
                        image.data[offset] = image.data[offset + 1] = image.data[offset + 2] = value;
                        image.data[offset + 3] = 255;
                    }
                }
                return image;
            };
            window.checker = (x, y) => ((Math.floor(x / 6) + Math.floor(y / 6)) % 2) ? 205 : 50;
            window.blur = (image, passes) => {
                const {width, height} = image;
                let values = Float64Array.from({length: width * height}, (_, index) => image.data[index * 4]);
                for (let pass = 0; pass < passes; pass++) {
                    const horizontal = new Float64Array(values.length);
                    const output = new Float64Array(values.length);
                    for (let y = 0; y < height; y++) {
                        for (let x = 0; x < width; x++) {
                            const index = y * width + x;
                            horizontal[index] = (values[y * width + Math.max(0, x - 1)] + 2 * values[index]
                                + values[y * width + Math.min(width - 1, x + 1)]) / 4;
                        }
                    }
                    for (let y = 0; y < height; y++) {
                        for (let x = 0; x < width; x++) {
                            const index = y * width + x;
                            output[index] = (horizontal[Math.max(0, y - 1) * width + x] + 2 * horizontal[index]
                                + horizontal[Math.min(height - 1, y + 1) * width + x]) / 4;
                        }
                    }
                    values = output;
                }
                return makeImage(width, height, (x, y) => values[y * width + x]);
            };
        ''')

    def test_sharp_edges_score_above_progressively_blurred_edges(self):
        samples = self.page.evaluate('''() => {
            const sharp = makeImage(128, 96, checker);
            return [sharp, blur(sharp, 2), blur(sharp, 6)].map(image => OcheCameraFocus.analyze(image));
        }''')
        self.assertTrue(all(sample['usable'] for sample in samples))
        self.assertGreater(samples[0]['score'], samples[1]['score'] * 2)
        self.assertGreater(samples[1]['score'], samples[2]['score'] * 1.2)
        self.assertGreater(samples[0]['score'], 100)  # Raw units have no universal 100-point ceiling.
        self.assertTrue(all(sample['lightingWarning'] is None for sample in samples))

    def test_roi_excludes_detail_and_lighting_outside_its_bounds(self):
        samples = self.page.evaluate('''() => {
            const original = makeImage(128, 96, (x, y) => x < 64 ? checker(x, y) : 128);
            const changed = makeImage(128, 96, (x, y) => x < 64 ? checker(x, y) : 255);
            const left = {x: .05, y: .1, width: .4, height: .8};
            return [OcheCameraFocus.analyze(original, left), OcheCameraFocus.analyze(changed, left),
                OcheCameraFocus.analyze(original, {x: .55, y: .1, width: .4, height: .8})];
        }''')
        self.assertEqual(samples[0], samples[1])
        self.assertTrue(samples[0]['usable'])
        self.assertFalse(samples[2]['usable'])
        self.assertEqual(samples[2]['reason'], 'low-detail')
        self.assertAlmostEqual(samples[2]['score'], 0, places=8)
        self.assertIsNone(samples[2]['lightingWarning'])

    def test_lighting_warnings_distinguish_dark_bright_clipped_and_flat_images(self):
        samples = self.page.evaluate('''() => [
            makeImage(100, 80, (x, y) => (x * 13 + y * 17) % 24),
            makeImage(100, 80, () => 255),
            makeImage(100, 80, x => x % 10 < 4 ? 255 : 0),
            makeImage(100, 80, () => 128),
        ].map(image => OcheCameraFocus.analyze(image))''')
        self.assertEqual([sample['reason'] for sample in samples],
                         ['too-dark', 'too-bright', 'clipped', 'low-detail'])
        self.assertTrue(all(not sample['usable'] for sample in samples))
        self.assertTrue(all(sample['lightingWarning'] for sample in samples[:3]))
        self.assertIsNone(samples[3]['lightingWarning'])
        self.assertGreater(samples[0]['score'], 0)  # Underexposed noise must not become a focus success.
        self.assertAlmostEqual(samples[1]['brightness'], 255)
        self.assertEqual(samples[1]['clippedBright'], 1)
        self.assertAlmostEqual(samples[2]['clippedDark'], .6)
        self.assertAlmostEqual(samples[2]['clippedBright'], .4)
        self.assertAlmostEqual(samples[3]['brightness'], 128)

    def test_roi_clamping_tiny_regions_and_invalid_frames_are_finite(self):
        result = self.page.evaluate('''() => {
            const image = makeImage(32, 32, checker);
            const samples = [OcheCameraFocus.analyze(null),
                OcheCameraFocus.analyze({width: 32, height: 32, data: new Uint8ClampedArray(4)}),
                OcheCameraFocus.analyze(image, {x: 1.2, y: .5, width: .4, height: .2}),
                OcheCameraFocus.analyze(image, {x: .2, y: .2, width: .01, height: .01}),
                OcheCameraFocus.analyze(image, {x: .2, y: .2, width: -1, height: .5})];
            return {samples,
                clipped: OcheCameraFocus.clampROI({x: -.25, y: .8, width: .5, height: .5}),
                fallback: OcheCameraFocus.clampROI({x: NaN, y: Infinity, width: NaN, height: Infinity}),
                finite: samples.every(sample => ['score', 'brightness', 'contrast', 'clippedDark',
                    'clippedBright', 'width', 'height', 'pixels'].every(key => Number.isFinite(sample[key])))};
        }''')
        self.assertTrue(result['finite'])
        self.assertTrue(all(not sample['usable'] and sample['warning'] for sample in result['samples']))
        self.assertEqual(result['samples'][0]['reason'], 'invalid-frame')
        self.assertEqual(result['samples'][2]['reason'], 'roi-too-small')
        self.assertEqual(result['clipped']['x'], 0)
        self.assertEqual(result['clipped']['width'], .25)
        self.assertAlmostEqual(result['clipped']['height'], .2)
        self.assertEqual(result['fallback'], {'x': 0, 'y': 0, 'width': 1, 'height': 1})

    def test_meter_smooths_tracks_relative_best_and_preserves_history_for_bad_light(self):
        result = self.page.evaluate('''() => {
            const meter = new OcheCameraFocus.Meter({alpha: .5});
            const first = meter.push({score: 100, usable: true});
            const better = meter.push({score: 200, usable: true});
            const worse = meter.push({score: 50, usable: true});
            const rejected = meter.push({score: 100000, usable: false, warning: 'Add light.', lightingWarning: 'Add light.'});
            const invalid = meter.push({score: NaN, usable: true});
            const reset = meter.reset();
            const nextSession = meter.push({score: 10, usable: true});
            return {first, better, worse, rejected, invalid, reset, nextSession};
        }''')
        self.assertEqual(result['first']['current'], 100)
        self.assertEqual(result['better']['current'], 150)
        self.assertEqual(result['better']['best'], 150)
        self.assertEqual(result['better']['trend'], 'improving')
        self.assertEqual(result['worse']['current'], 100)
        self.assertEqual(result['worse']['best'], 150)
        self.assertAlmostEqual(result['worse']['relative'], 2 / 3)
        self.assertEqual(result['worse']['trend'], 'declining')
        for sample in (result['rejected'], result['invalid']):
            self.assertFalse(sample['usable'])
            self.assertEqual(sample['current'], 100)
            self.assertEqual(sample['best'], 150)
            self.assertEqual(sample['samples'], 3)
            self.assertEqual(sample['trend'], 'unavailable')
        self.assertEqual(result['rejected']['lightingWarning'], 'Add light.')
        self.assertEqual(result['reset']['best'], 0)
        self.assertEqual(result['reset']['samples'], 0)
        self.assertEqual(result['nextSession']['best'], 10)
        self.assertEqual(result['nextSession']['relative'], 1)

    def test_meter_does_not_report_small_jitter_as_a_focus_trend(self):
        result = self.page.evaluate('''() => {
            const meter = new OcheCameraFocus.Meter();
            meter.push({score: 100, usable: true});
            return [100.5, 99.5, 100.2, 99.8].map(score => meter.push({score, usable: true}));
        }''')
        self.assertTrue(all(sample['trend'] == 'steady' for sample in result))
        self.assertTrue(all(0 <= sample['relative'] <= 1 for sample in result))


if __name__ == '__main__':
    unittest.main()
