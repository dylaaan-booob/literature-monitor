'use strict';
// RhinE archive.js / motion.js + visual-lab's Canvas affine drawing adaptation.
// Original springs, selection shoulders, axes, quads and sprites are retained.
// See SOURCES.md for the browser adaptation boundary.
const $ = id => document.getElementById(id);
const BG = '#eeede7', INK = '#252821', MUTED = '#63665d', ORANGE = '#ff7a01';
const mix = (a, b, t) => a + (b - a) * t;
const easeInOut = t => t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const pad = n => String(n).padStart(2, '0');
const initialParams = new URLSearchParams(location.search);
const initialView = initialParams.get('view') ?? 'inbox';
const initialPaper = initialParams.get('paper');
const initialLinkValid = ['inbox','kept'].includes(initialView) &&
  [...initialParams.keys()].every(key=>['view','paper'].includes(key)) &&
  initialParams.getAll('view').length <= 1 && initialParams.getAll('paper').length <= 1 &&
  (!initialParams.has('paper') || (initialParams.has('view') &&
    /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(initialPaper)));
window.uiNextInitialLink = initialLinkValid
  ? {view:initialView,paper:initialPaper?.toLowerCase() || null}
  : {view:'inbox',paper:null};
const mediaQuery = matchMedia('(prefers-reduced-motion: reduce)');
let REDUCE_MOTION = mediaQuery.matches;
function Spring(x = 0, w = 16) { return { x, v: 0, to: x, w }; }
function stepSpring(s, dt) {
    if (REDUCE_MOTION) { s.x = s.to; s.v = 0; return false; }
    const a = s.w * s.w * (s.to - s.x) - 2 * s.w * s.v;
    s.v += a * dt;
    s.x += s.v * dt;
    if (Math.abs(s.to - s.x) < 0.01 && Math.abs(s.v) < 0.05) { s.x = s.to; s.v = 0; return false; }
    return true;
}


let groups = [], rows = [], journalMeta, railMeta, arrayClear, arrayFrost, railImages;
const covers = new Map(), lists = new Map(), hitAreas = [];
const fadedSprites = new Map();
const state = { group: 0, track: 0, ready: false, view: 'selected', pendingPaper: null, focusSource: null };
// A continuous shelf coordinate retains direction across the logical 3 -> 1 seam.
const strip = Spring(0, 9), camera = { x: Spring(0, 9), y: Spring(0, 9) };
const groupAt = track => ((track % groups.length) + groups.length) % groups.length;
let selectedCoverQuad = [], stageWidth = 0, inspectionFrames = [];
const inspection = { time: 0, target: 0, duration: .60, removeAfter: false };
const directoryScroll = Spring(0, 12);
const directoryCanvas = document.createElement('canvas');
directoryCanvas.width = directoryCanvas.height = 768;
const directoryContext = directoryCanvas.getContext('2d');
let directoryAreas = [];
const decryption = new DocumentDecryption();
const reviewSurface = new SurfaceTransition($('journal-view'), undefined, 180, 180);
let enrichment = null;
const reviewedPapers = new Map();
const keptPapers = new Map();
let keptSnapshot;
let keptReview=null;
const reviewPanel=document.querySelector('.review-right');
let decisionPending=false, snapshotApplying=false;
const currentPaper = () => keptReview ? keptReview.entries.find(e=>e.paper.id===state.pendingPaper)?.paper : selectedJournal()?.papers.find(p => p.id === state.pendingPaper);
const reviewPapers = () => keptReview ? keptReview.entries.map(e=>e.paper) : selectedJournal().papers;
const reviewJournal = () => keptReview ? keptReview.entries.find(e=>e.paper.id===state.pendingPaper).journal : selectedJournal();
function syncNavigation() {
  const busy=decisionPending || Boolean(snapshotApplying) || enrichment?.phase==='Saving' || (inspection.target ? inspection.time!==inspection.duration : inspection.time!==0);
  const previewOpen=$('s-file-preview')?.hidden===false, settingsBusy=$('settings-view').dataset.busy==='true';
  $('inbox-open').disabled=$('kept-open').disabled=!state.ready || busy || previewOpen || settingsBusy;
  for(const name of ['journals','access','settings'])$(name+'-open').disabled=busy || previewOpen || settingsBusy || $('run-view').open || $('kept-flow').open;
  const active=window.uiNextSettings?.currentView() || (state.view==='kept'?'kept':'inbox');
  for(const name of ['inbox','kept','journals','access','settings'])$(name+'-open').setAttribute('aria-current',active===name?'page':'false');
  const offset=$(active+'-open').offsetLeft;
  document.querySelector('.view-nav').style.setProperty('--nav-x',`${offset}px`);
  $('view-nav-plate').style.transform=`translateX(${offset}px)`;
}
const activeSlots = group => groups[group].journals.flatMap((j, i) => j ? [i] : []);
const phaseTurn = () => easeInOut(clamp((inspection.time - .18) / .42));
const phaseLift = () => easeInOut(clamp(inspection.time / .18));
const AR = { hover: -1 };
const slotOf = (row, i) => i;
const selectedRow = () => rows[state.group];
const selectedJournal = () => groups[state.group]?.journals[selectedRow()?.sel];
let W = 0, H = 0, ratio = 1, running = false, lastTime = 0, lastWheel = 0;
const canvas = $('archive'), ctx = canvas.getContext('2d');

function caseTargets(i, r, active, row, hover = AR.hover) {
    const d = slotOf(row, i) - slotOf(row, r.sel), bell = Math.exp(-d * d / 4.5) - .32 * Math.exp(-d * d / 18), hd = i - hover;
    const hov = active && hover >= 0 ? .5 * Math.exp(-hd * hd / 2.2) : 0, isSel = active && i === r.sel;
    return {
        lift: (active ? 1.1 : .65) * bell + hov + (isSel ? 1.35 : 0),
        clear: isSel ? 1 : 0,
        gap: active ? (i > r.sel ? .55 : i < r.sel ? -.25 : 0) : 0,
    };
}

let CASE_POLY = null;
function casePoly() {
    if (CASE_POLY && CASE_POLY.meta === journalMeta) return CASE_POLY;   // made again for another skin's case.json
    const m = journalMeta.array.origin, ax = journalMeta.axes, at = (x, y, z) => [m[0] + x * ax.x[0] + y * ax.y[0] + z * ax.z[0], m[1] + x * ax.x[1] + y * ax.y[1] + z * ax.z[1]];
    CASE_POLY = [at(-2, -.23, 0), at(2, -.23, 0), at(2, -.23, 3.5), at(2, .23, 3.5), at(-2, .23, 3.5), at(-2, -.23, 3.5)];
    CASE_POLY.meta = journalMeta;
    return CASE_POLY;
}


