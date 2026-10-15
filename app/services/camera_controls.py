"""Bounded V4L2 control operations, profiles and reversible exposure sweeps.

Autodarts remains the sole image producer. Drivers that reject live control
changes report an error; this module never starts a second capture or restarts
the service. All mutations share the camera-settings lock.
"""

import asyncio
import json
import re
import statistics
import subprocess
import threading
import time
import uuid

from app import config
from app.services import cameras, camera_vision
from app.services.cameras import CameraError

ALLOWED = frozenset('brightness contrast saturation hue gamma gain sharpness backlight_compensation '
                    'white_balance_temperature_auto white_balance_automatic white_balance_temperature '
                    'power_line_frequency exposure_auto auto_exposure exposure_absolute exposure_time_absolute '
                    'exposure_auto_priority exposure_dynamic_framerate focus_auto focus_automatic_continuous focus_absolute zoom_absolute'.split())
AUTO = frozenset({'exposure_auto', 'auto_exposure', 'white_balance_temperature_auto',
                  'white_balance_automatic', 'focus_auto', 'focus_automatic_continuous'})
_job = None
_task = None
_cancel = None
_file_lock = threading.Lock()


def _path(name):
    return config.DATA_DIR / name


def _read(name, default):
    try:
        return json.loads(_path(name).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        raise CameraError(503, 'The saved camera data cannot be read. Check the data directory.') from None


def _write(name, data):
    path = _path(name)
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
        temporary.replace(path)
    except OSError:
        raise CameraError(503, 'Camera data could not be saved to the data directory.') from None


def command(path, *arguments):
    if not re.fullmatch(r'/dev/video[0-9]+', path):
        raise CameraError(422, 'This camera has no supported local V4L2 device path.')
    try:
        result = subprocess.run(['v4l2-ctl', '--device', path, *arguments],
                                capture_output=True, text=True, timeout=5, check=False)
    except FileNotFoundError:
        raise CameraError(503, 'Camera controls need v4l-utils. Rebuild the Oche image or install v4l-utils.') from None
    except (OSError, subprocess.TimeoutExpired):
        raise CameraError(503, 'The camera controls did not respond. Check device permissions and the USB connection.') from None
    if result.returncode:
        raise CameraError(409, 'The camera driver rejected the control request. Check permissions, automatic modes, and whether this camera permits controls while capturing.')
    return result.stdout


def parse_controls(raw):
    controls = {}
    current = None
    for line in raw.splitlines():
        match = re.match(r'\s*(\w+)\s+0x[0-9a-fA-F]+\s+\((\w+)\)\s*:\s*(.*)', line)
        if match:
            name, kind, fields = match.groups()
            current = None
            if name not in ALLOWED or kind not in ('int', 'bool', 'menu', 'intmenu'):
                continue
            values = {key: int(value) for key, value in re.findall(r'(min|max|step|default|value)=(-?\d+)', fields)}
            if 'value' not in values:
                continue
            if kind == 'bool':
                values.update(min=0, max=1, step=1)
            values.setdefault('step', 1)
            if not all(key in values for key in ('min', 'max')):
                continue
            flags = fields.split('flags=', 1)[-1] if 'flags=' in fields else ''
            current = {'name': name, 'kind': kind, **values, 'menu': {},
                       'writable': not any(flag in flags for flag in ('read-only', 'disabled', 'grabbed')),
                       'inactive': 'inactive' in flags}
            controls[name] = current
        elif current:
            menu = re.match(r'\s+(-?\d+):\s*(.+)', line)
            if menu:
                current['menu'][menu[1]] = menu[2].strip()
    return controls


def read_controls(path):
    return parse_controls(command(path, '--list-ctrls-menus'))


def validate(values, controls, *, inactive=False):
    if not values or len(values) > len(ALLOWED):
        raise CameraError(422, 'Choose at least one supported camera control.')
    for name, value in values.items():
        control = controls.get(name)
        if (name not in ALLOWED or type(value) is not int or control is None or
                not control['writable'] or (control['inactive'] and not inactive) or
                not control['min'] <= value <= control['max'] or
                (value - control['min']) % max(1, control['step']) or
                (control['kind'] in ('menu', 'intmenu') and str(value) not in control['menu'])):
            raise CameraError(422, f'Unsupported or unavailable value for camera control {name[:64]}.')


def set_values(path, values, *, inactive=False):
    controls = read_controls(path)
    validate(values, controls, inactive=True)
    # Manual mode first, dependent values next, automatic modes last.
    def order(name):
        if name not in AUTO:
            return 1
        c = controls[name]
        manual = (values[name] == 0 if c['kind'] == 'bool' else
                  c['menu'].get(str(values[name]), '').lower().startswith('manual'))
        return 0 if manual else 2
    ordered = sorted(values, key=order)
    for name in ordered:
        validate({name: values[name]}, read_controls(path), inactive=inactive)
        command(path, f'--set-ctrl={name}={values[name]}')
    actual = read_controls(path)
    if any(actual.get(name, {}).get('value') != value for name, value in values.items()):
        raise CameraError(409, 'The camera did not retain the requested controls. Reload to see its current values.')


def restore(path, values):
    """Dependent controls first (while manual); automatic modes last."""
    errors = []
    current = read_controls(path)
    # If a previous request re-enabled automation before failing, make its
    # dependent controls writable for rollback, then restore original modes.
    for name, c in current.items():
        if name not in AUTO or not c['writable'] or name not in values:
            continue
        if c['kind'] == 'bool':
            manual = 0
        else:
            manual = next((int(k) for k, label in c['menu'].items() if label.lower().startswith('manual')), None)
        if manual is not None and c['value'] != manual:
            try:
                set_values(path, {name: manual})
            except CameraError:
                errors.append(name)
    current = read_controls(path)
    for name, value in sorted(values.items(), key=lambda item: item[0] in AUTO):
        if name not in current or current[name]['value'] == value:
            continue
        try:
            set_values(path, {name: value}, inactive=True)
        except CameraError:
            errors.append(name)
    if errors:
        raise CameraError(503, 'Could not restore camera controls: ' + ', '.join(errors) + '. Use Restore interrupted changes before continuing.')


async def device(index, expected=None, revision=None):
    data = await cameras.get_data()
    identifier = data['settings']['devices'][index]
    if revision is not None and data['revision'] != revision:
        raise CameraError(409, 'Capture settings changed during calibration. Run a new sweep.')
    if expected is not None and identifier != expected:
        raise CameraError(409, 'The camera assignment changed. Reload the controls.')
    if identifier not in {entry['id'] for entry in data['devices']}:
        raise CameraError(409, 'The configured camera is unavailable.')
    path = cameras._native_path(identifier)
    if path is None:
        raise CameraError(422, 'This camera has no supported local V4L2 device path.')
    return identifier, path


def idle():
    if _task is not None and not _task.done():
        raise CameraError(409, 'A camera calibration is already running. Cancel it or wait for review.')
    if _read('camera-recovery.json', None):
        raise CameraError(409, 'Interrupted camera changes need restoration. Use Restore interrupted changes.')


async def get_controls(index):
    identifier, path = await device(index)
    controls = await asyncio.to_thread(read_controls, path)
    return {'device': identifier, 'controls': list(controls.values()),
            'recovery_required': bool(_read('camera-recovery.json', None))}


async def _mutate(function, *args):
    # A subprocess in a worker thread must finish before rollback starts, even
    # if the request or server task is cancelled in the middle of a write.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def update(index, identifier, values=None, defaults=False, expected_revision=None):
    idle()
    async with cameras._settings_lock:
        idle()
        _, path = await device(index, identifier)
        if expected_revision is not None and (await cameras.get_data())['revision'] != expected_revision:
            raise CameraError(409, 'Camera settings changed after calibration. Run a new sweep.')
        controls = await asyncio.to_thread(read_controls, path)
        if defaults:
            values = {name: c['default'] for name, c in controls.items()
                      if c['writable'] and not c['inactive'] and 'default' in c}
        validate(values, controls, inactive=True)
        before = {name: c['value'] for name, c in controls.items() if c['writable']}
        journal = {'index': index, 'device': identifier, 'values': before}
        _write('camera-recovery.json', journal)
        try:
            await _mutate(set_values, path, values)
        except BaseException:
            await _mutate(restore, path, before)
            _write('camera-recovery.json', None)
            raise
        _write('camera-recovery.json', None)
        invalidate_review()
    return await get_controls(index)


async def recover():
    if _task is not None and not _task.done():
        raise CameraError(409, 'Cancel the running calibration before restoring controls.')
    async with cameras._settings_lock:
        journal = _read('camera-recovery.json', None)
        if journal:
            _, path = await device(journal['index'], journal['device'])
            await _mutate(restore, path, journal['values'])
            _write('camera-recovery.json', None)
    if _job and _job['state'] == 'recovery_required':
        _job.update(state='cancelled', message='Interrupted controls restored. You can start a new sweep.')
    return {'restored': True}


def profiles():
    with _file_lock:
        return _read('camera-profiles.json', {})


async def save_profile(index, identifier, name):
    idle()
    async with cameras._settings_lock:
        idle()
        _, path = await device(index, identifier)
        controls = await asyncio.to_thread(read_controls, path)
        # Save active controls only; profiles are tied to the exact camera identity.
        values = {key: c['value'] for key, c in controls.items() if c['writable'] and not c['inactive']}
        if not values:
            raise CameraError(422, 'No writable controls are available to save.')
        with _file_lock:
            saved = _read('camera-profiles.json', {})
            if len(saved) >= 50 and name not in saved:
                raise CameraError(422, 'Delete a camera profile before saving another (maximum 50).')
            saved[name] = {'device': identifier, 'values': values}
            _write('camera-profiles.json', saved)
    return {'saved': True}


def delete_profile(name):
    with _file_lock:
        saved = _read('camera-profiles.json', {})
        saved.pop(name, None)
        _write('camera-profiles.json', saved)
    return {'deleted': True}


async def apply_profile(index, identifier, name):
    saved = profiles().get(name)
    if saved is None or saved['device'] != identifier:
        raise CameraError(422, 'Choose a profile saved for this camera.')
    return await update(index, identifier, saved['values'])


def invalidate_review():
    if _job and _job['state'] == 'review':
        _job.update(state='superseded', message='Controls or capture settings changed. Run a new calibration sweep.')


def job_status():
    return _job or {'state': 'idle'}


async def start(index, identifier, roi):
    global _job, _task, _cancel
    idle()
    if cameras._settings_lock.locked():
        raise CameraError(409, 'Camera settings are being changed. Try again shortly.')
    async with cameras._settings_lock:
        idle()
        await device(index, identifier)
        _cancel = asyncio.Event()
        _job = {'id': uuid.uuid4().hex, 'index': index, 'device': identifier, 'state': 'running',
                'progress': 0, 'samples': [], 'message': 'Reading camera controls…'}
        _task = asyncio.create_task(_sweep(_job, dict(roi), _cancel))
    return dict(_job)


async def cancel():
    if _task is not None and not _task.done():
        _cancel.set()
    return job_status()


async def shutdown():
    """Finish restoration before the managed Autodarts daemon is stopped."""
    if _task is not None and not _task.done():
        _cancel.set()
        await asyncio.shield(_task)


def candidates(control, count=5):
    low, high, step = control['min'], control['max'], max(1, control['step'])
    # Logarithmic exposure sampling avoids spending the sweep at very long
    # exposure times. Include the current value as a baseline candidate.
    if low > 0:
        values = [round(low * (high / low) ** (i / (count - 1))) for i in range(count)]
    else:
        values = [round(low + (high - low) * i / (count - 1)) for i in range(count)]
    values.append(control['value'])
    last = low + (high - low) // step * step
    return sorted({min(last, max(low, low + round((v - low) / step) * step)) for v in values})


async def _sweep(job, roi, cancelled):
    before = None
    path = None
    deadline = time.monotonic() + 180
    await cameras._settings_lock.acquire()
    try:
        camera_vision.require_dependencies()
        _, path = await device(job['index'], job['device'])
        data = await cameras.get_data()
        job['camera_revision'] = data['revision']
        controls = await asyncio.to_thread(read_controls, path)
        exposure = next((name for name in ('exposure_time_absolute', 'exposure_absolute') if name in controls), None)
        gain = 'gain' if 'gain' in controls and controls['gain']['writable'] else None
        if not exposure or not controls[exposure]['writable']:
            raise CameraError(422, 'This camera does not expose a writable exposure control.')
        before = {name: c['value'] for name, c in controls.items() if c['writable']}
        _write('camera-recovery.json', {'index': job['index'], 'device': job['device'], 'values': before})

        async def check():
            if cancelled.is_set():
                raise CameraError(409, 'Calibration cancelled. Previous controls restored.')
            if time.monotonic() > deadline:
                raise CameraError(408, 'Calibration timed out. Previous controls restored.')

        async def measure(values):
            await check()
            await device(job['index'], job['device'], job['camera_revision'])
            if values:
                await _mutate(set_values, path, values)
            # Allow buffered frames and auto exposure settling to pass.
            for _ in range(5):
                await asyncio.sleep(.2)
                await check()
            measurements = []
            for _ in range(3):
                await check()
                body, _ = await cameras.get_frame(job['index'])
                measurements.append(await asyncio.to_thread(camera_vision.quality, body, roi))
                await asyncio.sleep(.15)
            await device(job['index'], job['device'], job['camera_revision'])
            result = {key: statistics.median(item[key] for item in measurements) for key in measurements[0]}
            job['samples'].append({'values': dict(values), **result})
            return result['score']

        baseline = await measure({})
        modes = {}
        for name in ('auto_exposure', 'exposure_auto'):
            if name in controls:
                manual = next((int(k) for k, label in controls[name]['menu'].items() if label.lower().startswith('manual')), None)
                if manual is None:
                    raise CameraError(422, 'The camera does not advertise a manual exposure mode.')
                modes[name] = manual
        for name in ('exposure_auto_priority', 'exposure_dynamic_framerate'):
            if name in controls and controls[name]['writable']:
                modes[name] = 0
        if modes:
            await _mutate(set_values, path, modes)
        controls = await asyncio.to_thread(read_controls, path)
        # Standard V4L2 absolute exposure is in 100 microsecond units.
        # Stay within the requested frame period to avoid sacrificing FPS.
        exposure_control = dict(controls[exposure])
        cap = min(exposure_control['max'], int(10000 / max(1, data['settings']['fps'])))
        if cap < exposure_control['min']:
            raise CameraError(422, 'No exposure setting fits the requested frame rate.')
        exposure_control['max'] = exposure_control['min'] + ((cap - exposure_control['min']) // max(1, exposure_control['step'])) * max(1, exposure_control['step'])
        exposure_values = candidates(exposure_control)
        gain_values = candidates(controls[gain], 3) if gain else [None]
        total = len(exposure_values) + len(gain_values)
        best_score = -1
        best = {}
        fixed_gain = {gain: controls[gain]['min']} if gain else {}
        for value in exposure_values:
            values = {**modes, **fixed_gain, exposure: value}
            job['message'] = 'Comparing exposure settings…'
            score = await measure(values)
            job['progress'] = (len(job['samples']) - 1) / total
            if score > best_score:
                best_score, best = score, values
        for value in gain_values if gain else []:
            values = {**best, gain: value}
            job['message'] = 'Comparing gain settings…'
            score = await measure(values)
            job['progress'] = (len(job['samples']) - 1) / total
            if score > best_score:
                best_score, best = score, values
        await check()
        job.update(proposal=best if best_score > baseline and best_score > 0 else {},
                   baseline_score=baseline, proposed_score=max(baseline, best_score), progress=1)
    except asyncio.CancelledError:
        job.update(state='cancelled', message='Calibration interrupted. Previous controls restored.')
    except CameraError as error:
        job.update(state='cancelled' if cancelled.is_set() else 'failed', message=error.detail)
    except Exception:
        job.update(state='failed', message='Calibration failed. Check the camera connection and controls.')
    finally:
        if before is not None and path is not None:
            try:
                # Restore the same identity only. A changed assignment leaves
                # the journal intact for explicit recovery, never writes blindly.
                await device(job['index'], job['device'])
                await _mutate(restore, path, before)
                _write('camera-recovery.json', None)
            except Exception:
                job.update(state='recovery_required', message='Previous controls could not be restored. Use Restore interrupted changes after checking the camera connection.')
        cameras._settings_lock.release()
        if job['state'] == 'running':
            job.update(state='review', message='Previous controls restored. Review the proposal before applying it.' if job.get('proposal') else 'Previous controls restored. No improvement found; keep the existing settings.')


async def apply_calibration(job_id):
    job = job_status()
    if job.get('id') != job_id or job['state'] != 'review' or not job.get('proposal'):
        raise CameraError(409, 'No calibration proposal is ready to apply.')
    result = await update(job['index'], job['device'], job['proposal'], expected_revision=job['camera_revision'])
    job.update(state='applied', message='Calibration controls applied. Save a profile to reuse these settings.')
    return result
