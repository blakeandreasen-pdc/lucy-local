// Shared helpers for the lucy-local pages.
'use strict';

const $ = (id) => document.getElementById(id);

// h('div', {class: 'x', onclick: fn}, 'text', child) - builds DOM without innerHTML
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k instanceof Node ? k : String(k));
  return el;
}

async function api(path, opts = {}) {
  const init = {method: opts.method || (opts.body !== undefined ? 'POST' : 'GET'), headers: {}};
  if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => x.msg).join('; ') : 'HTTP ' + res.status);
  }
  return data;
}

let toastTimer;
function toast(msg, err) {
  let t = $('toast');
  if (!t) { t = h('div', {id: 'toast', class: 'toast', role: 'status'}); document.body.append(t); }
  t.textContent = msg;
  t.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), err ? 5000 : 2200);
}

async function act(name, value) {
  try {
    await api('api/action/' + name, {body: value === undefined ? {} : {value}});
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

// Live status: fills any element with data-status="<field>" and calls onStatus(s).
function liveStatus(onStatus) {
  const apply = (s) => {
    document.querySelectorAll('[data-status]').forEach((el) => {
      const k = el.dataset.status, v = s[k];
      el.textContent = v == null ? '--' : k === 'battery' || k === 'suction' || k === 'volume' ? v + '%' : v;
    });
    const dot = $('conn-dot');
    if (dot) dot.className = 'dot ' + (s.online ? 'ok' : 'bad');
    const conn = $('conn');
    if (conn) conn.textContent = s.online ? s.name : 'offline';
    onStatus && onStatus(s);
  };
  api('api/status').then(apply).catch(() => {});
  let es;
  const connect = () => {
    es = new EventSource('api/events');
    es.onmessage = (e) => { try { apply(JSON.parse(e.data)); } catch (_) {} };
    es.onerror = () => { es.close(); setTimeout(connect, 3000); };
  };
  connect();
}

function topbar(active) {
  const link = (href, label, key) => h('a', {href, class: active === key ? 'on' : null}, label);
  const bar = h('header', {class: 'topbar'},
    h('div', {class: 'brand'}, '🧹 lucy-local'),
    h('nav', {class: 'nav'}, link('./', 'Dashboard', 'home'), link('drive', 'Drive', 'drive'), link('profiles', 'Profiles', 'profiles'), link('settings', 'Settings', 'settings')),
    h('span', {class: 'grow'}),
    h('div', {class: 'chips'},
      h('span', {class: 'chip'}, h('span', {class: 'dot', id: 'conn-dot'}), h('span', {id: 'conn'}, '…')),
      h('span', {class: 'chip'}, '🔋 ', h('span', {'data-status': 'battery'}, '--'))));
  document.body.prepend(bar);
}

// ---- camera over WebRTC (WHEP) from MediaMTX; reading path "lucy" wakes the robot's stream.
// Signalling goes through lucy-local (api/camera/whep), so it works on any origin, under a
// path prefix (Home Assistant ingress) and over HTTPS; the video itself is UDP 8189 direct.
const Camera = (() => {
  const url = new URL('api/camera/whep', location.href).href;

  return function (video, onState) {
    let pc = null, session = null, starting = null;
    const set = (s, msg) => onState && onState(s, msg);
    function stop() {
      if (pc) { pc.close(); pc = null; }
      if (session) { try { fetch(new URL(session, url), {method: 'DELETE', keepalive: true}).catch(() => {}); } catch (_) {} session = null; }
      video.srcObject = null;
      set('off');
    }
    function start() {
      if (starting) return starting;
      if (pc) return Promise.resolve();
      set('starting');
      starting = (async () => {
        const conn = new RTCPeerConnection();
        pc = conn;
        conn.addTransceiver('video', {direction: 'recvonly'});
        conn.ontrack = (ev) => { video.srcObject = ev.streams[0] || new MediaStream([ev.track]); };
        conn.onconnectionstatechange = () => {
          if (conn === pc && ['failed', 'disconnected'].includes(conn.connectionState)) { stop(); set('dropped'); }
        };
        await conn.setLocalDescription(await conn.createOffer());
        await new Promise((r) => {
          if (conn.iceGatheringState === 'complete') return r();
          const t = setTimeout(r, 2000);
          conn.addEventListener('icegatheringstatechange', () => { if (conn.iceGatheringState === 'complete') { clearTimeout(t); r(); } });
        });
        const res = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/sdp'}, body: conn.localDescription.sdp});
        if (!res.ok) throw new Error('camera server returned ' + res.status + (res.status === 404 ? ' (robot did not start streaming)' : ''));
        session = res.headers.get('Location');
        await conn.setRemoteDescription({type: 'answer', sdp: await res.text()});
        await new Promise((r, j) => {
          if (video.videoWidth) return r();
          const t = setTimeout(() => j(new Error('no video after 20 s')), 20000);
          video.addEventListener('loadeddata', () => { clearTimeout(t); r(); }, {once: true});
        });
        set('live');
      })().catch((e) => { stop(); set('error', e.message); throw e; }).finally(() => { starting = null; });
      return starting;
    }
    function snapshot() {
      if (!pc || !video.videoWidth) return null;
      const c = document.createElement('canvas');
      c.width = video.videoWidth; c.height = video.videoHeight;
      c.getContext('2d').drawImage(video, 0, 0);
      return new Promise((r) => c.toBlob((b) => r({blob: b, width: c.width, height: c.height}), 'image/png'));
    }
    window.addEventListener('pagehide', stop);
    return {start, stop, snapshot, get running() { return !!pc; }};
  };
})();

function stamp() { return new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15); }

// Map: polls /api/map (which keeps the server asking the robot while someone looks) and draws
// the grid, cropped to the explored area, with the robot (dot + heading) and dock (square).
// Cells are occupancy 0-10: 5 unknown, below 5 free, above 5 likely obstacle.
// Poses come with row/col; theta is radians in a frame where x points up and y points left.
function MapView(canvas, onInfo, opts = {}) {
  const ctx = canvas.getContext('2d');
  const grid = document.createElement('canvas');
  let last = 0, map = null, timer = null, view = null, target = null;
  function info(t) { onInfo && onInfo(t); }
  function draw() {
    const cells = Uint8Array.from(atob(map.cells), (c) => c.charCodeAt(0));
    const W = map.width, H = map.height;
    let x0 = W, y0 = H, x1 = -1, y1 = -1, free = 0;
    const take = (x, y) => { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; };
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const v = cells[y * W + x];
      if (v === 5) continue;
      if (v < 5) free++;
      take(x, y);
    }
    for (const p of [map.robot, map.dock]) if (p && p.col >= 0 && p.col < W && p.row >= 0 && p.row < H) take(p.col, p.row);
    if (x1 < 0) { canvas.width = canvas.height = 1; view = null; return info('Map is empty so far'); }
    const pad = 4;
    x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad);
    x1 = Math.min(W - 1, x1 + pad); y1 = Math.min(H - 1, y1 + pad);
    const w = x1 - x0 + 1, hgt = y1 - y0 + 1;
    // cells at 1 px each on an offscreen canvas
    grid.width = w; grid.height = hgt;
    const g = grid.getContext('2d'), img = g.createImageData(w, hgt);
    for (let y = 0; y < hgt; y++) for (let x = 0; x < w; x++) {
      const v = cells[(y + y0) * W + (x + x0)];
      if (v === 5) continue;
      const c = v < 5 ? [79, 140, 255, 90 + v * 10] : [236, 238, 244, Math.round(120 + (v - 5) * 27)];
      img.data.set(c, (y * w + x) * 4);
    }
    g.putImageData(img, 0, 0);
    // scale up crisply, then draw the markers smoothly on top
    const box = canvas.parentElement, dpr = window.devicePixelRatio || 1;
    const fit = Math.min(box.clientWidth / w, (box.clientHeight || box.clientWidth) / hgt);
    const k = Math.max(2, fit) * dpr;
    canvas.width = Math.round(w * k); canvas.height = Math.round(hgt * k);
    canvas.style.width = (canvas.width / dpr) + 'px'; canvas.style.height = (canvas.height / dpr) + 'px';
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(grid, 0, 0, canvas.width, canvas.height);
    view = {x0, y0, k, dpr};
    const at = (p) => [(p.col - x0 + 0.5) * k, (p.row - y0 + 0.5) * k];
    const r = Math.max(5 * dpr, 1.6 * k);
    if (target) {
      // inverse of to_cell, continuous: x = (H/2 - row - 0.5) * res, y = (W/2 - col - 0.5) * res
      const tx = (W / 2 - target.y / map.resolution - x0) * k, ty = (H / 2 - target.x / map.resolution - y0) * k;
      ctx.strokeStyle = '#ff5c7a'; ctx.lineWidth = 2.5 * dpr;
      ctx.beginPath(); ctx.arc(tx, ty, r * 1.2, 0, 2 * Math.PI); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(tx - r * 1.8, ty); ctx.lineTo(tx + r * 1.8, ty); ctx.moveTo(tx, ty - r * 1.8); ctx.lineTo(tx, ty + r * 1.8); ctx.stroke();
    }
    if (map.dock) {
      const [dx, dy] = at(map.dock);
      ctx.fillStyle = '#3ecf8e'; ctx.strokeStyle = '#0f1117'; ctx.lineWidth = 1.5 * dpr;
      ctx.fillRect(dx - r * 0.7, dy - r * 0.7, r * 1.4, r * 1.4); ctx.strokeRect(dx - r * 0.7, dy - r * 0.7, r * 1.4, r * 1.4);
    }
    if (map.robot) {
      const [rx, ry] = at(map.robot), t = map.robot.theta;
      // x is up (-row), y is left (-col): heading (cos t, sin t) -> screen (-sin t, -cos t)
      const hx = -Math.sin(t), hy = -Math.cos(t);
      ctx.beginPath(); ctx.arc(rx, ry, r, 0, 2 * Math.PI);
      ctx.fillStyle = '#f2a83b'; ctx.fill(); ctx.lineWidth = 2 * dpr; ctx.strokeStyle = '#0f1117'; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(rx, ry); ctx.lineTo(rx + hx * r * 1.9, ry + hy * r * 1.9);
      ctx.strokeStyle = '#f2a83b'; ctx.lineWidth = 2.5 * dpr; ctx.stroke();
    }
    const area = free * map.resolution * map.resolution;
    shown = area.toFixed(1) + ' m² explored · ' + Math.round(map.resolution * 100) + ' cm cells';
    age();
  }
  // the Max builds a new map only every 1-2 min while cleaning, so say how old this one is
  let shown = '';
  function age() {
    if (!map) return;
    const s = Math.max(0, Math.round(Date.now() / 1000 - map.received));
    info(shown + ' · ' + (s < 60 ? s + ' s old' : Math.floor(s / 60) + ' min ' + (s % 60) + ' s old'));
  }
  async function poll() {
    if (document.hidden) return;
    age();
    try {
      const r = await api('api/map?since=' + last);
      if (!r.supported) { info('This robot profile has no map command'); return stop(); }
      if (r.changed) { map = r; last = r.received; draw(); }
      else if (!map) info('Asking the robot for its map… (about 10 s)');
    } catch (e) { info('Map unavailable: ' + e.message); }
  }
  function start() { if (!timer) { poll(); timer = setInterval(poll, opts.every || 3000); } }
  function stop() { clearInterval(timer); timer = null; }
  addEventListener('resize', () => map && draw());
  // tap to pick a point; opts.onPick(x, y) gets metres in the robot's map frame
  if (opts.onPick) {
    canvas.style.cursor = 'crosshair';
    canvas.addEventListener('click', (e) => {
      if (!map || !view) return;
      const b = canvas.getBoundingClientRect();
      const px = (e.clientX - b.left) * view.dpr, py = (e.clientY - b.top) * view.dpr;
      const x = (map.height / 2 - (py / view.k + view.y0)) * map.resolution;
      const y = (map.width / 2 - (px / view.k + view.x0)) * map.resolution;
      opts.onPick(Math.round(x * 100) / 100, Math.round(y * 100) / 100);
    });
  }
  function mark(t) { target = t; if (map) draw(); }
  return {start, stop, mark, get running() { return !!timer; }};
}

