#!/usr/bin/env node
/*
 * Compare static/js/nova_astro.js with the Python reference fixture.
 *
 *   node tools/compare_astro_engine.js            summary, exit code 1 on failure
 *   node tools/compare_astro_engine.js --verbose  also list the worst cases
 *
 * The fixture is built by tools/gen_astro_fixture.py from modules/astro_calculations.py.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const A = require(path.join(__dirname, '..', 'static', 'js', 'nova_astro.js'));

const verbose = process.argv.includes('--verbose');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'tests', 'fixtures', 'astro_reference.json'), 'utf8'));

// Pass marks
const LIMITS = {
    position: 0.05,      // degrees on the sky, object alt/az
    sunMinutes: 2,       // dusk, dawn, sunrise, sunset, transit
    moonPosition: 0.1,   // degrees on the sky
    moonSep: 1.0,        // degrees
    moonPhase: 1.0,      // percentage points
    durationSteps: 1,    // sampling steps
    maxAlt: 0.1,         // degrees
};

const DEG = Math.PI / 180;
function skyDistance(alt1, az1, alt2, az2) {
    const c = Math.sin(alt1 * DEG) * Math.sin(alt2 * DEG)
        + Math.cos(alt1 * DEG) * Math.cos(alt2 * DEG) * Math.cos((az1 - az2) * DEG);
    return Math.acos(Math.min(1, Math.max(-1, c))) / DEG;
}
const utcMs = s => Date.parse(s + 'Z');
const hmToMin = s => +s.slice(0, 2) * 60 + +s.slice(3, 5);
function minuteDiff(a, b) {
    let d = Math.abs(hmToMin(a) - hmToMin(b));
    return d > 720 ? 1440 - d : d;
}

class Stat {
    constructor(name, unit, limit) { this.name = name; this.unit = unit; this.limit = limit; this.v = []; this.worst = null; }
    add(x, label) {
        this.v.push(x);
        if (!this.worst || x > this.worst.x) this.worst = { x, label };
    }
    get max() { return this.v.reduce((a, b) => Math.max(a, b), 0); }
    get median() { const s = [...this.v].sort((a, b) => a - b); return s.length ? s[s.length >> 1] : 0; }
    get p99() { const s = [...this.v].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length * 0.99)] : 0; }
    get over() { return this.v.filter(x => x > this.limit).length; }
}

const stats = {
    position: new Stat('Object position (alt/az)', 'deg', LIMITS.position),
    sun: new Stat('Sun events', 'min', LIMITS.sunMinutes),
    moonPos: new Stat('Moon position', 'deg', LIMITS.moonPosition),
    moonSep: new Stat('Moon separation', 'deg', LIMITS.moonSep),
    moonPhase: new Stat('Moon phase', '%', LIMITS.moonPhase),
    duration: new Stat('Observable duration', 'steps', LIMITS.durationSteps),
    maxAlt: new Stat('Max altitude tonight', 'deg', LIMITS.maxAlt),
    fromTo: new Stat('Observable from / to', 'steps', LIMITS.durationSteps),
};
let naMismatch = [], sunExact = 0, sunTotal = 0, durExact = 0, durTotal = 0, gridMismatch = 0, sepRoundSame = 0, sepTotal = 0;
const SUN_KEYS = ['astronomical_dawn', 'sunrise', 'transit', 'sunset', 'astronomical_dusk'];

for (const c of fixture.cases) {
    const tag = `${c.location} ${c.date}`;

    // Sun events
    const ev = A.sunEvents(c.date, c.tz, c.lat, c.lon);
    for (const k of SUN_KEYS) {
        const ref = c.sun[k], got = ev[k];
        sunTotal++;
        if ((ref === 'N/A') !== (got === 'N/A')) { naMismatch.push(`${tag} ${k}: python ${ref}, js ${got}`); continue; }
        if (ref === 'N/A') { sunExact++; continue; }
        if (ref === got) sunExact++;
        stats.sun.add(minuteDiff(ref, got), `${tag} ${k}: python ${ref}, js ${got}`);
    }

    // Moon
    stats.moonPhase.add(Math.abs(A.moonPhase(c.date) - c.moon_phase), tag);
    const tRef = utcMs(c.ref_utc);
    const m = A.moonAltAz(tRef, c.lat, c.lon);
    stats.moonPos.add(skyDistance(m.alt, m.az, c.moon_alt, c.moon_az), tag);
    const seps = A.moonSeparations(fixture.objects, tRef, c.lat, c.lon);

    // Time grid
    const grid = A.timeGrid(c.date, c.tz, c.interval);
    if (grid.length !== c.grid_len || grid[0] !== utcMs(c.grid_first_utc)) gridMismatch++;

    // The duration is computed from the reference sun events, so this isolates the
    // duration logic; the end-to-end run below uses the engine's own events.
    const winRef = A.nightWindow(c.date, c.tz, c.lat, c.lon, c.interval, c.sun);
    const winOwn = A.nightWindow(c.date, c.tz, c.lat, c.lon, c.interval, ev);
    const step = c.interval * 60000;

    fixture.objects.forEach((o, i) => {
        const r = c.objects[i], label = `${tag} ${o.name}`;
        c.sample_utc.forEach((ts, j) => {
            const p = A.altAz(o.ra, o.dec, c.lat, c.lon, utcMs(ts));
            stats.position.add(skyDistance(p.alt, p.az, r.alt[j], r.az[j]), `${label} @ ${ts}`);
        });

        stats.moonSep.add(Math.abs(seps[i] - r.moon_sep), label);
        sepTotal++;
        if (Math.round(seps[i]) === Math.round(r.moon_sep)) sepRoundSame++;

        const opts = { threshold: c.threshold, interval: c.interval, mask: c.mask };
        const dOwn = A.observableDuration(o.ra, o.dec, c.lat, c.lon, c.date, c.tz, { ...opts, window: winOwn });
        const dRef = A.observableDuration(o.ra, o.dec, c.lat, c.lon, c.date, c.tz, { ...opts, window: winRef });
        durTotal++;
        if (dOwn.minutes === r.dur) durExact++;
        stats.duration.add(Math.abs(dOwn.minutes - r.dur) / c.interval, `${label}: python ${r.dur} min, js ${dOwn.minutes} min`);
        if (dRef.minutes !== r.dur) stats.duration.add(Math.abs(dRef.minutes - r.dur) / c.interval, `${label} (reference twilight): python ${r.dur}, js ${dRef.minutes}`);
        if (winRef) stats.maxAlt.add(Math.abs(dRef.maxAltitude - r.max_alt), label);

        for (const [key, got] of [['from', dOwn.from], ['to', dOwn.to]]) {
            if ((r[key] === null) !== (got === null)) { stats.fromTo.add(99, `${label} ${key}: python ${r[key]}, js ${got}`); continue; }
            if (got === null) continue;
            const lp = A.localParts(got, c.tz);
            const gotMin = lp.hour * 60 + lp.minute, refMin = hmToMin(r[key].slice(11));
            let d = Math.abs(gotMin - refMin); if (d > 720) d = 1440 - d;
            stats.fromTo.add(d / c.interval, `${label} ${key}: python ${r[key]}, js ${A.formatHM(got, c.tz)}`);
        }
    });
}

// Speed: a full Up Now calculation for a large library
const N = 5000;
const lib = Array.from({ length: N }, (_, i) => ({ ra: (i * 7.31) % 24, dec: ((i * 13.7) % 170) - 85 }));
const t0 = process.hrtime.bigint();
const bEv = A.sunEvents('2026-10-15', 'Europe/Vienna', 47.83, 16.17);
const bWin = A.nightWindow('2026-10-15', 'Europe/Vienna', 47.83, 16.17, 15, bEv);
const bGrid = A.timeGrid('2026-10-15', 'Europe/Vienna', 15);
let sink = 0;
for (const o of lib) {
    sink += A.altAzCurve(o.ra, o.dec, 47.83, 16.17, bGrid, bWin.frame).alt[0];
    sink += A.observableDuration(o.ra, o.dec, 47.83, 16.17, '2026-10-15', 'Europe/Vienna', { window: bWin }).minutes;
}
sink += A.moonSeparations(lib, bGrid[40], 47.83, 16.17)[0];
const benchMs = Number(process.hrtime.bigint() - t0) / 1e6;

// Report
const f = (x, n = 3) => x.toFixed(n);
console.log(`\nNova astro engine vs Python reference: ${fixture.cases.length} location-nights x ${fixture.objects.length} objects\n`);
console.log('Check'.padEnd(28) + 'n'.padStart(8) + 'median'.padStart(10) + 'p99'.padStart(10) + 'max'.padStart(10) + 'limit'.padStart(9) + '  over  result');
let failed = false;
for (const s of Object.values(stats)) {
    const ok = s.over === 0;
    if (!ok) failed = true;
    console.log(s.name.padEnd(28) + String(s.v.length).padStart(8) + f(s.median).padStart(10) + f(s.p99).padStart(10)
        + f(s.max).padStart(10) + (s.limit + ' ' + s.unit).padStart(9) + String(s.over).padStart(6) + '  ' + (ok ? 'PASS' : 'FAIL'));
}
console.log(`\nSun events identical to the minute: ${sunExact} of ${sunTotal}`);
console.log(`Observable duration identical:      ${durExact} of ${durTotal}`);
console.log(`Moon separation same whole degree:  ${sepRoundSame} of ${sepTotal}`);
console.log(`N/A mismatches (sun events):        ${naMismatch.length}`);
console.log(`Time grid mismatches:               ${gridMismatch}`);
console.log(`Speed: curves + duration + Moon separation for ${N} objects: ${benchMs.toFixed(0)} ms`);
if (naMismatch.length || gridMismatch) failed = true;

if (verbose || failed) {
    console.log('\nWorst cases:');
    for (const s of Object.values(stats)) if (s.worst) console.log(`  ${s.name}: ${f(s.worst.x)} ${s.unit}  (${s.worst.label})`);
    naMismatch.forEach(x => console.log('  N/A mismatch: ' + x));
}
console.log(failed ? '\nRESULT: FAIL' : '\nRESULT: PASS');
process.exit(failed ? 1 : 0);
