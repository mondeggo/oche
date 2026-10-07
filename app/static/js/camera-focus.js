// Browser-only focus feedback. Scores compare one camera, ROI, resolution and
// exposure within a session; they are not a calibrated measure of image quality.
(() => {
  'use strict';

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
  const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;

  /** Clip a normalized rectangle to the image. Empty selections stay empty. */
  function clampROI(roi = {}) {
    const source = roi && typeof roi === 'object' ? roi : {};
    const x = finite(source.x, 0);
    const y = finite(source.y, 0);
    const width = Math.max(0, finite(source.width, 1));
    const height = Math.max(0, finite(source.height, 1));
    const left = clamp(x, 0, 1);
    const top = clamp(y, 0, 1);
    return {
      x: left, y: top,
      width: Math.max(0, clamp(x + width, 0, 1) - left),
      height: Math.max(0, clamp(y + height, 0, 1) - top),
    };
  }

  /**
   * Analyze ImageData inside a normalized ROI (defaults to the whole image).
   * score is variance of the four-neighbour luminance Laplacian, in raw units.
   * brightness/contrast use 0..255 luminance; clippedDark/clippedBright are the
   * fractions at <=10 and >=245. Lighting checks are advisory exposure checks.
   * unusable samples still return finite statistics, with a reason and warning.
   * Keep the analysis canvas size fixed; reset the Meter after changing the
   * camera, ROI, resolution, lighting or exposure. Call at a modest rate (~5 Hz).
   */
  function analyze(imageData, roi) {
    const selection = clampROI(roi);
    const result = {
      score: 0, usable: false, reason: null, warning: null, lightingWarning: null,
      brightness: 0, contrast: 0, clippedDark: 0, clippedBright: 0,
      roi: selection, width: 0, height: 0, pixels: 0,
    };
    const fail = (reason, warning, lighting = false) => {
      result.reason = reason;
      result.warning = warning;
      if (lighting) result.lightingWarning = warning;
      return result;
    };
    const imageWidth = imageData?.width;
    const imageHeight = imageData?.height;
    const data = imageData?.data;
    const dataType = Object.prototype.toString.call(data);
    if (!Number.isInteger(imageWidth) || !Number.isInteger(imageHeight) ||
        imageWidth <= 0 || imageHeight <= 0 ||
        !['[object Uint8ClampedArray]', '[object Uint8Array]'].includes(dataType) ||
        data.length < imageWidth * imageHeight * 4) {
      return fail('invalid-frame', 'Waiting for a camera image.');
    }

    const left = Math.floor(selection.x * imageWidth);
    const top = Math.floor(selection.y * imageHeight);
    const right = Math.min(imageWidth, Math.ceil((selection.x + selection.width) * imageWidth));
    const bottom = Math.min(imageHeight, Math.ceil((selection.y + selection.height) * imageHeight));
    const width = selection.width ? right - left : 0;
    const height = selection.height ? bottom - top : 0;
    result.width = width;
    result.height = height;
    result.pixels = width * height;
    if (width < 8 || height < 8) {
      return fail('roi-too-small', 'Select a larger area for the focus meter.');
    }

    // Three rows avoid allocating another full camera frame on every sample.
    let previous = new Float64Array(width);
    let current = new Float64Array(width);
    let next = new Float64Array(width);
    let total = 0, squared = 0, dark = 0, bright = 0;
    let laplacianTotal = 0, laplacianSquared = 0, count = 0;
    for (let y = 0; y < height; y++) {
      let offset = ((top + y) * imageWidth + left) * 4;
      for (let x = 0; x < width; x++, offset += 4) {
        const value = 0.2126 * data[offset] + 0.7152 * data[offset + 1] + 0.0722 * data[offset + 2];
        next[x] = value;
        total += value;
        squared += value * value;
        if (value <= 10) dark++;
        if (value >= 245) bright++;
      }
      if (y >= 2) {
        for (let x = 1; x < width - 1; x++) {
          const laplacian = previous[x] + next[x] + current[x - 1] + current[x + 1] - 4 * current[x];
          laplacianTotal += laplacian;
          laplacianSquared += laplacian * laplacian;
          count++;
        }
      }
      const reusable = previous;
      previous = current;
      current = next;
      next = reusable;
    }

    const mean = total / result.pixels;
    result.brightness = clamp(mean, 0, 255);
    result.contrast = Math.sqrt(Math.max(0, squared / result.pixels - mean * mean));
    result.clippedDark = dark / result.pixels;
    result.clippedBright = bright / result.pixels;
    result.score = Math.max(0, laplacianSquared / count - (laplacianTotal / count) ** 2);

    // Do not interpret noise in an underexposed image as improved focus, or
    // missing detail in a washed-out image as a focus adjustment problem.
    if (mean < 30 || result.clippedDark > 0.75) {
      return fail('too-dark', 'Add more light or increase exposure before comparing focus.', true);
    }
    if (mean > 225 || result.clippedBright > 0.45) {
      return fail('too-bright', 'Reduce glare or exposure before comparing focus.', true);
    }
    if (result.clippedDark + result.clippedBright > 0.8) {
      return fail('clipped', 'Too much of this area is clipped to black or white. Adjust the lighting or select another area.', true);
    }
    if (result.contrast < 3) {
      return fail('low-detail', 'Choose an area with visible edges or texture.');
    }
    result.usable = true;
    return result;
  }

  /**
   * Smooth usable analyzer samples. push() returns a new snapshot containing
   * raw/current/best and relative (0..1 of this session's smoothed best).
   * A relative value of 1 means "session best", never "perfect focus".
   * Rejected samples preserve history; show their warning instead of a verdict.
   */
  class Meter {
    constructor({ alpha = 0.25 } = {}) {
      this.alpha = Number.isFinite(alpha) && alpha > 0 && alpha <= 1 ? alpha : 0.25;
      this.reset();
    }

    reset() {
      this.current = 0;
      this.best = 0;
      this.samples = 0;
      return this.snapshot(0, false, 'unavailable', null, null);
    }

    snapshot(raw, usable, trend, warning, lightingWarning) {
      return {
        raw, current: this.current, best: this.best,
        relative: this.best > 0 ? clamp(this.current / this.best, 0, 1) : 0,
        trend, usable, warning, lightingWarning, samples: this.samples,
      };
    }

    push(sample) {
      const score = sample?.score;
      if (!sample?.usable || !Number.isFinite(score) || score < 0) {
        return this.snapshot(Number.isFinite(score) ? Math.max(0, score) : 0, false, 'unavailable',
          sample?.warning || sample?.lightingWarning || 'Waiting for a usable focus sample.',
          sample?.lightingWarning || null);
      }
      const previous = this.current;
      this.current = this.samples ? this.alpha * score + (1 - this.alpha) * previous : score;
      this.best = Math.max(this.best, this.current);
      const tolerance = 0.02 * Math.max(previous, this.current, 1);
      const change = this.current - previous;
      const trend = !this.samples || Math.abs(change) <= tolerance ? 'steady'
        : change > 0 ? 'improving' : 'declining';
      this.samples++;
      return this.snapshot(score, true, trend, null, null);
    }
  }

  window.OcheCameraFocus = Object.freeze({ analyze, clampROI, Meter });
})();
