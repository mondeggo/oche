"""Image-only targeting and exposure scoring; never opens a capture device."""

import json
import math

from app.services import cameras
from app.services.cameras import CameraError


def require_dependencies():
    try:
        import cv2
        import numpy
    except ImportError:
        raise CameraError(503, 'Image analysis needs the updated Oche dependencies. Rebuild the image before using targeting or calibration.') from None


def decode(body):
    require_dependencies()
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0 or image.shape[0] * image.shape[1] > 32_000_000:
        raise CameraError(422, "The camera image cannot be analyzed.")
    return image


def _homography(device, width, height):
    """Use only an exact camera identity and resolution; never guess index order."""
    import numpy as np

    for directory in (cameras._config_path().parent,):
        path = directory / 'calibration.json'
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            entry = data['cameras'][device][f'{width}x{height}']['homography']
            matrix = np.asarray(entry, dtype=float).reshape(3, 3)
            if np.isfinite(matrix).all() and abs(np.linalg.det(matrix)) > 1e-10:
                return matrix
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return None


def target(body, device, mode):
    require_dependencies()
    import cv2
    import numpy as np

    image = decode(body)
    h, w = image.shape[:2]
    matrix = _homography(device, w, h)
    method = 'Autodarts calibration'
    if matrix is not None:
        inverse = np.linalg.inv(matrix)

        def project(x, y):
            p = inverse @ np.array([x, y, 1.0])
            if abs(p[2]) < 1e-8:
                raise CameraError(422, 'The calibration target is outside this camera view. Select an area manually.')
            return p[:2] / p[2]

        if mode == 't20':
            # Autodarts normalized board: center 500,500; outer double radius 323.
            # Triple center 103/170 of the outer radius; 20 is at the top.
            point = project(500, 500 - 323 * 103 / 170)
        else:
            points = [project(500 + 323 * math.cos(a), 500 + 323 * math.sin(a))
                      for a in np.linspace(0, 2 * math.pi, 180, endpoint=False)]
            point = min(points, key=lambda p: p[1])
    else:
        if mode == 't20':
            raise CameraError(422, 'T20 needs matching Autodarts calibration to determine board orientation. Select T20 manually or calibrate in Autodarts first.')
        # Fit the outer colored segment ring, with explicit rejection when the
        # board is obscured or too little of its colored ring is visible.
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = (cv2.inRange(hsv, (0, 65, 45), (14, 255, 255)) |
                cv2.inRange(hsv, (166, 65, 45), (180, 255, 255)) |
                cv2.inRange(hsv, (35, 55, 40), (88, 255, 255)))
        ys, xs = np.nonzero(mask)
        if len(xs) < 150:
            raise CameraError(422, 'No reliable board ring found. Select a wire manually.')
        points = np.column_stack((xs, ys)).astype(np.float32)
        hull = cv2.convexHull(points)
        if len(hull) < 5:
            raise CameraError(422, 'No reliable board ring found. Select a wire manually.')
        ellipse = cv2.fitEllipse(hull)
        (cx, cy), (a, b), angle = ellipse
        if min(a, b) < min(w, h) * .12 or a > w * 1.5 or b > h * 1.5:
            raise CameraError(422, 'Board detection is uncertain. Select a wire manually.')
        radians = math.radians(angle)
        samples = np.linspace(0, 2 * math.pi, 180, endpoint=False)
        ring = [(cx + a / 2 * math.cos(t) * math.cos(radians) - b / 2 * math.sin(t) * math.sin(radians),
                 cy + a / 2 * math.cos(t) * math.sin(radians) + b / 2 * math.sin(t) * math.cos(radians)) for t in samples]
        point = min(ring, key=lambda p: p[1])
        method = 'Estimated colored ring; verify the target on the preview'
    x, y = map(float, point)
    if not math.isfinite(x + y) or not (0 <= x < w and 0 <= y < h):
        raise CameraError(422, 'The target is outside this image. Select an area manually.')
    size = min(w, h) * .12
    return {'roi': {'x': max(0, min(1 - size / w, (x - size / 2) / w)),
                    'y': max(0, min(1 - size / h, (y - size / 2) / h)),
                    'width': size / w, 'height': size / h},
            'method': method, 'mode': mode, 'width': w, 'height': h}


def quality(body, roi):
    """Reward visible detail while penalizing noise/gain and clipped exposure."""
    require_dependencies()
    import cv2
    import numpy as np

    image = decode(body)
    h, w = image.shape[:2]
    x, y = int(roi['x'] * w), int(roi['y'] * h)
    right, bottom = min(w, math.ceil((roi['x'] + roi['width']) * w)), min(h, math.ceil((roi['y'] + roi['height']) * h))
    crop = image[y:bottom, x:right]
    if crop.shape[0] < 16 or crop.shape[1] < 16:
        raise CameraError(422, 'Select a larger detailed area before calibration.')
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    dark, bright = float(np.mean(gray <= 10)), float(np.mean(gray >= 245))
    brightness = float(gray.mean())
    detail = float(cv2.Laplacian(blurred, cv2.CV_64F).var())
    score = detail * max(0, 1 - dark - bright) ** 2
    if brightness < 30 or brightness > 225 or dark > .75 or bright > .45:
        score = 0
    return {'score': score, 'brightness': brightness, 'clipped_dark': dark, 'clipped_bright': bright}
