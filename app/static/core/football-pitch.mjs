// A continuous radial stretch: the 45° sector boundaries become rectangle corners.
export const ORDER = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5];
export const RINGS = { B: [0, 6.35], OB: [6.35, 15.9], I: [15.9, 99], T: [99, 107], O: [107, 162], D: [162, 170] };
export const CELLS = ["B", "OB", ...ORDER.flatMap(number => ["I", "T", "O", "D"].map(ring => ring + number))];

// Widen the narrow scoring bands for readable/tappable cells; radial order is unchanged.
export function displayRadius(radius) {
  const physical = [0, 6.35, 15.9, 99, 107, 162, 170];
  const visual = [0, .055, .13, .52, .65, .88, 1];
  for (let i = 1; i < physical.length; i++) if (radius <= physical[i]) {
    return visual[i - 1] + (visual[i] - visual[i - 1]) * (radius - physical[i - 1]) / (physical[i] - physical[i - 1]);
  }
  return radius / 170;
}

export function pitchPoint(radius, degrees, morph = 1) {
  const angle = degrees * Math.PI / 180, x = Math.sin(angle), y = -Math.cos(angle);
  const edge = Math.max(Math.abs(x), Math.abs(y));
  const amount = Math.max(0, Math.min(1, morph)), r = displayRadius(radius);
  return [500 + r * x * (270 * (1 - amount) + 450 * amount / edge),
    320 + r * y * (270 * (1 - amount) + 270 * amount / edge)];
}

export function cellPosition(region, morph = 1) {
  if (region === "B") return [500, 320];
  if (region === "OB") return pitchPoint(11, 0, morph);
  const [inner, outer] = RINGS[region[0]];
  return pitchPoint((inner + outer) / 2, ORDER.indexOf(Number(region.slice(1))) * 18, morph);
}

export function cellOutline(region, morph = 1) {
  const bull = region === "B" || region === "OB";
  const [inner, outer] = RINGS[bull ? region : region[0]];
  const centre = bull ? 0 : ORDER.indexOf(Number(region.slice(1))) * 18;
  const start = bull ? -9 : centre - 9, end = bull ? 351 : centre + 9;
  const arc = radius => Array.from({ length: Math.round((end - start) / 1.5) + 1 }, (_, i) => pitchPoint(radius, start + i * 1.5, morph));
  // Identical angular samples on shared edges avoid seams throughout the morph.
  return [...arc(outer), ...arc(inner).reverse()];
}

export function cellPath(region, morph = 1) {
  const points = cellOutline(region, morph);
  const path = values => values.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(3)},${y.toFixed(3)}`).join(" ") + " Z";
  if (region === "B" || region === "OB") {
    const half = points.length / 2;
    return path(points.slice(0, half)) + (region === "OB" ? " " + path(points.slice(half)) : "");
  }
  return path(points);
}

export function cellDart(region) {
  if (region === "B" || region === "OB") return { number: 25, multiplier: region === "B" ? 2 : 1 };
  return { number: Number(region.slice(1)), multiplier: { I: 1, T: 3, O: 1, D: 2 }[region[0]],
    bed: region[0] === "I" ? "inner" : region[0] === "O" ? "outer" : null };
}


export function goalNet(side, morph = 1) {
  const angle = side === "right" ? 90 : 270, direction = side === "right" ? 1 : -1;
  const points = [pitchPoint(170, angle - 9, morph), pitchPoint(170, angle + 9, morph)];
  const top = Math.min(points[0][1], points[1][1]), bottom = Math.max(points[0][1], points[1][1]);
  const front = points[0][0], back = front + direction * 38;
  let path = `M${front},${top} H${back} V${bottom} H${front}`;
  for (let i = 1; i < 5; i++) {
    const x = front + direction * 38 * i / 5, y = top + (bottom - top) * i / 5;
    path += ` M${x},${top} V${bottom} M${front},${y} H${back}`;
  }
  return path;
}
