import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseCameraDevice} from './camera-choice.mjs';
const phone = {id:'phone',name:'Abhi’s iPhone Camera'};
const laptop = {id:'laptop',name:'MacBook Air Camera'};
test('single dropdown resolves phone and laptop separately regardless of order', () => {
  assert.equal(chooseCameraDevice([phone,laptop],'usb'),laptop);
  assert.equal(chooseCameraDevice([laptop,phone],'iphone'),phone);
});
test('missing selected camera never silently selects the other camera', () => {
  assert.equal(chooseCameraDevice([phone],'usb'),null);
  assert.equal(chooseCameraDevice([laptop],'iphone'),null);
});