function geometry() {
  // Keep the existing sprite scale and axes; move only the archive origin.
  const k = .81 * Math.min(W / 930, H / 710);
  return { k, x: camera.x.x, y: camera.y.x };
}
function centerArchive(immediate = false) {
  if (!state.ready || W <= 0 || H <= 0) return;
  const row = selectedRow(), m = journalMeta.array, ax = journalMeta.axes, k = geometry().k;
  const center = m.cover.reduce((p, q) => [p[0] + q[0] / 4, p[1] + q[1] / 4], [0, 0]);
  const slot = row.sel - row.scroll.to;
  const lift = caseTargets(row.sel, row, true, groups[state.group], -1).lift;
  camera.x.to = W * .50 - (slot * ax.y[0] + lift * ax.z[0] + center[0] - m.origin[0]) * k;
  camera.y.to = H * .47 - (slot * ax.y[1] + lift * ax.z[1] + center[1] - m.origin[1]) * k;
  if (immediate) for (const s of Object.values(camera)) { s.x = s.to; s.v = 0; }
}
function projection(group, index, lift, gap = 0, position = state.track - strip.x) {
  const g = geometry(), ax = journalMeta.axes;
  const y = index - rows[group].scroll.x + gap, x = -position * 4.7;
  return [g.x + (x * ax.x[0] + y * ax.y[0] + lift * ax.z[0]) * g.k,
          g.y + (x * ax.x[1] + y * ax.y[1] + lift * ax.z[1]) * g.k];
}
function transformedQuad(q, ox, oy, k) { return q.map(([x, y]) => [ox + x * k, oy + y * k]); }
function quadImage(image, q, ox, oy, k, alpha = 1) {
  ctx.save(); ctx.globalAlpha *= alpha;
  ctx.transform((q[1][0] - q[0][0]) * k / image.width, (q[1][1] - q[0][1]) * k / image.width,
    (q[3][0] - q[0][0]) * k / image.height, (q[3][1] - q[0][1]) * k / image.height, ox + q[0][0] * k, oy + q[0][1] * k);
  ctx.drawImage(image, 0, 0); ctx.restore();
}
function spine(q, ox, oy, k, issn) {
  ctx.save(); ctx.transform((q[1][0] - q[0][0]) * k / 100, (q[1][1] - q[0][1]) * k / 100,
    (q[3][0] - q[0][0]) * k / 60, (q[3][1] - q[0][1]) * k / 60, ox + q[0][0] * k, oy + q[0][1] * k);
  ctx.fillStyle = INK; ctx.font = '500 13px "Geist Mono"'; ctx.fillText('ISSN-L', 8, 21);
  ctx.font = '15px "Geist Mono"'; ctx.fillText(issn, 8, 48); ctx.restore();
}
function topTitle(group, index, ox, oy, k) {
  const ax = journalMeta.axes, m = journalMeta.array.origin;
  const map = (x, y, z) => [ox + (m[0] + x * ax.x[0] + y * ax.y[0] + z * ax.z[0]) * k, oy + (m[1] + x * ax.x[1] + y * ax.y[1] + z * ax.z[1]) * k];
  const a = map(-1.8, .15, 3.5), b = map(1.1, .15, 3.5), c = map(-1.8, -.15, 3.5);
  ctx.save(); ctx.transform((b[0] - a[0]) / 290, (b[1] - a[1]) / 290, (c[0] - a[0]) / 33, (c[1] - a[1]) / 33, ...a);
  ctx.fillStyle = '#e9e6dd'; ctx.fillRect(0, 0, 290, 33); ctx.fillStyle = INK;
  ctx.font = '500 13px "Geist Mono"';
  let title = groups[group].journals[index].name.toUpperCase();
  if (ctx.measureText(title).width > 275) {
    while (ctx.measureText(title + '…').width > 275) title = title.slice(0, -1);
    title += '…';
  }
  ctx.fillText(title, 8, 23); ctx.restore();
}
function arrayCase(group, index, track) {
  const journal = groups[group].journals[index];
  const isInspected = group === state.group && track === state.track && index === selectedRow().sel && inspection.time > 0;
  if (isInspected && phaseTurn() > 0) return;
  const active = track === state.track, position = track - strip.x;
  // Empty cases are drawing slots only, with no Journal or selection state.
  const c = rows[group].cases[index] || { lift: { x: 0 }, clear: { x: 0 }, gap: { x: active ? .55 : 0 } };
  const clear = active ? c.clear.x : 0;
  const g = geometry(), m = journalMeta.array;
  const [sx, sy] = projection(group, index, c.lift.x + (isInspected ? 2.2 * phaseLift() : 0), c.gap.x, position);
  const ox = sx - m.origin[0] * g.k, oy = sy - m.origin[1] * g.k, size = 600 * g.k;
  ctx.save();
  const artworkPair = journal && covers.get(journal.key);
  const tint = Math.round(clamp(.38 * clamp(position) + .14 * Math.max(0, position), 0, .8) * 10) / 10;
  if (artworkPair) quadImage(artworkPair.blur, m.cover, ox, oy, g.k);
  else {
    ctx.beginPath(); transformedQuad(m.cover, ox, oy, g.k).forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.closePath(); ctx.fillStyle = '#deddd5'; ctx.fill();
  }
  if (tint > 0) {
    ctx.save(); ctx.globalAlpha *= tint;
    ctx.beginPath(); transformedQuad(m.cover, ox, oy, g.k).forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.closePath(); ctx.fillStyle = BG; ctx.fill(); ctx.restore();
    ctx.drawImage(fadedSprite(tint), ox, oy, size, size);
  } else {
    if (artworkPair && clear > .01) quadImage(artworkPair.image, m.cover, ox, oy, g.k, clamp(clear));
    ctx.save(); ctx.globalAlpha *= 1 - clamp(clear); ctx.drawImage(arrayFrost, ox, oy, size, size); ctx.restore();
    ctx.save(); ctx.globalAlpha *= clamp(clear); ctx.drawImage(arrayClear, ox, oy, size, size); ctx.restore();
  }
  ctx.restore();
  ctx.save();
  if (journal && active) spine(m.label, ox, oy, g.k, journal.issn_l || 'UNMAPPED');
  if (journal && tint < .5) topTitle(group, index, ox, oy, g.k);
  ctx.restore();
  const quad = transformedQuad(m.cover, ox, oy, g.k);
  if (journal && active && index === selectedRow().sel) {
    selectedCoverQuad = quad;
    ctx.fillStyle = ORANGE; quad.forEach(([x, y]) => ctx.fillRect(x - 2.5, y - 2.5, 5, 5));
    const [x, y] = quad[1], lx = x + 22, ly = y - 27;
    ctx.strokeStyle = INK; ctx.lineWidth = .8; ctx.beginPath(); ctx.moveTo(x + 4, y - 4); ctx.lineTo(lx, ly); ctx.lineTo(lx + 24, ly); ctx.stroke();
    const label = journal.issn_l ? `ISSN-L ${journal.issn_l}` : 'UNMAPPED';
    ctx.font = '9px "Geist Mono"';
    ctx.fillStyle = INK; ctx.fillRect(lx + 24, ly - 9, ctx.measureText(label).width + 14, 18);
    ctx.fillStyle = BG; ctx.fillText(label, lx + 31, ly + 3);
  }
  // Original resting hit polygon avoids moving the target under the pointer.
  const target = journal ? caseTargets(index, rows[group], active, groups[group], -1) : { lift: 0, gap: c.gap.x };
  const rest = projection(group, index, target.lift, target.gap, position);
  // An inert hit blocks clicks through an empty case into a row behind it.
  hitAreas.push({ group, track, index, empty: !journal, quad: transformedQuad(casePoly(), rest[0] - m.origin[0] * g.k, rest[1] - m.origin[1] * g.k, g.k) });
}
function fadedSprite(f) {
  // Canvas equivalent of RhinE's Direct2D colour matrix: RGB*(1-f)+BG*f,
  // preserving source alpha. Cached at the original 0.1 tint steps.
  if (fadedSprites.has(f)) return fadedSprites.get(f);
  const image = document.createElement('canvas'); image.width = image.height = 600;
  const g = image.getContext('2d'); g.drawImage(arrayFrost, 0, 0);
  const pixels = g.getImageData(0, 0, 600, 600), bg = [238, 237, 231];
  for (let i = 0; i < pixels.data.length; i += 4)
    for (let c = 0; c < 3; c++) pixels.data[i + c] = pixels.data[i + c] * (1 - f) + bg[c] * f;
  g.putImageData(pixels, 0, 0); fadedSprites.set(f, image); return image;
}
function drawRail(group, slot, slots, position) {
  const g = geometry(), [sx, sy] = projection(group, slot, 0, 0, position), type = slot < 0 ? 'front' : slot >= slots ? 'back' : 'mid';
  ctx.save();
  const ox = sx - railMeta.origin[0] * g.k, oy = sy - railMeta.origin[1] * g.k;
  ctx.drawImage(railImages[type], ox, oy, 900 * g.k, 900 * g.k);
  const journal = groups[group].journals[slot];
  const previous = groups[group].journals.slice(0, Math.max(0, slot)).filter(Boolean).at(-1);
  if (journal && (!previous || journal.tier !== previous.tier)) {
    // Segment labels use the rail's own projected label, rather than a new plane.
    const q = railMeta.label;
    ctx.transform((q[1][0] - q[0][0]) * g.k / 100, (q[1][1] - q[0][1]) * g.k / 100,
      (q[3][0] - q[0][0]) * g.k / 18, (q[3][1] - q[0][1]) * g.k / 18,
      ox + q[0][0] * g.k, oy + q[0][1] * g.k);
    ctx.fillStyle = INK; ctx.fillRect(0, 0, 100, 18);
    ctx.fillStyle = BG; ctx.font = '500 13px "Geist Mono"';
    ctx.fillText(journal.tier || groups[group].name, 5, 14, 90);
  }
  ctx.restore();
}

