/* Display helpers for local games. Scoring stays in the service. */

const TRIPLES = Array.from({ length: 20 }, (_, index) => ({ name: `T${20 - index}`, value: 3 * (20 - index), double: false }));
const SINGLES = [
  ...Array.from({ length: 20 }, (_, index) => ({ name: `${20 - index}`, value: 20 - index, double: false })),
  { name: "25", value: 25, double: false },
];
// Common finishing doubles first, then the rest from high to low.
const DOUBLES = [20, 16, 18, 12, 10, 8, 19, 17, 15, 14, 13, 11, 9, 7, 6, 5, 4, 3, 2, 1]
  .map(number => ({ name: `D${number}`, value: 2 * number, double: true }))
  .concat({ name: "Bull", value: 50, double: true });
// Setup darts prefer trebles, then singles, then doubles.
const SETUP = [...TRIPLES, ...SINGLES, ...DOUBLES];

function setup(value, darts) {
  for (const dart of SETUP) {
    if (darts === 1) {
      if (dart.value === value) return [dart.name];
    } else if (dart.value < value) {
      const rest = setup(value - dart.value, darts - 1);
      if (rest) return [dart.name, ...rest];
    }
  }
  return null;
}

function finish(remaining, darts, doubleOut) {
  // Choose the finishing dart first so routes end on preferred doubles.
  for (const last of doubleOut ? DOUBLES : SETUP) {
    if (last.value === remaining && darts === 1) return [last.name];
    if (darts > 1 && last.value < remaining) {
      const route = setup(remaining - last.value, darts - 1);
      if (route) return [...route, last.name];
    }
  }
  return null;
}

/** Suggest the shortest finish for an X01 score, or null when none fits. */
export function checkout(remaining, darts = 3, doubleOut = true) {
  if (!Number.isInteger(remaining) || remaining < 1 || remaining > 180 || darts < 1) return null;
  for (let count = 1; count <= Math.min(darts, 3); count++) {
    const route = finish(remaining, count, doubleOut);
    if (route) return route;
  }
  return null;
}

/** Three-dart average and last visit per player, from completed visits the service retains. */
export function playerStats(frame) {
  return frame.players.map(player => {
    const turns = frame.turns.filter(turn => turn.playerId === player.id && turn.throws.length && turn.finishedAt);
    const darts = turns.reduce((total, turn) => total + turn.throws.length, 0);
    const points = turns.reduce((total, turn) => total + (turn.busted ? 0 : turn.points), 0);
    const last = turns[0];
    return {
      average: darts ? (points / darts) * 3 : null,
      last: last ? (last.busted ? null : last.points) : undefined,
      busted: Boolean(last?.busted),
    };
  });
}

const ADJECTIVES = ["Lucky", "Sneaky", "Fluffy", "Grumpy", "Turbo", "Wobbly", "Sleepy", "Cheeky", "Mighty", "Dizzy",
  "Jolly", "Fancy", "Spicy", "Bouncy", "Sassy", "Clumsy", "Brave", "Zippy", "Funky", "Cosmic"];
const ANIMALS = ["Otter", "Badger", "Llama", "Penguin", "Walrus", "Hedgehog", "Panda", "Alpaca", "Moose", "Gecko",
  "Narwhal", "Koala", "Raccoon", "Flamingo", "Yak", "Platypus", "Sloth", "Ferret", "Puffin", "Capybara"];
export const BOT_PREFIX = "[Bot] ";

/** A playful default name that no listed player uses yet; bots get a [Bot] prefix. */
export function funnyName(taken = [], bot = false, random = Math.random) {
  const used = new Set(taken.map(name => name.replace(BOT_PREFIX, "").trim()));
  const names = ADJECTIVES.flatMap(adjective => ANIMALS.map(animal => `${adjective} ${animal}`));
  const free = names.filter(name => !used.has(name));
  const pool = free.length ? free : names;
  const name = pool[Math.floor(random() * pool.length)];
  return bot ? `${BOT_PREFIX}${name}` : name;
}

