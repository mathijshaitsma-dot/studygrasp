import test from 'node:test';
import assert from 'node:assert/strict';
import { wheelZoomFactor, attachSlideGestures } from '../frontend/js/slide-gestures.js';

function surface(scale = 2) {
  const holder = new EventTarget();
  holder.scrollLeft = 100; holder.scrollTop = 200;
  const classes = new Set();
  holder.classList = { add: name => classes.add(name), remove: name => classes.delete(name) };
  holder.setPointerCapture = () => {};
  const image = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 800 }) };
  const zooms = [], pages = [];
  attachSlideGestures(holder, image, { getScale: () => scale, setScale: (...args) => zooms.push(args), navigate: delta => pages.push(delta) });
  const send = (type, values) => {
    const event = new Event(type, { cancelable: true });
    Object.assign(event, { pointerId: 1, pointerType: 'mouse', button: 0, clientX: 200, clientY: 200 }, values);
    holder.dispatchEvent(event);
    return event;
  };
  return { holder, classes, zooms, pages, send };
}

test('wheel sensitivity matches college slides across pixel, line and page devices', () => {
  const factor = wheelZoomFactor({ deltaY: -100, deltaMode: 0 });
  assert.ok(Math.abs(factor - Math.exp(0.28)) < 1e-10);
  assert.ok(Math.abs(factor - wheelZoomFactor({ deltaY: -100 / 24, deltaMode: 1 })) < 1e-10);
  assert.ok(Math.abs(factor - wheelZoomFactor({ deltaY: -0.5, deltaMode: 2 })) < 1e-10);
  assert.ok(Math.abs(factor * wheelZoomFactor({ deltaY: 100, deltaMode: 0 }) - 1) < 1e-10);
});

test('modified wheel zooms around cursor while normal wheel stays native', () => {
  const s = surface();
  assert.equal(s.send('wheel', { deltaY: -100, deltaMode: 0 }).defaultPrevented, false);
  assert.equal(s.zooms.length, 0);
  assert.equal(s.send('wheel', { ctrlKey: true, deltaY: -100, deltaMode: 0, clientX: 320, clientY: 280 }).defaultPrevented, true);
  assert.deepEqual(s.zooms[0], [2 * Math.exp(0.28), 320, 280]);
});

test('drag pans both axes and stops immediately after release or cancellation', () => {
  const s = surface();
  s.send('pointerdown');
  s.send('pointermove', { clientX: 120, clientY: 180 });
  assert.equal(s.holder.scrollLeft, 180); assert.equal(s.holder.scrollTop, 220);
  assert.ok(s.classes.has('panning'));
  s.send('pointerup');
  s.send('pointermove', { clientX: 60, clientY: 50 });
  assert.equal(s.holder.scrollLeft, 180); assert.equal(s.holder.scrollTop, 220);
  assert.equal(s.classes.has('panning'), false);
  s.send('pointerdown'); s.send('pointercancel');
  s.send('pointermove', { clientX: 0, clientY: 0 });
  assert.equal(s.holder.scrollLeft, 180);
});

test('unzoomed touch swipe changes page while cancelled gestures never navigate', () => {
  const s = surface(1);
  s.send('pointerdown', { pointerType: 'touch' });
  s.send('pointermove', { pointerType: 'touch', clientX: 120 });
  s.send('pointerup', { pointerType: 'touch' });
  assert.deepEqual(s.pages, [1]);
  s.send('pointerdown', { pointerType: 'touch' });
  s.send('pointermove', { pointerType: 'touch', clientX: 120 });
  s.send('pointercancel', { pointerType: 'touch' });
  assert.deepEqual(s.pages, [1]);
});