function paddedSlotCount(group, position, active) {
  const g = geometry(), m = journalMeta.array;
  let slots = groups[group].journals.length;
  // Same projected bounds and overscan as RhinE's caseWindow. Extend each
  // row independently until the next entire case is outside the viewport.
  while (true) {
    const [sx, sy] = projection(group, slots, 0, active ? .55 : 0, position);
    const ox = sx - m.origin[0] * g.k, oy = sy - m.origin[1] * g.k;
    if (ox > W + 160 || oy + 600 * g.k < -320) return slots;
    slots++;
  }
}

function draw() {
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, stageWidth, H);
  hitAreas.length = 0;
  selectedCoverQuad = [];
  if (!journalMeta || !groups.length || W <= 0 || H <= 0) return;
  // Preserve the original shelf viewport while inspection can cross the full stage.
  ctx.save(); ctx.beginPath(); ctx.rect(0, 0, W, H); ctx.clip();
  // RhinE's back-to-front rail/case ordering, one row per subject.
  for (let track = Math.floor(strip.x) + groups.length; track >= Math.floor(strip.x) - 1; track--) {
    const group = groupAt(track), row = rows[group], pos = track - strip.x, active = track === state.track;
    if (pos <= -1 || pos >= groups.length) continue;
    ctx.save();
    ctx.globalAlpha = clamp(1 + pos) * clamp((groups.length - pos) / .8);
    const items = [];
    const slots = paddedSlotCount(group, pos, active);
    for (let i = -1; i <= slots; i++)
      items.push({ i, rail: true, close: -.62 * (i - row.scroll.x + .4) });
    for (let i = 0; i < slots; i++) {
      const c = row.cases[i];
      items.push({ i, rail: false, close: -.62 * (i - row.scroll.x + (c ? c.gap.x : active ? .55 : 0)) + .64 * (c ? c.lift.x : 0) * .2 });
    }
    items.sort((a, b) => a.close - b.close);
    for (const item of items) {
      if (item.rail) drawRail(group, item.i, slots, pos);
      else arrayCase(group, item.i, track);
    }
    ctx.restore();
  }
  ctx.restore();
  // Keep the original soft edge where the archive meets its navigation strip.
  const fade = ctx.createLinearGradient(0, H - 130, 0, H - 80);
  fade.addColorStop(0, '#eeede700'); fade.addColorStop(1, BG);
  ctx.fillStyle = fade; ctx.fillRect(0, H - 130, W, 130);
  if (inspection.time > .18) {
    ctx.save(); ctx.globalAlpha = phaseTurn() * .97; ctx.fillStyle = BG; ctx.fillRect(0, 0, stageWidth, H); ctx.restore();
    drawInspection();
  }
}

function tick(now) {
  if(window.uiNextSettings?.active()){running=false;lastTime=0;return;}
  const dt = lastTime ? Math.min(.05, (now - lastTime) / 1000) : .016;
  lastTime = now;
  let more = stepInspection(dt);
  if (!groups.length && !keptReview) { draw(); running = false; lastTime = 0; return; }
  if (state.view === 'selected') {
    strip.to = state.track;
    centerArchive();
    for (const s of [strip, camera.x, camera.y]) {
      if (REDUCE_MOTION) { s.x = s.to; s.v = 0; }
      more = stepSpring(s, dt) || more;
    }
    rows.forEach((row, g) => {
      if (REDUCE_MOTION) { row.scroll.x = row.scroll.to; row.scroll.v = 0; }
      more = stepSpring(row.scroll, dt) || more;
      row.cases.forEach((c, i) => {
        const target = groups[g].journals[i] ? caseTargets(i, row, g === state.group, groups[g]) : {lift:0,clear:0,gap:g === state.group ? .55 : 0};
        for (const key of ['lift', 'clear', 'gap']) {
          c[key].to = target[key];
          if (REDUCE_MOTION) { c[key].x = c[key].to; c[key].v = 0; }
          more = stepSpring(c[key], dt) || more;
        }
      });
    });
  }
  if (state.view === 'journal') {
    more = stepSpring(directoryScroll, dt) || more;
    if (!inspection.removeAfter) paintDirectory();
  }
  const revealing = decryption.active;
  decryption.update(now / 1000, { clarity: 1 }, REDUCE_MOTION);
  if (revealing && !decryption.active && (state.view === 'journal' || keptReview)) renderEnrich();
  more = decryption.active || more;
  draw();
  if (more) requestAnimationFrame(tick);
  else { running = false; lastTime = 0; }
}
function wake() { if (!window.uiNextSettings?.active() && !running && state.ready) { running = true; lastTime = 0; requestAnimationFrame(tick); } }
function resize() {
  syncNavigation();
  if(window.uiNextSettings?.active())return;
  const r = canvas.getBoundingClientRect(); W = document.querySelector('.collection').clientWidth; H = r.height; stageWidth = r.width;
  ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(stageWidth * ratio); canvas.height = Math.round(H * ratio);
  if (groups.length) centerArchive(true);
  if (state.ready && groups.length && W > 0 && state.view === 'selected') {
    layoutJournalTitle();
  }
  if (state.view === 'journal' || keptReview) fillAbstractPlaceholder();
  if (decryption.active) decryption.refresh();
  wake();
}
new ResizeObserver(resize).observe(canvas);

function scrollTarget(index, row) {
  // Same slot-minus-four rule as RhinE; measure available slots from its axes.
  const g = geometry(), ax = journalMeta.axes, m = journalMeta.array;
  let slots = 0;
  while (slots < row.journals.length) {
    // Capacity stays independent of the camera following the selected case.
    const ox = W * .50 + slots * ax.y[0] * g.k - m.origin[0] * g.k;
    const oy = H - 110 + slots * ax.y[1] * g.k - m.origin[1] * g.k;
    if (ox + 600 * g.k * .7 > W || oy < 84 - 600 * g.k * .05) break;
    slots++;
  }
  return clamp(index - Math.min(4, Math.max(0, slots - 2)), 0, Math.max(0, row.journals.length - Math.max(1, slots)));
}
function selectJournal(group, index, track = state.track) {
  if (!state.ready || !groups.length || state.view !== 'selected') return;
  state.group = clamp(group, 0, groups.length - 1);
  state.track = track;
  const row = selectedRow();
  row.sel = clamp(index, 0, groups[state.group].journals.length - 1);
  row.scroll.to = scrollTarget(row.sel, groups[state.group]);
  AR.hover = -1;
  updateJournal(); wake();
}
function moveJournal(delta) {
  if (!groups.length) return;
  const slots = activeSlots(state.group), position = slots.indexOf(selectedRow().sel);
  selectJournal(state.group, slots[clamp(position + delta, 0, slots.length - 1)]);
}
function moveGroup(delta) {
  if (!groups.length) return;
  const track = state.track + delta, group = groupAt(track);
  selectJournal(group, rows[group].sel, track);
}
function button(text, label, action) {
  const node = document.createElement('button'); node.type = 'button';
  node.textContent = text; node.setAttribute('aria-label', label);
  node.addEventListener('click', action); return node;
}
function updateJournal() {
  const j = selectedJournal(), group = groups[state.group];
  $('journal-group-label').textContent = group.name;
  layoutJournalTitle();
  $('ghost-subject').textContent = group.name.toUpperCase();
  $('profile-identity').textContent = j.issn_l || 'UNMAPPED';
  $('profile-publisher').textContent = j.publisher || '未配置';
  $('profile-service').textContent = j.service || '未配置';
  $('paper-count').textContent = `${pad(j.papers.length)} RECORDS`;
  const slots = activeSlots(state.group);
  $('selection-number').textContent = pad(slots.indexOf(selectedRow().sel) + 1);
  $('selection-total').textContent = `/ ${pad(slots.length)}`;
  $('group-number').textContent = `SUBJECT ${pad(state.group + 1)} / ${pad(groups.length)}`;
  $('group-name').textContent = group.name;
  for (const [issn, list] of lists) list.hidden = issn !== j.key;
  // Keep each Journal's actual list DOM and scroll position across selections.
  if ($('journal-ticks').dataset.group !== String(state.group)) {
    $('journal-ticks').dataset.group = state.group;
    $('journal-ticks').replaceChildren(...slots.map(i => {
      const journal = group.journals[i];
      const tick = button('', `选择 ${journal.name}`, () => selectJournal(state.group, i));
      tick.title = `${journal.tier || group.name} / ${journal.name}`;
      if (slots.indexOf(i) && journal.tier !== group.journals[slots[slots.indexOf(i) - 1]].tier) tick.classList.add('tier-start');
      return tick;
    }));
    const tiers = [...new Set(group.journals.filter(Boolean).map(journal => journal.tier).filter(Boolean))];
    $('tiers').replaceChildren(...tiers.map(tier => button(tier, `跳转 ${tier}`, () =>
      selectJournal(state.group, group.journals.findIndex(journal => journal?.tier === tier)))));
  }
  [...$('journal-ticks').children].forEach((n, i) => n.setAttribute('aria-current', String(slots[i] === selectedRow().sel)));
  [...$('tiers').children].forEach(n => n.setAttribute('aria-current', String(n.textContent === j.tier)));
  $('previous-journal').disabled = selectedRow().sel === slots[0];
  $('next-journal').disabled = selectedRow().sel === slots.at(-1);
  for (const id of ['profile-publisher', 'profile-service']) $(id).title = $(id).textContent;
}
function layoutJournalTitle() {
  // Canvas measureText replaces Direct2D EstimateLineWrap; preserve the two
  // separately sized inverse plates from drawInfo(), including its 24px inset.
  const title = $('journal-title'), name = selectedJournal().name;
  title.title = name; title.setAttribute('aria-label', name);
  ctx.save(); ctx.font = '600 25px "Geist Mono"';
  const words = name.split(' '), width = Math.max(1, title.clientWidth - 24);
  let n = 1;
  while (n < words.length && ctx.measureText(words.slice(0, n + 1).join(' ')).width <= width) n++;
  const lines = n === words.length ? [name] : [words.slice(0, n).join(' ') + ' ', words.slice(n).join(' ')];
  title.replaceChildren(...lines.map(line => { const span = document.createElement('span'); span.textContent = line; return span; }));
  ctx.restore();
}
function marquee(row, start) {
  const text = row.querySelector('.title-text'), clip = row.querySelector('.title-clip');
  const distance = text.scrollWidth - clip.clientWidth;
  row.classList.toggle('moving', start && distance > 1);
  row.style.setProperty('--travel', `${-Math.max(0, distance)}px`);
  row.style.setProperty('--duration', `${Math.max(5, distance / 35 + 2)}s`);
}
function createLists(journals = groups.flatMap(group => group.journals)) {
  for (const journal of journals) {
    if (!journal) continue;
    const existing=lists.get(journal.key), scroll=existing?.scrollTop || 0;
    const list = existing || document.createElement('div'); list.className = 'paper-list';
    list.dataset.journal = journal.key;
    if (!existing) list.hidden = true;
    list.replaceChildren();
    journal.papers.forEach((paper, i) => {
      const row = button('', paper.title, () => openView(paper.id, row));
      row.className = 'paper-row'; row.dataset.paperId = paper.id;
      const number = document.createElement('span'); number.className = 'row-number'; number.textContent = pad(i + 1);
      const clip = document.createElement('span'); clip.className = 'title-clip';
      const title = document.createElement('span'); title.className = 'title-text'; title.textContent = paper.title;
      clip.append(title);
      const date = document.createElement('time'); date.textContent = paper.publication_date || '—';
      if (paper.publication_date) date.dateTime = paper.publication_date;
      else date.setAttribute('aria-label', 'Publication date unavailable');
      row.append(number, clip, date);
      row.addEventListener('pointerenter', () => marquee(row, true));
      row.addEventListener('pointerleave', () => marquee(row, row.matches(':focus-visible')));
      row.addEventListener('focus', () => marquee(row, true));
      row.addEventListener('blur', () => marquee(row, row.matches(':hover')));
      list.append(row);
    });
    lists.set(journal.key, list); $('paper-lists').append(list);
    list.scrollTop=scroll;
  }
}