// Talk: sends this browser's microphone to the robot's speaker. Tap to start, tap to stop.
// Browsers only allow the microphone on HTTPS pages (or localhost).
// onState('starting'|'on'|'off'|'error', message)
function Talk(onState) {
  const TALK_WHIP = new URL('api/talk/whip', location.href).href;
  let pc = null, session = null, mic = null, ping = null, busy = false, told = false;
  const set = (s, m) => onState && onState(s, m);
  async function start() {
    if (busy || pc) return;
    busy = true;
    set('starting');
    try {
      if (!window.isSecureContext || !navigator.mediaDevices) {
        throw new Error('the browser only allows the microphone on HTTPS pages; open this through your HTTPS proxy');
      }
      mic = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      pc = new RTCPeerConnection();
      pc.addTransceiver(mic.getAudioTracks()[0], {direction: 'sendonly'});
      await pc.setLocalDescription(await pc.createOffer());
      await new Promise((r) => {
        if (pc.iceGatheringState === 'complete') return r();
        const t = setTimeout(r, 2000);
        pc.addEventListener('icegatheringstatechange', () => { if (pc && pc.iceGatheringState === 'complete') { clearTimeout(t); r(); } });
      });
      const res = await fetch(TALK_WHIP, {method: 'POST', headers: {'Content-Type': 'application/sdp'}, body: pc.localDescription.sdp});
      if (!res.ok) throw new Error('media server returned ' + res.status);
      session = res.headers.get('Location');
      await pc.setRemoteDescription({type: 'answer', sdp: await res.text()});
      await new Promise((r, j) => {
        const t = setTimeout(() => j(new Error('could not reach the media server (UDP 8189)')), 10000);
        const check = () => { if (pc && pc.connectionState === 'connected') { clearTimeout(t); r(); } };
        pc.addEventListener('connectionstatechange', check); check();
      });
      told = true;
      await api('api/talk', {body: {on: true}});
      ping = setInterval(() => api('api/talk', {body: {on: true}}).catch(() => {}), 3000);
      pc.addEventListener('connectionstatechange', () => { if (pc && ['failed', 'disconnected'].includes(pc.connectionState)) stop('dropped'); });
      set('on');
    } catch (e) {
      await stop();
      set('error', e.message);
    } finally { busy = false; }
  }
  async function stop(reason) {
    clearInterval(ping); ping = null;
    // tell the robot first, so it isn't left reconnecting to a stream that's gone. Only if this
    // page started the talk: a failed start must not cut off someone else who is talking.
    if (told) { told = false; await api('api/talk', {body: {on: false}}).catch(() => {}); }
    if (pc) { pc.close(); pc = null; }
    if (session) { fetch(new URL(session, TALK_WHIP), {method: 'DELETE', keepalive: true}).catch(() => {}); session = null; }
    if (mic) { mic.getTracks().forEach((t) => t.stop()); mic = null; }
    set(reason === 'dropped' ? 'error' : 'off', reason === 'dropped' ? 'connection dropped' : undefined);
  }
  addEventListener('pagehide', () => {
    if (told) fetch('api/talk', {method: 'POST', keepalive: true, headers: {'Content-Type': 'application/json'}, body: '{"on":false}'}).catch(() => {});
  });
  return {start, stop, toggle: () => (pc ? stop() : start()), get on() { return !!pc; }};
}
