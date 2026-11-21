// ElRobot calibration coach: overlay for the NormaCore Station page (http://localhost:8889).
// Reads the motor table that Station already shows and adds a live panel:
// which joint is moving now, live position, min/max seen since reset, travel in ticks and degrees.
// Read-only: it sends nothing to the robot.
(() => {
  // ADJUST: bus serial -> arm name
  const ARMS = { '5B61037139': 'LEADER', '5B79016927': 'FOLLOWER' };
  const MOVED_TICKS = 300;   // ADJUST: travel needed before a joint counts as "moved"
  const TICKS_PER_REV = 4096;

  if (window.__calibCoach) { clearInterval(window.__calibCoach.timer); window.__calibCoach.el.remove(); }

  const el = document.createElement('div');
  el.style.cssText = 'position:fixed;top:8px;right:8px;width:440px;max-height:96vh;overflow:auto;z-index:99999;' +
    'background:#101418;color:#e6edf3;font:12px/1.35 ui-monospace,Menlo,monospace;border:1px solid #30363d;' +
    'border-radius:8px;padding:10px;box-shadow:0 6px 24px rgba(0,0,0,.5)';
  document.body.appendChild(el);

  const state = {}; // serial -> { M1: {pos,min,max,last,lastMoveT,wrap,sMin,sMax,curr} }
  let collapsed = false;

  function parse() {
    const lines = document.body.innerText.split('\n');
    let bus = null;
    for (let i = 0; i < lines.length; i++) {
      const p = lines[i].match(/usbmodem([0-9A-Fa-f]+?)1?\s*$/);
      if (lines[i].startsWith('/dev/') && p) { bus = p[1]; continue; }
      const m = lines[i].match(/^M(\d+)\t(\d+)\t(-?\d+)\t(\d+)/);
      if (!m || !bus) continue;
      const next = (lines[i + 1] || '').split('\t');
      update(bus, 'M' + m[1], +m[2], +m[3], +m[4], +next[1]);
    }
  }

  function update(bus, id, pos, curr, sMin, sMax) {
    const b = state[bus] = state[bus] || {};
    const now = Date.now();
    const j = b[id] = b[id] || { min: pos, max: pos, last: pos, lastMoveT: 0, wrap: false };
    if (Math.abs(pos - j.last) > 2000) j.wrap = true;            // jumped across 0/4095
    if (Math.abs(pos - j.last) > 6) { j.lastMoveT = now; }
    j.last = pos; j.pos = pos; j.curr = curr; j.sMin = sMin; j.sMax = sMax;
    j.min = Math.min(j.min, pos); j.max = Math.max(j.max, pos);
  }

  function bar(j, moving) {
    const pct = v => (100 * v / (TICKS_PER_REV - 1)).toFixed(1);
    const col = j.wrap ? '#f85149' : (j.max - j.min >= MOVED_TICKS ? '#3fb950' : '#6e7681');
    return '<div style="position:relative;height:14px;background:#21262d;border-radius:3px;margin:2px 0">' +
      `<div style="position:absolute;left:${pct(j.min)}%;width:${Math.max(0.6, pct(j.max) - pct(j.min))}%;top:0;bottom:0;background:${col};opacity:.55;border-radius:3px"></div>` +
      `<div style="position:absolute;left:${pct(j.pos)}%;top:-2px;bottom:-2px;width:3px;margin-left:-1px;background:${moving ? '#f0c000' : '#e6edf3'}"></div></div>`;
  }

  function render() {
    const now = Date.now();
    let movingTxt = [];
    let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
      '<b style="font-size:14px">Calibration coach</b><span>' +
      '<button data-a="resetall">Reset all</button> <button data-a="toggle">' + (collapsed ? 'Show' : 'Hide') + '</button></span></div>';
    let body = '';
    for (const bus of Object.keys(state)) {
      const name = ARMS[bus] || 'UNKNOWN ARM';
      const ids = Object.keys(state[bus]).sort((a, b) => a.slice(1) - b.slice(1));
      const done = ids.filter(id => state[bus][id].max - state[bus][id].min >= MOVED_TICKS).length;
      // What Station itself has recorded: this is what Save writes to the motors.
      const rec = ids.filter(id => { const j = state[bus][id]; const w = j.sMax - j.sMin; return w >= MOVED_TICKS && w < 4000; }).length;
      const ready = rec === ids.length;
      body += `<div style="margin-top:8px;padding-top:6px;border-top:1px solid #30363d"><b style="font-size:13px">${name}</b> ` +
        `<span style="color:#8b949e">#${bus}</span> <span style="float:right">${done}/${ids.length} moved <button data-a="reset" data-b="${bus}">Reset</button></span></div>` +
        `<div style="margin-top:4px;padding:5px;border-radius:5px;font-size:13px;background:${ready ? '#12351c' : '#3d1418'}">` +
        `Station recorded ${rec}/${ids.length} joints: <b>${ready ? 'OK TO SAVE' : 'DO NOT SAVE YET'}</b></div>`;
      for (const id of ids) {
        const j = state[bus][id];
        const moving = now - j.lastMoveT < 400;
        if (moving) movingTxt.push(name + ' ' + id);
        const travel = j.max - j.min;
        const deg = (travel * 360 / TICKS_PER_REV).toFixed(0);
        const status = j.wrap ? '<span style="color:#f85149">WRAP: re-centre, Calibrate again</span>'
          : travel >= MOVED_TICKS ? '<span style="color:#3fb950">moved</span>'
          : '<span style="color:#8b949e">not moved</span>';
        const sw = j.sMax - j.sMin;
        const sOk = sw >= MOVED_TICKS && sw < 4000;
        const saved = `<span style="color:${sOk ? '#3fb950' : '#f85149'}">Station range ${j.sMin}..${j.sMax} = ${sw} ticks ` +
          (sOk ? 'recorded' : sw >= 4000 ? 'NOT RECORDED (still 0..4095)' : sw < 0 ? 'WRAPPED (min above max)' : 'TOO SMALL, move this joint') + '</span>';
        const torque = j.curr !== 0 ? ` <span style="color:#f0c000">curr ${j.curr}</span>` : '';
        body += `<div style="margin-top:5px;padding:3px 5px;border-radius:4px;${moving ? 'background:#3a2f00;outline:1px solid #f0c000' : ''}">` +
          `<b>${id}</b> pos <b style="font-size:14px">${j.pos}</b>${torque} ` +
          `<span style="float:right">seen ${j.min}..${j.max} = <b>${travel}</b> ticks (${deg}&deg;) ${status}</span>` +
          bar(j, moving) + `<div>${saved}</div></div>`;
      }
    }
    html += `<div style="font-size:15px;padding:6px;border-radius:5px;background:${movingTxt.length ? '#3a2f00' : '#161b22'}">` +
      (movingTxt.length ? 'NOW MOVING: <b>' + movingTxt.join(', ') + '</b>' : 'No joint moving') + '</div>';
    if (!Object.keys(state).length) html += '<div style="margin-top:8px;color:#f0c000">No motor table found. Open the Home page of Station.</div>';
    el.innerHTML = html + (collapsed ? '' : body);
  }

  el.addEventListener('click', e => {
    const a = e.target.dataset && e.target.dataset.a;
    if (a === 'toggle') collapsed = !collapsed;
    if (a === 'resetall') for (const k of Object.keys(state)) delete state[k];
    if (a === 'reset') delete state[e.target.dataset.b];
  });

  const timer = setInterval(() => { try { parse(); render(); } catch (err) { el.textContent = 'Coach error: ' + err.message; } }, 100);
  window.__calibCoach = { timer, el, state };
})();