// RhinE lift / 60-frame Inspection, accelerated to .18 + .42 s.
function inspectionPose() {
  const turning = phaseTurn();
  if (!turning) return null;
  const g = geometry(), c = selectedRow().cases[selectedRow().sel];
  const [sx, sy] = projection(state.group, selectedRow().sel, c.lift.x + 2.2 * phaseLift(), c.gap.x);
  const start = [sx + (journalMeta.array.center[0] - journalMeta.array.origin[0]) * g.k,
                 sy + (journalMeta.array.center[1] - journalMeta.array.origin[1]) * g.k];
  const frame = turning * 59, i = Math.min(58, Math.floor(frame)), f = frame - i;
  const a = journalMeta.inspect[i], b = journalMeta.inspect[i + 1], crop = journalMeta.inspect[59].crop;
  const k0 = g.k * journalMeta.array_ppu / journalMeta.inspect_ppu;
  const frontK = Math.max(k0, Math.min(k0 * 2.05, W * .9 / crop[2], (H - 190) / crop[3]));
  const k = mix(k0, frontK, turning);
  const end = [stageWidth * .25, H * .45];
  const center = a.center.map((v, axis) => mix(v, b.center[axis], f));
  const destination = [end[0] - (crop[0] + crop[2] / 2) * frontK,
                       end[1] - (crop[1] + crop[3] / 2) * frontK];
  const ox = mix(start[0] - center[0] * k, destination[0], turning);
  const oy = mix(start[1] - center[1] * k, destination[1], turning);
  const q = key => a[key].map((point, n) => point.map((v, axis) => mix(v, b[key][n][axis], f)));
  return { ox, oy, k, i, f, a, b, q, bounds: {x:ox + crop[0] * k, y:oy + crop[1] * k, width:crop[2] * k, height:crop[3] * k} };
}
function drawInspection() {
  const pose = inspectionPose();
  if (!pose) return;
  const {ox, oy, k, i, f, a, b, q} = pose, journal = selectedJournal();
  const reveal = clamp((inspection.time - (inspection.duration - .18)) / .18);
  quadImage(covers.get(journal.key).image, q('cover'), ox, oy, k);
  if (reveal) quadImage(directoryCanvas, q('cover'), ox, oy, k, reveal);
  const sprite = (image, meta, alpha) => {
    const [x, y, w, h] = meta.crop;
    ctx.save(); ctx.globalAlpha = alpha;
    ctx.drawImage(image, ox + x * k, oy + y * k, w * k, h * k); ctx.restore();
  };
  sprite(inspectionFrames[i], a, 1);
  if (f > .001) sprite(inspectionFrames[i + 1], b, f);
  spine(q('label'), ox, oy, k, journal.issn_l || 'UNMAPPED');
  selectedCoverQuad = transformedQuad(q('cover'), ox, oy, k);
  ctx.fillStyle = ORANGE;
  selectedCoverQuad.forEach(([x,y]) => ctx.fillRect(x - 2, y - 2, 4, 4));
}
function stepInspection(dt) {
  const target = inspection.target ? inspection.duration : 0;
  if (inspection.time === target) return false;
  inspection.time = REDUCE_MOTION ? target : clamp(inspection.time + Math.sign(target - inspection.time) * dt, 0, inspection.duration);
  if (inspection.time === inspection.duration) { $('mode-label').textContent = 'JOURNAL'; renderEnrich(); }
  if (inspection.time === 0) finishReturn();
  return inspection.time !== target;
}
function openView(paperId = null, source = canvas) {
  if (state.view !== 'selected' || !groups.length) return;
  const journal = selectedJournal();
  state.view = 'journal'; state.pendingPaper = paperId || journal.papers[0].id; state.focusSource = source;
  $('run-start').hidden=true;
  inspection.target = 1; inspection.removeAfter = false;
  document.querySelector('.file-panel').hidden = true;
  $('selected-view').classList.add('inspecting');
  $('mode-label').textContent = 'INSPECTION';
  resetDirectory(true); renderPaper();
  $('journal-view').inert = false; reviewSurface.show(REDUCE_MOTION);
  fillAbstractPlaceholder(); wake();
}
function back() {
  if (state.view !== 'journal' || decisionPending || enrichment?.phase==='Saving') return;
  cancelRetrieval(); decryption.reset(null, true);
  inspection.target = 0;
  $('journal-view').inert = true;
  reviewSurface.hide(REDUCE_MOTION); renderEnrich();
  $('mode-label').textContent = 'RETURN';
  if (inspection.time === 0) finishReturn();
  wake();
}
function finishReturn() {
  reviewSurface.finish();
  $('journal-view').hidden = true;
  if (inspection.removeAfter) removeFinishedJournal();
  inspection.removeAfter = false;
  state.pendingPaper = null;
  $('selected-view').classList.remove('inspecting');
  $('run-start').hidden=false;
  syncNavigation();
  if (!groups.length) {
    state.view = 'empty'; $('empty-inbox').hidden = false;
    document.querySelector('.collection').hidden=true;
    $('mode-label').textContent = 'COMPLETE'; updateSnapshotCount(); syncNavigation(); return;
  }
  state.view = 'selected'; document.querySelector('.file-panel').hidden = false;
  updateJournal(); updateSnapshotCount();
  $('mode-label').textContent = 'BROWSE';
  syncNavigation();
  const source = state.focusSource?.isConnected ? state.focusSource : canvas;
  source.focus({preventScroll:true});
}
function resetDirectory(immediate = false) {
  const papers = selectedJournal().papers, index = papers.findIndex(p => p.id === state.pendingPaper);
  directoryScroll.to = clamp(index * 128 - 190, 0, Math.max(0, papers.length * 128 - 550));
  if (immediate || REDUCE_MOTION) { directoryScroll.x = directoryScroll.to; directoryScroll.v = 0; }
  paintDirectory();
}
function paintDirectory() {
  if (!groups.length) return;
  const journal = selectedJournal(), g = directoryContext;
  g.fillStyle = BG; g.fillRect(0,0,768,768);
  g.fillStyle = INK; g.font = '500 22px "Geist Mono"'; g.fillText('PAPER DIRECTORY',38,51);
  g.fillStyle = MUTED; g.font = '18px "Geist Mono"'; g.fillText(`${journal.issn_l || 'UNMAPPED'} / ${pad(journal.papers.length)} CANDIDATES`,38,88);
  g.fillStyle = INK; g.fillRect(38,111,692,3);
  directoryAreas = []; g.save(); g.beginPath(); g.rect(30,132,708,550); g.clip();
  journal.papers.forEach((paper,index) => {
    const y = 136 + index * 128 - directoryScroll.x;
    if (y + 116 < 132 || y > 682) return;
    const selected = paper.id === state.pendingPaper;
    g.fillStyle = selected ? INK : '#e3e3da'; g.fillRect(30,y,708,116);
    g.fillStyle = selected ? BG : INK; g.font = '18px "Geist Mono"'; g.fillText(pad(index + 1),46,y+30);
    g.font = '500 24px "Geist Mono", "MiSans"';
    const words = paper.title.split(/\s+/); let line = '', lines = [];
    for (const word of words) {
      if (g.measureText(`${line} ${word}`).width > 575 && line) { lines.push(line); line = word; }
      else line = line ? `${line} ${word}` : word;
    }
    if (line) lines.push(line);
    lines.slice(0,2).forEach((text,n) => g.fillText(text,91,y+31+n*29,610));
    g.fillStyle = selected ? '#bebfb3' : MUTED; g.font = '18px "Geist Mono"';
    g.fillText(paper.publication_date || 'DATE UNAVAILABLE',91,y+98);
    directoryAreas.push({id:paper.id,x:30,y:Math.max(132,y),width:708,height:Math.min(682,y+116)-Math.max(132,y)});
  });
  g.restore(); g.fillStyle = MUTED; g.font = '17px "Geist Mono"'; g.fillText('SCROLL / SELECT TO READ',38,730);
}
function directoryPoint(event) {
  if (selectedCoverQuad.length !== 4 || inspection.time !== inspection.duration) return null;
  const rect=canvas.getBoundingClientRect(), x=event.clientX-rect.left, y=event.clientY-rect.top;
  if (!contains(selectedCoverQuad,x,y)) return null;
  const [p,a,,b]=selectedCoverQuad, ax=a[0]-p[0], ay=a[1]-p[1], bx=b[0]-p[0], by=b[1]-p[1];
  const determinant=ax*by-ay*bx;
  return {x:((x-p[0])*by-(y-p[1])*bx)/determinant*768,y:(ax*(y-p[1])-ay*(x-p[0]))/determinant*768};
}
function directoryHit(event) {
  if (state.view !== 'journal' || !inspection.target || inspection.removeAfter) return null;
  const point = directoryPoint(event);
  return point && directoryAreas.find(a => point.x >= a.x && point.x <= a.x+a.width && point.y >= a.y && point.y <= a.y+a.height);
}
function selectPaper(id) {
  if (decisionPending || inspection.removeAfter || id === state.pendingPaper || enrichment?.phase==='Saving') return;
  cancelRetrieval(); state.pendingPaper = id;
  renderPaper();
  if(keptReview) window.dispatchEvent(new CustomEvent('kept-paper-selected',{detail:id}));
  else resetDirectory();
  wake();
}
function movePaper(delta) {
  const papers=reviewPapers();
  if (papers.length < 2) return;
  const index=papers.findIndex(p => p.id === state.pendingPaper);
  selectPaper(papers[(index + delta + papers.length) % papers.length].id);
}
function missingFields(paper) {
  const fields=[];
  if (!paper.abstract?.trim()) fields.push('abstract');
  if (!paper.authors.length) fields.push('authors');
  if (!paper.publication_date || paper.publication_date.length === 7) fields.push('publication_date');
  return fields;
}
function renderField(id, text, missing, bars=1) {
  const root=$(id); root.classList.remove('enrich-new','reveal-pending'); root.replaceChildren();
  if (!missing) { root.textContent=text; return; }
  const placeholder=document.createElement('span'); placeholder.className='missing-field';
  placeholder.setAttribute('aria-label','信息尚未获得');
  for (let i=0;i<bars;i++) { const bar=document.createElement('span'); bar.className='missing-ink'; placeholder.append(bar); }
  root.append(placeholder);
}
function renderPaper() {
  const paper=currentPaper(); if (!paper) return;
  decryption.reset(null,true);
  const detail=$('paper-detail'), changed=detail.dataset.paperId!==paper.id;
  const journal=reviewJournal(), papers=reviewPapers(), index=papers.indexOf(paper);
  $('view-title').textContent=journal.name;
  $('view-paper-title').textContent=paper.title; $('view-paper-id').textContent=paper.id;
  $('paper-position').textContent=`${pad(index+1)} / ${pad(papers.length)}`;
  for (const id of ['previous-paper','next-paper']) $(id).disabled=papers.length===1 || enrichment?.phase==='Saving';
  renderPaperFields(paper, ['abstract','authors','publication_date']);
  $('paper-doi').textContent=paper.doi || 'DOI unavailable';
  if (paper.doi) $('paper-doi').href=`https://doi.org/${encodeURI(paper.doi)}`; else $('paper-doi').removeAttribute('href');
  detail.dataset.paperId=paper.id;
  detail.classList.toggle('abstract-locked', !paper.abstract?.trim());
  if (changed) detail.scrollTop=0;
  fillAbstractPlaceholder(); renderEnrich();
}
function renderPaperFields(paper, fields) {
  if (fields.includes('authors')) {
    renderField('paper-authors',paper.authors.join(', '),!paper.authors.length);
  }
  if (fields.includes('publication_date')) {
    renderField('paper-date',paper.publication_date,!paper.publication_date);
    if (paper.publication_date?.length===7) {
      const precision=document.createElement('span'); precision.className='date-precision'; precision.textContent='MONTH ONLY'; $('paper-date').append(precision);
    }
  }
  if (fields.includes('abstract')) {
    renderField('paper-abstract',paper.abstract,!paper.abstract?.trim());
  }
}
function fillAbstractPlaceholder() {
  const placeholder=$('paper-abstract').querySelector('.missing-field');
  if (!placeholder || (state.view!=='journal' && !(keptReview && state.view==='kept'))) return;
  const root=$('paper-abstract'), metrics=abstractLineMetrics(root);
  root.style.setProperty('--abstract-ink-top',`${metrics.top}px`);
  root.style.setProperty('--abstract-ink-height',`${metrics.height}px`);
  root.style.setProperty('--abstract-line-height',`${metrics.lineHeight}px`);
  const height=root.getBoundingClientRect().height;
  const count=Math.max(1,Math.ceil((height-metrics.top)/metrics.lineHeight));
  placeholder.replaceChildren(...Array.from({length:count},(_,i) => {
    const bar=document.createElement('span'); bar.className='missing-ink';
    bar.style.setProperty('--abstract-line',i);
    if (i===count-1) bar.style.height=`${Math.max(0,height-metrics.top-i*metrics.lineHeight)}px`;
    return bar;
  }));
}
function updateEnrichedFields(fields) {
  const paper=currentPaper(), detail=$('paper-detail');
  decryption.reset(null,true);
  renderPaperFields(paper,fields);
  detail.classList.toggle('abstract-locked',!paper.abstract?.trim());
  fillAbstractPlaceholder();
  const targets={abstract:'paper-abstract',authors:'paper-authors',publication_date:'paper-date'};
  fields.forEach(field => $(targets[field]).classList.add('enrich-new','reveal-pending'));
  if (!REDUCE_MOTION) decryption.reset(detail,false);
  fields.forEach(field => $(targets[field]).classList.remove('reveal-pending'));
  renderEnrich(); wake();
}
function renderEnrich() {
  syncNavigation();window.dispatchEvent(new Event('enrich-state'));
  const paper=currentPaper(); if (!paper) return;
  const operation=enrichment?.paperId===paper.id ? enrichment : null;
  const phase=operation?.phase || paper.enrichPhase || 'Idle';
  const active=phase==='Retrieving' || phase==='Saving';
  for(const id of ['previous-paper','next-paper']) $(id).disabled=decisionPending || Boolean(snapshotApplying) || phase==='Saving' || reviewPapers().length===1;
  $('enrich-paper').hidden=paper.enriched || !missingFields(paper).length || active;
  $('enrich-paper').disabled=decisionPending || Boolean(snapshotApplying);
  $('cancel-enrich').hidden=phase!=='Retrieving';
  $('keep-paper').disabled=$('reject-paper').disabled=decisionPending || Boolean(snapshotApplying) || phase==='Saving' || inspection.time!==inspection.duration || !inspection.target;
  const status=$('enrich-status'); status.dataset.phase=phase; status.dataset.warning=String(paper.syncWarning || false);
  status.textContent=paper.enrichMessage==='Complete authentication or the challenge in Chrome'
    ? '请手动切换到 Chrome 任务窗口完成登录或验证。' : paper.enrichMessage || '';
  document.querySelectorAll('#paper-detail .missing-field, #paper-detail .date-precision').forEach(n=>n.classList.toggle('scanning',phase==='Retrieving'));
}
function enrichmentURL(operation) {return `/api/papers/${operation.paperId}/enrichment`;}
function cancelRetrieval() {
  const operation=enrichment;
  if(operation?.phase!=='Retrieving') return;
  operation.cancelled=true; clearTimeout(operation.timer); enrichment=null;
  operation.paper.enrichPhase='Cancelled';operation.paper.enrichMessage='正在取消检索…';
  // A decision waits for its cancellation, including an in-flight start response.
  if(operation.requestID)return cancelOperation(operation);
  return operation.startTask?.then(result=>{operation.requestID=result.request_id;return cancelOperation(operation);},error=>uiNext.notice(error.message));
}
async function cancelOperation(operation) {
  try {
    const result=await uiNext.request(`${enrichmentURL(operation)}/cancel`,{request_id:operation.requestID},{keepalive:true});
    operation.paper.enrichMessage=result.accepted?'已取消检索。':'检索已结束，正在刷新实际结果。';
    if(!result.accepted)await refreshWorkspace();
  } catch(error) {uiNext.notice(error.message);}
}
async function pollEnrich(operation) {
  if(enrichment!==operation || operation.cancelled) return;
  try {
    const result=await uiNext.request(`${enrichmentURL(operation)}/state?request_id=${encodeURIComponent(operation.requestID)}`);
    operation.phase=result.phase;operation.paper.enrichPhase=result.phase;operation.paper.enrichMessage=result.message;
    if(result.phase==='Completed') {
      const fields=Object.keys(result.saved || {});
      operation.paper.enriched=true;operation.paper.syncWarning=result.message.includes('sync failed');
      await refreshWorkspace();
      if(currentPaper()?.id===operation.paperId) {updateEnrichedFields(fields);if(!keptReview)paintDirectory();}
      return;
    }
    if(result.phase==='Failed' || result.phase==='Idle') {
      if(currentPaper()?.id===operation.paperId)renderEnrich();
      if(result.phase==='Idle')await refreshWorkspace();
      return;
    }
  } catch(error) {operation.paper.enrichMessage=error.message;}
  if(currentPaper()?.id===operation.paperId)renderEnrich();
  if(enrichment===operation && !operation.cancelled)operation.timer=setTimeout(()=>void pollEnrich(operation),1000);
}
async function startEnrich() {
  const paper=currentPaper();
  if(!paper || paper.enriched || decisionPending || snapshotApplying || ['Retrieving','Saving'].includes(enrichment?.phase))return;
  const operation=enrichment={paperId:paper.id,paper,phase:'Retrieving',requestID:null,cancelled:false};
  paper.enrichMessage='Retrieving missing fields…';renderEnrich();
  try {
    operation.startTask=uiNext.request(`${enrichmentURL(operation)}/start`,{});
    const result=await operation.startTask;
    operation.requestID=result.request_id;
    if(operation.cancelled)return;
    operation.phase=result.phase;paper.enrichPhase=result.phase;paper.enrichMessage=result.message;
    void pollEnrich(operation);
  } catch(error) {operation.phase='Failed';paper.enrichPhase='Failed';paper.enrichMessage=error.message;}
  if(currentPaper()?.id===paper.id)renderEnrich();
}
async function reviewPaper(status) {
  if (state.view!=='journal' || decisionPending || snapshotApplying || inspection.time!==inspection.duration || inspection.removeAfter || enrichment?.phase==='Saving') return;
  decisionPending=true;renderEnrich();
  await cancelRetrieval();decryption.reset(null,true);
  const journal=selectedJournal(), index=journal?.papers.findIndex(p => p.id===state.pendingPaper), paper=journal?.papers[index];
  if(!paper){decisionPending=false;renderEnrich();uiNext.notice('文献已改变，请刷新后继续。');return;}
  try {
    const result=await uiNext.request(`/api/ui-next/papers/${paper.id}/${status==='kept'?'keep':'reject'}`,{expected_status:paper.status});
    if(result.outcome!=='UPDATED')throw new Error(result.message);
    paper.status=result.resulting_status;
    if(result.message)uiNext.notice(result.message);
  } catch(error) {
    decisionPending=false;renderEnrich();uiNext.notice(error.message);return;
  }
  decisionPending=false;
  reviewedPapers.set(paper.id,status); journal.papers.splice(index,1);
  if (status==='kept') keptPapers.set(paper.id,{paper,journal:{key:journal.key,issn_l:journal.issn_l,name:journal.name}});
  const list=lists.get(journal.key);
  list.querySelector(`[data-paper-id="${paper.id}"]`)?.remove();
  [...list.children].forEach((node,i) => node.querySelector('.row-number').textContent=pad(i+1));
  updateSnapshotCount();
  if (!journal.papers.length) { inspection.removeAfter=true; back(); return; }
  state.pendingPaper=journal.papers[index % journal.papers.length].id;
  resetDirectory(); renderPaper(); wake();
}
function removeFinishedJournal() {
  const group=groups[state.group], row=selectedRow(), slot=row.sel, journal=group.journals[slot];
  lists.get(journal.key)?.remove(); lists.delete(journal.key);
  group.journals.splice(slot,1); row.cases.splice(slot,1);
  // Keep surviving cases at their current positions, then let their existing
  // gap springs close the removed slot. WHITE padding belongs at the tail.
  for (let i=slot;i<row.cases.length;i++) row.cases[i].gap.x+=1;
  if (group.journals.length) row.sel=slot<group.journals.length ? slot : 0;
  else {
    groups.splice(state.group,1); rows.splice(state.group,1);
    state.group=groups.length ? state.group % groups.length : 0;
    state.track=state.group; strip.x=strip.to=state.track; strip.v=0;
  }
  if (groups.length) selectedRow().scroll.to=scrollTarget(selectedRow().sel,groups[state.group]);
  AR.hover=-1;
  $('journal-ticks').dataset.group='';
}
function updateSnapshotCount() {
  const journals=groups.flatMap(g => g.journals.filter(Boolean)), count=journals.reduce((n,j) => n+j.papers.length,0);
  $('snapshot-label').textContent=`${journals.length} JOURNALS / ${count} CANDIDATES / WORKSPACE`;
  $('kept-count').textContent=pad(keptPapers.size);
}
$('previous-paper').addEventListener('click',() => movePaper(-1));
$('next-paper').addEventListener('click',() => movePaper(1));
$('keep-paper').addEventListener('click',() => reviewPaper('kept'));
$('reject-paper').addEventListener('click',() => reviewPaper('rejected'));
$('enrich-paper').addEventListener('click',startEnrich);
$('cancel-enrich').addEventListener('click',() => { cancelRetrieval(); renderEnrich(); });

