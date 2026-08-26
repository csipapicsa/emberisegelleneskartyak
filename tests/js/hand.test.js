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

test('cards still in hand keep their angle when the next round is dealt', () => {
  const { hand, track } = loadHand();
  const cards = deal(6);
  hand.setCards(cards);
  const before = cards.map(c => hand._tiltFor(c.id));

  // What the server actually sends each round: the same hand minus the card
  // that was played, plus one freshly drawn replacement.
  const kept = cards.slice(1);
  const nextHand = [...kept, { id: 999, text: 'drawn' }];
  hand.setCards(nextHand);

  assert.deepEqual(
    kept.map(c => hand._tiltFor(c.id)),
    before.slice(1),
    'untouched cards were re-rolled — the fan reshuffled itself between rounds',
  );
  assert.deepEqual(kept.map(c => track.cardTilt(c.id)),
    before.slice(1).map(rendered), 'rendered angles moved too');
});

test('a freshly drawn card gets an angle of its own', () => {
  const { hand, track, maxTilt } = loadHand();
  hand.setCards(deal(4));
  hand.setCards([...deal(4), { id: 999, text: 'drawn' }]);

  const t = hand._tiltFor(999);
  assert.notEqual(t, null);
  assert.ok(Math.abs(t) <= maxTilt);
  assert.equal(track.cardTilt(999), rendered(t));
});

test('a card that leaves the hand is forgotten, so the map stays bounded', () => {
  const { hand } = loadHand();
  hand.setCards(deal(5));
  assert.equal(Object.keys(hand._tilts).length, 5);

  for (let round = 0; round < 50; round++) {
    hand.setCards(deal(5).map(c => ({ ...c, id: c.id + round * 100 })));
  }
  assert.ok(Object.keys(hand._tilts).length <= 6,
    `tilt map grew to ${Object.keys(hand._tilts).length} entries`);
});