/* Dartboard drawing: 100 units is the outer edge of the double ring, as in dart coordinates. */
export const BOARD_ORDER = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5];
const MM = value => Number((value / 170 * 100).toFixed(2));
const RING = { bull: MM(6.35), outerBull: MM(15.9), tripleIn: MM(99), tripleOut: MM(107), doubleIn: MM(162), doubleOut: 100 };

function polar(radius, degrees) {
  const angle = degrees * Math.PI / 180;
  return [radius * Math.sin(angle), -radius * Math.cos(angle)].map(value => Number(value.toFixed(2)));
}

function sector(inner, outer, from, to) {
  const [ax, ay] = polar(outer, from), [bx, by] = polar(outer, to);
  const [cx, cy] = polar(inner, to), [dx, dy] = polar(inner, from);
  return `M${ax} ${ay}A${outer} ${outer} 0 0 1 ${bx} ${by}L${cx} ${cy}A${inner} ${inner} 0 0 0 ${dx} ${dy}Z`;
}

/** Static SVG content for the board; segments carry data-hit keys such as T20, S25 or D25.
    Wires are drawn once over the beds, as rings and spokes, like OcheCore's live board. */
export function boardMarkup() {
  // OcheCore's proportions: a 222 mm surround with the numbers at 199 mm.
  const parts = [`<circle r="${MM(222)}" class="board-surround"/>`];
  BOARD_ORDER.forEach((number, index) => {
    const from = index * 18 - 9, to = from + 18;
    const [single, ring] = index % 2 ? ["light", "green"] : ["dark", "red"];
    parts.push(
      `<path d="${sector(RING.outerBull, RING.tripleIn, from, to)}" class="board-${single}" data-hit="S${number}" data-region="I${number}"/>`,
      `<path d="${sector(RING.tripleIn, RING.tripleOut, from, to)}" class="board-${ring}" data-hit="T${number}" data-region="T${number}"/>`,
      `<path d="${sector(RING.tripleOut, RING.doubleIn, from, to)}" class="board-${single}" data-hit="S${number}" data-region="O${number}"/>`,
      `<path d="${sector(RING.doubleIn, RING.doubleOut, from, to)}" class="board-${ring}" data-hit="D${number}" data-region="D${number}"/>`,
    );
  });
  parts.push(`<circle r="${RING.outerBull}" class="board-green" data-hit="S25" data-region="OB"/>`,
    `<circle r="${RING.bull}" class="board-red" data-hit="D25" data-region="B"/>`);
  const wires = Object.values(RING).map(radius => `<circle r="${radius}"/>`);
  BOARD_ORDER.forEach((_, index) => {
    const [ax, ay] = polar(RING.outerBull, index * 18 - 9), [bx, by] = polar(RING.doubleOut, index * 18 - 9);
    wires.push(`<path d="M${ax} ${ay}L${bx} ${by}"/>`);
  });
  parts.push(`<g class="board-wires">${wires.join("")}</g>`);
  BOARD_ORDER.forEach((number, index) => {
    const [x, y] = polar(MM(199), index * 18);
    parts.push(`<text x="${x}" y="${y}" class="board-number">${number}</text>`);
  });
  parts.push('<g class="board-darts"></g>');
  return parts.join("");
}

/** The data-hit key of a scored segment, or null for a miss. */
export function hitKey(segment) {
  if (!segment?.number || !segment.multiplier) return null;
  return `${"SDT"[segment.multiplier - 1]}${segment.number}`;
}

/** SVG position of dart coordinates (y up), kept inside the drawn board. */
export function boardPoint(coords) {
  const x = coords.x * 100, y = -coords.y * 100;
  const scale = Math.min(1, 120 / (Math.hypot(x, y) || 1));
  return [Number((x * scale).toFixed(2)), Number((y * scale).toFixed(2))];
}

/* Seats players clockwise around the board, as player indexes listed top to bottom per side:
   player 1 at the top left, then down the right side and back up the left. */
export function seats(count) {
  if (count < 2) return { left: count ? [0] : [], right: [] };
  const right = Math.ceil(count / 2);
  return {
    left: [0, ...Array.from({ length: count - 1 - right }, (_, index) => count - 1 - index)],
    right: Array.from({ length: right }, (_, index) => index + 1),
  };
}
