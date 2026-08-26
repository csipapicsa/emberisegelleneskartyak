// Tests for the hand-dealt card tilt.
//
// The point of the feature is that the fan looks laid out by a person. The
// point of these tests is that it does NOT look broken: a card holds its angle
// until it is actually handled, so nothing twitches while you drag the hand.
const test = require('node:test');
const assert = require('node:assert');
const { loadHand } = require('./harness');

const deal = n => Array.from({ length: n }, (_, i) => ({ id: 100 + i, text: `card ${i}` }));

function tiltsOf(track, cards) {
  return cards.map(c => track.cardTilt(c.id));
}

// The inline style is written with two decimals, so compare like for like.
const rendered = v => Number(v.toFixed(2));

test('every dealt card is rendered with an angle inside the configured range', () => {
  const { hand, track, maxTilt } = loadHand();
  const cards = deal(8);
  hand.setCards(cards);

  const tilts = tiltsOf(track, cards);
  assert.equal(tilts.length, 8);
  for (const t of tilts) {
    assert.notEqual(t, null, 'card rendered without a --tilt');
    assert.ok(Math.abs(t) <= maxTilt, `${t}deg exceeds the ${maxTilt}deg limit`);
  }
});

test('the action card is tilted too, so it does not sit conspicuously square', () => {
  const { hand, track, maxTilt } = loadHand();
  hand.setAction(true);
  hand.setCards(deal(3));

  const t = track.actionTilt();
  assert.notEqual(t, null);
  assert.ok(Math.abs(t) <= maxTilt);
});

test('a card holds its angle when read repeatedly', () => {
  const { hand } = loadHand();
  hand.setCards(deal(4));

  const first = hand._tiltFor(101);
  for (let i = 0; i < 20; i++) {
    assert.equal(hand._tiltFor(101), first, 'angle drifted between reads');
  }
});

test('class updates never re-roll angles — the fan must not vibrate during a drag', () => {
  const { hand, track } = loadHand();
  const cards = deal(6);
  hand.setCards(cards);

  const before = cards.map(c => hand._tiltFor(c.id));

  // _updateClasses() is what the requestAnimationFrame drag loop calls on every
  // frame. Re-rolling in there was the trap this guards against.
  for (let frame = 0; frame < 60; frame++) hand._updateClasses();

  assert.deepEqual(cards.map(c => hand._tiltFor(c.id)), before,
    'angles changed without the card being handled');
  assert.deepEqual(tiltsOf(track, cards), before.map(rendered), 'rendered angles changed too');
});

test('picking a card up gives it a new angle, and leaves its neighbours alone', () => {
  const { hand } = loadHand();
  const cards = deal(5);
  hand.setPickCount(2);          // keeps the confirm modal out of this test
  hand.setCards(cards);

  const before = cards.map(c => hand._tiltFor(c.id));
  hand._confirmCard(2);          // tap to select

  const after = cards.map(c => hand._tiltFor(c.id));
  assert.notEqual(after[2], before[2], 'the handled card kept its old angle');
  assert.deepEqual(
    after.filter((_, i) => i !== 2),
    before.filter((_, i) => i !== 2),
    'untouched cards moved',
  );
});

test('putting a card back down gives it another new angle', () => {
  const { hand, track } = loadHand();
  const cards = deal(5);
  hand.setPickCount(2);
  hand.setCards(cards);

  hand._confirmCard(1);                       // pick up
  const whileHeld = hand._tiltFor(101);
  assert.equal(track.cardTilt(101), rendered(whileHeld), 'rendered angle followed the pickup');

  hand._confirmCard(1);                       // put back down
  assert.deepEqual(hand.selected, [], 'card was actually deselected');
  assert.notEqual(hand._tiltFor(101), whileHeld, 'it landed at exactly the same angle');
  assert.equal(track.cardTilt(101), rendered(hand._tiltFor(101)));
});

test('a bumped-out selection is put down too when the pick limit is reached', () => {
  const { hand } = loadHand();
  const cards = deal(5);
  hand.setPickCount(1);
  hand.setCards(cards);

  hand.selected = [cards[0].id];              // already holding one
  const bumpedBefore = hand._tiltFor(cards[0].id);

  hand._confirmCard(3);                       // choosing another replaces it

  assert.deepEqual(hand.selected, [cards[3].id]);
  assert.notEqual(hand._tiltFor(cards[0].id), bumpedBefore,
    'the displaced card should land at a new angle as well');
});

test('a new deal lays every card out afresh', () => {
  const { hand } = loadHand();
  const cards = deal(4);
  hand.setCards(cards);
  const before = cards.map(c => hand._tiltFor(c.id));

  hand.setCards(cards);                       // same ids, dealt again
  const after = cards.map(c => hand._tiltFor(c.id));

  assert.notDeepEqual(after, before, 'angles were carried over from the previous hand');
});

test('the fan is not uniform — cards get genuinely different angles', () => {
  const { hand } = loadHand();
  const cards = deal(12);
  hand.setCards(cards);

  const unique = new Set(cards.map(c => hand._tiltFor(c.id).toFixed(4)));
  assert.ok(unique.size >= 10, `expected varied angles, got ${unique.size} distinct of 12`);
});

test('angles stay within range across many deals', () => {
  const { hand, maxTilt } = loadHand();
  for (let round = 0; round < 200; round++) {
    const cards = deal(10);
    hand.setCards(cards);
    for (const c of cards) {
      const t = hand._tiltFor(c.id);
      assert.ok(Math.abs(t) <= maxTilt, `${t}deg out of range`);
    }
  }
});