$('back').addEventListener('click', back);
$('previous-journal').addEventListener('click', () => moveJournal(-1));
$('next-journal').addEventListener('click', () => moveJournal(1));
$('previous-group').addEventListener('click', () => moveGroup(-1));
$('next-group').addEventListener('click', () => moveGroup(1));
document.addEventListener('keydown', event => {
  if (window.uiNextSettings?.active() || !state.ready || event.altKey || event.metaKey || event.ctrlKey) return;
  if (event.key === 'Escape' && state.view === 'journal') { back(); event.preventDefault(); }
  else if (event.target === canvas && state.view === 'selected') {
    const action = { ArrowUp: () => moveJournal(-1), ArrowDown: () => moveJournal(1),
      ArrowLeft: () => moveGroup(-1), ArrowRight: () => moveGroup(1), Enter: () => openView() }[event.key];
    if (action) { action(); event.preventDefault(); }
  }
});
canvas.addEventListener('wheel', event => {
  if (!state.ready || !groups.length) return;
  event.preventDefault();
  if (state.view === 'journal') {
    if (inspection.time === inspection.duration && !inspection.removeAfter && directoryPoint(event)) {
      directoryScroll.to = clamp(directoryScroll.to + event.deltaY * .7, 0, Math.max(0, selectedJournal().papers.length * 128 - 550));
      wake();
    }
    return;
  }
  if (performance.now() - lastWheel < 160) return;
  lastWheel = performance.now();
  if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) moveGroup(Math.sign(event.deltaX));
  else moveJournal(Math.sign(event.deltaY));
}, { passive: false });
// Same point-in-polygon hit testing as the existing Canvas adaptation.
function contains(q, x, y) {
  let inside = false;
  for (let i = 0, j = q.length - 1; i < q.length; j = i++) {
    const [xi, yi] = q[i], [xj, yj] = q[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function hit(event) {
  const r = canvas.getBoundingClientRect();
  return [...hitAreas].reverse().find(h => contains(h.quad, event.clientX - r.left, event.clientY - r.top));
}
canvas.addEventListener('pointermove', event => {
  if (state.view === 'journal') {
    canvas.style.cursor = directoryHit(event) ? 'pointer' : 'default'; return;
  }
  const h = hit(event), next = h && !h.empty && h.track === state.track ? h.index : -1;
  canvas.style.cursor = h && !h.empty ? 'pointer' : 'default';
  if (next !== AR.hover) { AR.hover = next; wake(); }
});
canvas.addEventListener('pointerleave', () => { AR.hover = -1; wake(); });
canvas.addEventListener('click', event => {
  if (!state.ready || !groups.length) return;
  if (state.view === 'journal') {
    const paper = directoryHit(event); if (paper && inspection.time === inspection.duration && inspection.target) selectPaper(paper.id);
    return;
  }
  const h = hit(event);
  if (!h || h.empty) return;
  if (h.track === state.track && h.index === selectedRow().sel) openView();
  else selectJournal(h.group, h.index, h.track);
});
mediaQuery.addEventListener('change', event => {
  REDUCE_MOTION = event.matches;
  if (REDUCE_MOTION) reviewSurface.finish();
  wake();
});

async function image(url) { const img = new Image(); img.src = url; await img.decode(); return img; }
async function json(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); }
function coverPair(journal, source) {
  // RhinE lib/album.js: crop the centered square, preserving proportions.
  const square = document.createElement('canvas'); square.width = square.height = 512;
  const g = square.getContext('2d'); g.fillStyle = '#deddd5'; g.fillRect(0, 0, 512, 512);
  if (source) {
    const side = Math.min(source.width, source.height);
    g.drawImage(source, (source.width - side) / 2, (source.height - side) / 2, side, side, 0, 0, 512, 512);
  } else {
    g.strokeStyle = '#a9ab9f'; g.strokeRect(26, 26, 460, 460);
    g.fillStyle = INK; g.font = '500 26px "Geist Mono"';
    const words = journal.name.split(' '); let line = '', y = 180;
    for (const word of words) {
      if (g.measureText(`${line} ${word}`).width > 400) { g.fillText(line, 50, y); line = word; y += 40; }
      else line = line ? `${line} ${word}` : word;
    }
    g.fillText(line, 50, y); g.font = '18px "Geist Mono"';
    g.fillText(journal.issn_l || 'UNMAPPED', 50, 420); g.fillStyle = MUTED; g.fillText('COVER UNAVAILABLE', 50, 455);
  }
  // RhinE draws blurred thumbnails under frost, and blends the sharp image in.
  const blur = document.createElement('canvas'); blur.width = blur.height = 128;
  const b = blur.getContext('2d'); b.filter = 'blur(14px)'; b.drawImage(square, -4, -4, 136, 136);
  return { image: square, blur, sourceLoaded: !!source };
}
function paperObjects() {
  return new Map([...groups.flatMap(g=>g.journals.flatMap(j=>j.papers)),...keptPapers.values()].map(item=>{const p=item.paper || item;return [p.id,p];}));
}
async function applySnapshot(data,selection=window.inboxRun?.selection()) {
  const objects=paperObjects(), selected=state.pendingPaper, wasKept=state.view==='kept';
  const oldSelections=new Map(groups.map((g,i)=>[g.name,g.journals[rows[i]?.sel]?.key]));
  const oldRows=new Map(groups.map((g,i)=>[g.name,{row:rows[i],cases:new Map(g.journals.map((j,n)=>[j.key,rows[i].cases[n]]))}]));
  const scrolls=new Map([...lists].map(([key,list])=>[key,list.scrollTop]));
  const merge=paper=>{const existing=objects.get(paper.id);if(existing){Object.assign(existing,paper);return existing;}return paper;};
  for(const group of data.subjects)for(const journal of group.journals){
    journal.papers=journal.papers.map(merge);
    const source=journal.cover?await image(journal.cover).catch(()=>null):null;
    covers.set(journal.key,coverPair(journal,source));
  }
  groups=data.subjects; keptSnapshot=data.kept;
  if(wasKept)inboxViewBeforeKept=groups.length?'selected':'empty';
  keptPapers.clear();for(const entry of data.kept.papers){entry.paper=merge(entry.paper);keptPapers.set(entry.paper.id,entry);}
  rows=groups.map(g=>({sel:Math.max(0,g.journals.findIndex(j=>j.key===oldSelections.get(g.name))),scroll:oldRows.get(g.name)?.row.scroll || Spring(0,9),
    cases:g.journals.map(j=>oldRows.get(g.name)?.cases.get(j.key) || {lift:Spring(0,13),clear:Spring(0,8),gap:Spring(0,11)})}));
  const gi=groups.findIndex(g=>g.journals.some(j=>j.key===selection?.issn));state.group=gi>=0?gi:Math.min(state.group,Math.max(0,groups.length-1));
  if(gi>=0)rows[gi].sel=groups[gi].journals.findIndex(j=>j.key===selection.issn);
  state.track=state.group;strip.x=strip.to=state.track;strip.v=0;
  for(const [key,list] of lists)if(!groups.some(g=>g.journals.some(j=>j.key===key))){list.remove();lists.delete(key);}
  createLists();for(const [key,scroll]of scrolls)if(lists.has(key))lists.get(key).scrollTop=scroll;
  document.querySelector('.collection').hidden=!groups.length;
  $('empty-inbox').hidden=groups.length>0;
  if(keptReview){
    const entries=keptReview.entries.map(e=>keptPapers.get(e.paper.id)).filter(Boolean);
    if(entries.length){keptReview.entries=entries;state.pendingPaper=entries.some(e=>e.paper.id===selected)?selected:entries[0].paper.id;}
    else window.dispatchEvent(new Event('kept-reader-empty'));
  }
  if(!wasKept && state.view==='journal' && !groups.some(g=>g.journals.some(j=>j.papers.some(p=>p.id===selected)))) {
    inspection.time=inspection.target=0;state.pendingPaper=null;reviewSurface.finish();$('journal-view').hidden=true;$('selected-view').classList.remove('inspecting');state.view=groups.length?'selected':'empty';
  }
  if(!wasKept && state.view!=='journal')state.view=groups.length?'selected':'empty';
  document.querySelector('.file-panel').hidden=!groups.length || state.view==='journal';
  $('journal-ticks').dataset.group='';
  if(groups.length)updateJournal();
  if(currentPaper() && (state.view==='journal' || keptReview)){renderPaper();if(!keptReview)resetDirectory();}
  updateSnapshotCount();resize();syncNavigation();wake();
  window.dispatchEvent(new Event('kept-data-changed'));
  if(data.issues.length)uiNext.notice(data.issues.join(' · '));
}
async function refreshWorkspace(selection) {
  if(snapshotApplying)return snapshotApplying;
  snapshotApplying=(async()=>{const data=await uiNext.workspace();await applySnapshot(data,selection);return data;})();
  renderEnrich();
  try{return await snapshotApplying;}finally{snapshotApplying=false;renderEnrich();}
}
async function load() {
  try {
    const [data,meta,rail]=await Promise.all([uiNext.workspace(),json('assets/journal/case.json'),json('assets/rail/rail.json')]);
    journalMeta=meta;railMeta=rail;
    await document.fonts.ready;
    [arrayClear,arrayFrost]=await Promise.all(['clear','frost'].map(n=>image(`assets/journal/array-${n}.png`)));
    const rails=await Promise.all(['front','mid','back'].map(n=>image(`assets/rail/rail-${n}.png`)));
    railImages=Object.fromEntries(['front','mid','back'].map((n,i)=>[n,rails[i]]));
    inspectionFrames=await Promise.all(Array.from({length:60},(_,i)=>image(`assets/journal/inspect-${pad(i)}.png`)));
    await applySnapshot(data);state.ready=true;$('run-start').disabled=false;$('load-status').hidden=true;resize();wake();
    const initial=window.uiNextInitialLink;
    if(initial.view==='inbox' && initial.paper){
      const group=groups.findIndex(g=>g.journals.some(j=>j.papers.some(p=>p.id===initial.paper)));
      if(group>=0){
        const journal=groups[group].journals.findIndex(j=>j.papers.some(p=>p.id===initial.paper));
        selectJournal(group,journal,group);openView(initial.paper);
      }else uiNext.notice('该 Paper 不在当前 Inbox 中，可能已移出待审阅列表。');
    }
    const pending=[...paperObjects().values()].find(p=>['Retrieving','Saving'].includes(p.enrichPhase) && p.enrichment_request_id);
    if(pending){enrichment={paperId:pending.id,paper:pending,phase:pending.enrichPhase,requestID:pending.enrichment_request_id,cancelled:false};void pollEnrich(enrichment);}
    window.dispatchEvent(new Event('inbox-ready'));
  }catch(error){$('load-status').textContent=`读取失败：${error.message}`;uiNext.notice(error.message);}
}
let inboxViewBeforeKept='selected';
let inboxScrollBeforeKept=0;
function leaveInboxReview() {
  if(state.view!=='journal') return;
  cancelRetrieval();decryption.reset(null,true);
  inspection.target=inspection.time=0;finishReturn();
}
window.inboxKept={
  available:()=>state.ready && !decisionPending && enrichment?.phase!=='Saving' && !$('run-view').open &&
    (['selected','empty'].includes(state.view) || (state.view==='journal' && inspection.time===inspection.duration && inspection.target)),
  busy:()=>decisionPending || Boolean(snapshotApplying) || enrichment?.phase==='Saving',
  data:()=>keptSnapshot,
  papers:()=>[...keptPapers.values()],
  open() {
    if(!this.available()) return false;
    leaveInboxReview();
    inboxScrollBeforeKept=lists.get(selectedJournal()?.key)?.scrollTop || 0;
    inboxViewBeforeKept=state.view;state.view='kept';
    $('selected-view').hidden=true;$('kept-view').hidden=false;
    $('run-start').hidden=true;
    $('mode-label').textContent='KEPT';syncNavigation();return true;
  },
  close() {
    if(this.busy()) return false;
    this.closeReader();state.view=inboxViewBeforeKept;
    $('selected-view').hidden=false;$('kept-view').hidden=true;
    $('run-start').hidden=false;
    $('mode-label').textContent=groups.length?'BROWSE':'COMPLETE';
    resize();const list=lists.get(selectedJournal()?.key);
    if(list) list.scrollTop=inboxScrollBeforeKept;
    wake();$('inbox-open').focus({preventScroll:true});return true;
  },
  showInbox() {if(!this.busy()) {leaveInboxReview();syncNavigation();}},
  openReader(entries,id) {
    if(this.busy()) return false;
    cancelRetrieval();decryption.reset(null,true);
    keptReview={entries};state.pendingPaper=id;
    $('kept-reader-body').append(reviewPanel);
    document.querySelector('.paper-navigation>span').textContent='Paper / Kept';
    renderPaper();wake();return true;
  },
  closeReader() {
    if(this.busy()) return false;
    if(keptReview) {
      cancelRetrieval();decryption.reset(null,true);keptReview=null;state.pendingPaper=null;
      $('journal-view').append(reviewPanel);
      document.querySelector('.paper-navigation>span').textContent='Paper / Candidate';

    }
    return true;
  },
  exported(ids) {
    for(const id of ids) {keptPapers.delete(id);reviewedPapers.set(id,'exported');}
    $('kept-count').textContent=pad(keptPapers.size);
  },
};
new ResizeObserver(()=>{if(keptReview && state.view==='kept') fillAbstractPlaceholder();}).observe($('kept-reader'));
window.inboxRun={
  available:()=>state.ready && !decisionPending && !snapshotApplying && ['selected','empty'].includes(state.view) && inspection.time===0,
  selection:()=>({issn:selectedJournal()?.key,scroll:lists.get(selectedJournal()?.key)?.scrollTop || 0}),
  prepare:async(_papers,selection)=>{await refreshWorkspace(selection);},
};
window.uiWorkspace={refresh:refreshWorkspace,ready:()=>state.ready};
window.addEventListener('focus',()=>{if(!window.uiNextSettings?.active() && state.ready && !decisionPending && !['Retrieving','Saving'].includes(enrichment?.phase))void refreshWorkspace().catch(error=>uiNext.notice(error.message));});
window.addEventListener('pagehide',()=>{cancelRetrieval();});
// Suspend presentation once across all three configuration pages.
let settingsSuspension=null;
window.uiNextSettings={
  active:()=>Boolean(settingsSuspension),
  currentView:()=>settingsSuspension?.page || null,
  refreshNavigation:syncNavigation,
  refreshWorkspace:async()=>{
    if(state.ready)await refreshWorkspace();
    else {await load();if(!state.ready)throw new Error($('load-status').textContent);}
    if(settingsSuspension)settingsSuspension.workspaceChanged=true;
  },
  open(page='settings') {
    if(decisionPending || snapshotApplying || enrichment?.phase==='Saving' || $('run-view').open || $('kept-flow').open ||
      (inspection.target ? inspection.time!==inspection.duration : inspection.time!==0))return false;
    if(settingsSuspension){settingsSuspension.page=page;syncNavigation();return true;}
    cancelRetrieval();renderEnrich();
    const nodes=[$('selected-view'),$('kept-view'),document.querySelector('footer'),$('run-start'),document.querySelector('.message-region')];
    settingsSuspension={page,origin:state.view==='kept'?'kept':'inbox',nodes:nodes.map(node=>({node,hidden:node.hidden}))};
    nodes.forEach(node=>{node.hidden=true;node.inert=true;});syncNavigation();return true;
  },
  close() {
    if(!settingsSuspension)return null;
    const saved=settingsSuspension;settingsSuspension=null;
    saved.nodes.forEach(({node,hidden})=>{node.hidden=hidden;node.inert=false;});
    resize();wake();syncNavigation();
    if(saved.workspaceChanged)window.dispatchEvent(new Event('kept-data-changed'));
    return saved.origin;
  },
};
window.inboxStudy = {
  snapshot: () => ({ ...state, focusSource: undefined, selected: state.ready && groups.length ? selectedJournal().key : null,
    strip: { position: strip.x, target: strip.to }, coverQuad: selectedCoverQuad,
    rows: rows.map(row => ({ selected: row.sel, scroll: row.scroll.x })),
    hitAreas: hitAreas.map(h => ({ ...h })), moving: running,
    loadedCovers: [...covers.values()].filter(c => c.sourceLoaded).length,
    caseBounds: state.ready && groups.length ? inspectionPose()?.bounds : null,
    inspection: inspection.time, directoryScroll: directoryScroll.x,
    directoryAreas: directoryAreas.map(a => ({ ...a })),
    paper: state.ready && groups.length ? currentPaper()?.id : null,
    candidates: groups.flatMap(g => g.journals.filter(Boolean)).reduce((n, j) => n + j.papers.length, 0),
    journals: groups.flatMap(g => g.journals.filter(Boolean)).length,
    reviewed: [...reviewedPapers], enriching: enrichment ? {paperId: enrichment.paperId, phase: enrichment.phase} : null,
    revealing: decryption.active }),
};
load();
