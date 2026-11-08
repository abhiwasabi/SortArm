import test from 'node:test';
import assert from 'node:assert/strict';
import {averageCycle, binRows, cardStatus, formatSeconds, minuteSeries, modeNote} from './tiger.mjs';

test('card status covers off, offline, simulated, and physical', () => {
  assert.equal(cardStatus({enabled: false}).badge, 'OFF');
  assert.equal(cardStatus(null).tone, 'offline');
  assert.equal(cardStatus({enabled: true, ok: false, error: 'down'}).message, 'down');
  assert.equal(cardStatus({enabled: true, ok: true, simulated: true}).badge, 'SIMULATED');
  assert.equal(cardStatus({enabled: true, ok: true, simulated: false}).badge, 'PHYSICAL');
});

test('mode note names the mode shown and the rows kept out', () => {
  assert.match(modeNote({simulated: true, other_mode_rows: 0}), /not physical drops\.$/);
  assert.match(modeNote({simulated: true, other_mode_rows: 2}), /2 physical rows are stored separately/);
  assert.match(modeNote({simulated: false, other_mode_rows: 1}), /^Physical arm sorts only\. 1 simulated row is/);
});

test('minute series fills empty minutes and ends at the current minute', () => {
  const now = Date.parse('2026-11-08T01:30:40Z');
  const series = minuteSeries([{bucket: '2026-11-08T01:29:00+00:00', done: 2, stopped: 1, fault: 0}], 5, now);
  assert.equal(series.length, 5);
  assert.equal(series.at(-1).t, Date.parse('2026-11-08T01:30:00Z'));
  assert.deepEqual(series.map(s => s.done), [0, 0, 0, 2, 0]);
  assert.equal(series[3].stopped, 1);
});

test('bin rows keep recycle then trash and fill a missing bin', () => {
  const rows = binRows([{bin: 'TRASH', done: 3, stopped: 0, fault: 1, avg_cycle_s: 12}]);
  assert.deepEqual(rows.map(r => r.bin), ['RECYCLE', 'TRASH']);
  assert.equal(rows[1].done, 3);
  assert.equal(rows[0].done, 0);
});

test('average cycle weights by completed sorts', () => {
  assert.equal(averageCycle([{done: 1, avg_cycle_s: 10}, {done: 3, avg_cycle_s: 20}, {done: 0, avg_cycle_s: null}]), 17.5);
  assert.equal(averageCycle([{done: 0, avg_cycle_s: null}]), null);
  assert.equal(formatSeconds(17.5), '17.5 s');
  assert.equal(formatSeconds(null), '—');
});