test('a completely new hand is laid out afresh', () => {
  const { hand } = loadHand();
  hand.setCards(deal(4));
  const before = deal(4).map(c => hand._tiltFor(c.id));

  hand.setCards(deal(4).map(c => ({ ...c, id: c.id + 500 })));   // all new ids
  const after = deal(4).map((c, i) => hand._tiltFor(c.id + 500));

  assert.notDeepEqual(after, before);
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

// ── mouse pickup feel (desktop only) ─────────────────────────────────────────

test('a card the mouse leaves is set back down at a new angle', () => {
  const { hand, track } = loadHand();
  const cards = deal(5);
  hand.setCards(cards);
  const before = cards.map(c => hand._tiltFor(c.id));

  hand._onCardLeave(String(cards[2].id));

  const after = cards.map(c => hand._tiltFor(c.id));
  assert.notEqual(after[2], before[2], 'the brushed card kept its old angle');
  assert.deepEqual(
    after.filter((_, i) => i !== 2),
    before.filter((_, i) => i !== 2),
    'other cards moved',
  );
  assert.equal(track.cardTilt(cards[2].id), rendered(after[2]), 'the new angle was rendered');
});

test('a held (selected) card keeps its angle when the mouse leaves it', () => {
  const { hand } = loadHand();
  const cards = deal(5);
  hand.setPickCount(2);
  hand.setCards(cards);

  hand._confirmCard(1);                        // now holding it
  const held = hand._tiltFor(cards[1].id);

  hand._onCardLeave(String(cards[1].id));

  assert.equal(hand._tiltFor(cards[1].id), held,
    'a card still in your hand should not be re-laid on the table');
});

test('leaving a card does nothing once the hand is submitted', () => {
  const { hand } = loadHand();
  const cards = deal(4);
  hand.setCards(cards);
  const before = hand._tiltFor(cards[0].id);

  hand.submitted = true;
  hand._onCardLeave(String(cards[0].id));
  assert.equal(hand._tiltFor(cards[0].id), before);

  hand.submitted = false;
  hand.disabled = true;
  hand._onCardLeave(String(cards[0].id));
  assert.equal(hand._tiltFor(cards[0].id), before);
});

test('the action card has no id, so leaving it is a no-op', () => {
  const { hand } = loadHand();
  hand.setAction(true);
  hand.setCards(deal(3));
  const before = { ...hand._tilts };

  hand._onCardLeave(undefined);

  assert.deepEqual(hand._tilts, before);
});

test('the mouse handler is wired up on a desktop pointer', () => {
  const { hand, track } = loadHand();           // finePointer defaults to true
  const cards = deal(4);
  hand.setCards(cards);
  assert.equal(track.handlerCount('mouseout'), 1, 'no mouseout listener bound');

  const before = hand._tiltFor(cards[1].id);
  const card = track._els.find(e => e.dataset.id === String(cards[1].id));
  track.dispatch('mouseout', { target: card, relatedTarget: null });

  assert.notEqual(hand._tiltFor(cards[1].id), before, 'the event did not reach _onCardLeave');
});

test('moving within the same card does not re-roll it', () => {
  const { hand, track } = loadHand();
  const cards = deal(4);
  hand.setCards(cards);

  const before = hand._tiltFor(cards[1].id);
  const card = track._els.find(e => e.dataset.id === String(cards[1].id));
  // relatedTarget still inside the card — the pointer never actually left.
  track.dispatch('mouseout', { target: card, relatedTarget: card });

  assert.equal(hand._tiltFor(cards[1].id), before);
});

test('no mouse handler is bound on a touchscreen', () => {
  const { hand, track } = loadHand({ finePointer: false });
  hand.setCards(deal(4));
  assert.equal(track.handlerCount('mouseout'), 0,
    'a touchscreen must not get the mouse-only behaviour');
});

// ── coming back down from the lifted state ───────────────────────────────────

test('a lifted card that drops back on its own lands at a new angle', () => {
  const { hand, track, runTimers, tapCard } = loadHand();
  const cards = deal(5);
  hand.setCards(cards);

  tapCard(2);                                  // a real tap, which lifts the card
  assert.equal(hand._activeIdx, 2, 'the tap did not lift the card');
  const whileUp = hand._tiltFor(cards[2].id);

  runTimers();                                 // hold timer expires, it drops back

  assert.equal(hand._activeIdx, -1, 'the card should no longer be lifted');
  assert.notEqual(hand._tiltFor(cards[2].id), whileUp,
    'it fell back into exactly the same position');
  assert.equal(track.cardTilt(cards[2].id), rendered(hand._tiltFor(cards[2].id)));
});

test('lifting a different card sets the previous one back down at a new angle', () => {
  const { hand } = loadHand();
  const cards = deal(5);
  hand.setCards(cards);

  hand._setActive(1);
  const first = hand._tiltFor(cards[1].id);
  const untouched = hand._tiltFor(cards[4].id);

  hand._setActive(3);                          // attention moves to another card

  assert.notEqual(hand._tiltFor(cards[1].id), first, 'the card left behind kept its angle');
  assert.equal(hand._tiltFor(cards[4].id), untouched, 'an uninvolved card moved');
});

test('re-asserting the same active card does not re-roll it', () => {
  const { hand } = loadHand();
  const cards = deal(4);
  hand.setCards(cards);

  hand._setActive(2);
  const t = hand._tiltFor(cards[2].id);
  // What the drag loop does every frame while the finger stays on one card.
  for (let frame = 0; frame < 60; frame++) hand._setActive(2);

  assert.equal(hand._tiltFor(cards[2].id), t, 'the lifted card twitched while held');
});

test('dragging across the fan sets each card down once as it is left', () => {
  const { hand } = loadHand();
  const cards = deal(5);
  hand.setCards(cards);
  const before = cards.map(c => hand._tiltFor(c.id));

  hand._setActive(0);
  hand._setActive(1);
  hand._setActive(2);
  const afterPass = cards.map(c => hand._tiltFor(c.id));

  assert.notEqual(afterPass[0], before[0], 'card 0 was passed over but not re-laid');
  assert.notEqual(afterPass[1], before[1], 'card 1 was passed over but not re-laid');
  assert.equal(afterPass[2], before[2], 'card 2 is still lifted, so it keeps its angle');
  assert.equal(afterPass[3], before[3], 'card 3 was never touched');

  // Still holding card 2: hovering it again must not disturb the ones already down.
  const settled = cards.map(c => hand._tiltFor(c.id));
  for (let i = 0; i < 10; i++) hand._setActive(2);
  assert.deepEqual(cards.map(c => hand._tiltFor(c.id)), settled);
});

test('a held card is not re-laid when it stops being the lifted one', () => {
  const { hand } = loadHand();
  const cards = deal(5);
  hand.setPickCount(2);
  hand.setCards(cards);

  hand._confirmCard(1);                        // selected — in your hand now
  const held = hand._tiltFor(cards[1].id);

  hand._setActive(1);
  hand._setActive(3);                          // attention leaves the held card

  assert.equal(hand._tiltFor(cards[1].id), held,
    'a card you are holding should keep its angle until you put it back');
});
