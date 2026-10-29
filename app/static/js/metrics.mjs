// Timestamps preserve real spacing when sampling settings change or samples fail.
export function metricSeries(samples, fields, seconds, now, ceiling = null) {
  const recent = samples.filter(sample => sample.timestamp >= now - seconds && sample.timestamp <= now);
  const finite = recent.flatMap(sample => fields.map(key => sample[key]).filter(Number.isFinite));
  const maximum = ceiling ?? Math.max(1, ...finite);
  return fields.map(key => ({ key, points: recent.filter(sample => Number.isFinite(sample[key])).map(sample => ({
    x: (sample.timestamp - (now - seconds)) / seconds * 100,
    y: 38 - Math.max(0, Math.min(maximum, sample[key])) / maximum * 36,
    value: sample[key], timestamp: sample.timestamp,
  })) }));
}
