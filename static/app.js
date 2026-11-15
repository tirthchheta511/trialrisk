/* TrialRisk: the Protocol Review Desk. Plain JavaScript, no libraries. */
const $ = (s, el = document) => el.querySelector(s);
const NS = 'http://www.w3.org/2000/svg';
const state = { summary: null, showcase: [], current: null, base: null, mode: 'showcase', nct: null, design: null, dirty: {}, timer: null, seq: 0 };

/* ---------- small helpers ---------- */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = r => (r < 0.01 ? 'under 1%' : `${Math.round(r * 100)}%`);
const pct1 = v => `${(+v).toFixed(1)}%`;
const lc = s => (/^[A-Z]{2}/.test(s) ? s : s.charAt(0).toLowerCase() + s.slice(1));
const signed = v => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(3)}`;
const num = n => Number(n).toLocaleString('en-US');
const words = s => String(s || '').replace(/_/g, ' ').toLowerCase().replace(/^./, c => c.toUpperCase());
const phaseName = p => ({ NA: 'No phase (device, behavioral, etc.)', EARLY_PHASE1: 'Early phase 1' }[p] || String(p || 'Not given').replace(/PHASE(\d)/g, 'Phase $1').replace(/\|/g, ' + '));
const statusName = s => ({ COMPLETED: 'Completed', TERMINATED: 'Terminated', WITHDRAWN: 'Withdrawn', RECRUITING: 'Recruiting',
  NOT_YET_RECRUITING: 'Not yet recruiting', ACTIVE_NOT_RECRUITING: 'Active, not recruiting', ENROLLING_BY_INVITATION: 'Enrolling by invitation' }[s] || words(s));
const SPONSOR_TYPES = { INDUSTRY: 'Industry', OTHER: 'University, hospital or other', NIH: 'NIH', OTHER_GOV: 'Other government', FED: 'US federal agency',
  NETWORK: 'Research network', INDIV: 'Individual' };

async function api(path) {
  const r = await fetch(path);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Something went wrong. Please try again.');
  return body;
}
function say(text, error = false) { const m = $('#message'); m.textContent = text || ''; m.classList.toggle('error', error); }

/* ---------- the protocol sheet ---------- */
const fact = (label, feat, get) => ({ label, feat, get: get || (d => d.facts?.[[].concat(feat)[0]] ?? '—') });
const SECTIONS = [
  { title: 'Sponsor', match: c => c.startsWith('sponsor_') || c === 'n_collaborators', facts: [
    fact('Lead sponsor', 'sponsor_name', d => d.sponsor || '—'),
    fact('Sponsor type', 'sponsor_class', d => SPONSOR_TYPES[d.levers?.sponsor_type] || d.facts?.sponsor_class || '—'),
    fact('Earlier trials that ended', 'sponsor_prior_ended'),
    fact('Share of those that stopped early', 'sponsor_prior_stop_rate'),
    fact('Collaborators', 'n_collaborators')] },
  { title: 'Design', match: c => ['phase', 'allocation', 'masking', 'intervention_model', 'primary_purpose', 'n_arms', 'n_interventions'].includes(c)
      || c.startsWith('type_') || c.startsWith('title_'), facts: [
    fact('Phase', 'phase', d => d.phase || '—'),
    fact('Randomized', 'allocation'), fact('Blinding', 'masking'), fact('Study design', 'intervention_model'),
    fact('Main purpose', 'primary_purpose'), fact('Study arms', 'n_arms'), fact('Treatments', 'n_interventions')] },
  { title: 'Who can join', match: c => c.startsWith('elig_') || ['n_inclusion_rules', 'n_exclusion_rules', 'n_numbers_in_rules', 'min_age_years', 'max_age_years',
      'age_span_years', 'includes_children', 'includes_older_adults', 'healthy_volunteers', 'sex'].includes(c), facts: [
    fact('Inclusion rules', 'n_inclusion_rules'), fact('Exclusion rules', 'n_exclusion_rules'),
    fact('Length of the rules', ['elig_length', 'n_numbers_in_rules'], d => d.facts?.elig_length ? `${d.facts.elig_length} characters` : '—'),
    fact('Ages', ['min_age_years', 'max_age_years', 'age_span_years', 'includes_children', 'includes_older_adults'], d => ages(d)),
    fact('Healthy volunteers', 'healthy_volunteers')] },
  { title: 'What it measures', match: c => c === 'n_primary_outcomes' || c === 'n_secondary_outcomes', facts: [
    fact('Primary outcomes', 'n_primary_outcomes'), fact('Secondary outcomes', 'n_secondary_outcomes')] },
  { title: 'Oversight', match: c => ['has_dmc', 'fda_drug', 'fda_device'].includes(c), facts: [
    fact('Data monitoring committee', 'has_dmc'), fact('FDA-regulated drug', 'fda_drug'), fact('FDA-regulated device', 'fda_device')] },
  { title: 'The protocol text', match: c => c === 'ai_text_score' || c.startsWith('area_') || ['summary_length', 'title_length', 'n_conditions', 'n_keywords', 'start_year'].includes(c), facts: [
    fact('Conditions', 'n_conditions', d => d.conditions || '—'),
    fact('Disease area', 'area', d => areas(d)),
    fact('AI reading of the text', 'ai_text_score', d => (d.effects?.ai_text_score ?? 0) > 0 ? 'reads like trials that stopped' : 'reads like trials that finished')] },
];
function ages(d) {
  const lo = d.facts?.min_age_years, hi = d.facts?.max_age_years;
  if ((!lo || lo === 'not given') && (!hi || hi === 'not given')) return 'not given';
  if (!hi || hi === 'not given') return `${lo} and older`;
  if (!lo || lo === 'not given') return `up to ${hi}`;
  return `${lo} to ${hi}`;
}
function areas(d) {
  const a = Object.entries(d.facts || {}).filter(([k, v]) => k.startsWith('area_') && v === 'yes').map(([k]) => words(k.slice(5)));
  return a.length ? a.join(', ') : 'not mapped';
}
function underline(el, dir) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 100 9'); svg.setAttribute('preserveAspectRatio', 'none');
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', dir === 'up' ? 'M1 5 C 18 1, 30 8, 50 4 S 82 1, 99 6' : 'M1 6 C 20 8, 36 2, 55 5 S 84 8, 99 3');
  p.setAttribute('vector-effect', 'non-scaling-stroke');
  svg.appendChild(p); el.appendChild(svg);
}

function renderSheet(d, opts = {}) {
  const sheet = $('#sheet');
  $('#title').textContent = d.title || 'Untitled trial';
  $('#kind').textContent = opts.kind || '';
  const id = d.nct_id || '';
  $('#m-id').innerHTML = /^NCT\d{8}$/.test(id) ? `<a href="https://clinicaltrials.gov/study/${id}" target="_blank" rel="noopener">${id}</a>` : esc(id);
  $('#m-status').textContent = statusName(d.status);
  $('#m-start').textContent = d.start || '—';
  $('#m-cond').textContent = (d.conditions || '—').split(',')[0];
  $('#m-cond').title = d.conditions || '';

  const up = d.raises_risk || [], down = d.lowers_risk || [];
  const marked = new Map([...up.map(r => [r.feature, 'up']), ...down.map(r => [r.feature, 'down'])]);
  const verdict = [`Stamped ${d.band.toLowerCase()} risk: a ${pct(d.risk)} chance of stopping early. The average trial has ${pct(d.average_risk)}.`];
  if (up.length) verdict.push(`Biggest worries: ${up.slice(0, 2).map(r => lc(r.label)).join(' and ')}.`);
  if (down.length) verdict.push(`In its favour: ${lc(down[0].label)}.`);
  $('#verdict').textContent = verdict.join(' ');

  const box = $('#sections'); box.innerHTML = '';
  SECTIONS.forEach((s, i) => {
    const sec = document.createElement('div'); sec.className = 'sec';
    const body = document.createElement('div'); body.className = 'sec-body';
    body.innerHTML = `<span class="sec-num">${i + 1}</span><h4>${s.title}</h4>`;
    const dl = document.createElement('dl'); dl.className = 'facts';
    s.facts.forEach(f => {
      const dt = document.createElement('dt'); dt.textContent = f.label;
      const dd = document.createElement('dd');
      const span = document.createElement('span'); span.textContent = f.get(d);
      const keys = f.feat === 'area' ? [...marked.keys()].filter(k => k.startsWith('area_')) : [].concat(f.feat);
      const dir = keys.map(k => marked.get(k)).find(Boolean);
      if (dir) { span.className = `mark ${dir}`; underline(span, dir); }
      dd.appendChild(span); dl.append(dt, dd);
    });
    body.appendChild(dl);
    const mine = [...up.map(r => ({ ...r, dir: 'up' })), ...down.map(r => ({ ...r, dir: 'down' }))].filter(r => s.match(r.feature));
    const note = document.createElement('aside'); note.className = 'note';
    if (mine.length) {
      const net = mine.reduce((a, r) => a + r.effect, 0);
      note.classList.add(net >= 0 ? 'up' : 'down');
      note.innerHTML = mine.map(r => `<p class="why-${r.dir}"><strong>${r.dir === 'up' ? '▲ Raises risk' : '▼ Lowers risk'}</strong><br>${esc(r.label)}: ${esc(r.value)}</p>`).join('');
    } else {
      note.innerHTML = '<p class="quiet">Nothing here moves the risk much.</p>';
    }
    sec.append(body, note); box.appendChild(sec);
  });

  const out = $('#outcome');
  if (opts.actual) {
    out.hidden = false; out.className = `outcome ${opts.right ? 'right' : 'wrong'}`;
    out.innerHTML = `<h4>What really happened</h4><p>${esc(opts.actual)}</p>`;
  } else out.hidden = true;
  stamp(d, opts.was);
}

function stamp(d, was) {
  const st = $('#stamp');
  st.className = `stamp band-${d.band}`;
  $('#stamp-band').textContent = `${d.band} risk`;
  $('#stamp-pct').textContent = d.risk < 0.01 ? '<1%' : pct(d.risk);
  $('#stamp-was').textContent = was != null && Math.abs(was - d.risk) >= 0.005 ? `was ${pct(was)}` : '';
  st.setAttribute('aria-label', `Stamped ${d.band} risk, ${pct(d.risk)} chance of stopping early`);
  void st.offsetWidth; st.classList.add('slam');
}

/* ---------- precedents ---------- */
function renderPrecedents(d) {
  const sec = $('.precedents');
  if (!d.similar) { sec.hidden = true; return; }
  sec.hidden = false;
  const n = d.similar_checked, s = d.similar_stopped;
  const avg = state.summary ? state.summary.data.stop_rate * 100 : 15;
  const reasons = Object.entries(d.similar_reasons || {}).sort((a, b) => b[1] - a[1]);
  $('#prec-sum').textContent = `${s} of the ${n} most similar past trials stopped early (${Math.round(100 * s / n)}%). Across all trials it is ${Math.round(avg)}%.`
    + (reasons.length ? ` Most common reason here: ${reasons[0][0].toLowerCase()}.` : '');
  $('#dots').innerHTML = Array.from({ length: n }, (_, i) => `<span class="${i < s ? 'stopped' : ''}"></span>`).join('');
  $('#cards').innerHTML = d.similar.map(c => {
    const stopped = c.status !== 'COMPLETED';
    const st = stopped ? (c.status === 'WITHDRAWN' ? '▲ Withdrawn before it started' : '▲ Stopped early') : '▼ Finished as planned';
    return `<div class="card"><span class="st ${stopped ? 'stopped' : 'finished'}">${st}</span>
      <a class="t" href="https://clinicaltrials.gov/study/${esc(c.nct_id)}" target="_blank" rel="noopener">${esc(c.title)}</a>
      ${stopped && c.why_stopped ? `<span class="w">"${esc(c.why_stopped)}"</span>` : ''}
      <span class="sim">${esc(c.nct_id)}, started ${esc(c.start_year)}, ${Math.round(c.similarity * 100)}% similar text</span></div>`;
  }).join('');
}

/* ---------- redesign levers ---------- */
const LEVERS = [
  { key: 'phase', label: 'Phase', type: 'select', options: ['EARLY_PHASE1', 'PHASE1', 'PHASE1|PHASE2', 'PHASE2', 'PHASE2|PHASE3', 'PHASE3', 'PHASE4', 'NA'], name: phaseName },
  { key: 'allocation', label: 'Randomized', type: 'select', options: ['RANDOMIZED', 'NON_RANDOMIZED', 'NA'], name: v => ({ RANDOMIZED: 'Randomized', NON_RANDOMIZED: 'Not randomized', NA: 'One group only' }[v]) },
  { key: 'masking', label: 'Blinding', type: 'select', options: ['NONE', 'SINGLE', 'DOUBLE', 'TRIPLE', 'QUADRUPLE'], name: v => ({ NONE: 'None (open label)' }[v] || words(v)) },
  { key: 'exclusion_rules', label: 'Exclusion rules', type: 'range', min: 0, max: 80 },
  { key: 'inclusion_rules', label: 'Inclusion rules', type: 'range', min: 0, max: 60 },
  { key: 'arms', label: 'Study arms', type: 'number', min: 1, max: 20 },
  { key: 'primary_outcomes', label: 'Primary outcomes', type: 'number', min: 0, max: 30 },
  { key: 'secondary_outcomes', label: 'Secondary outcomes', type: 'number', min: 0, max: 80 },
  { key: 'collaborators', label: 'Collaborators', type: 'number', min: 0, max: 30 },
  { key: 'sponsor', label: 'Lead sponsor', type: 'text' },
  { key: 'dmc', label: 'Data monitoring committee', type: 'toggle' },
  { key: 'fda_drug', label: 'FDA-regulated drug', type: 'toggle' },
  { key: 'healthy_volunteers', label: 'Accepts healthy volunteers', type: 'toggle' },
];
function renderLevers(lv) {
  const form = $('#levers'); form.innerHTML = '';
  state.dirty = {};
  LEVERS.forEach(L => {
    const v = lv?.[L.key];
    const wrap = document.createElement('label'); wrap.className = `lever ${L.type === 'toggle' ? 'toggle' : ''}`;
    let input;
    if (L.type === 'select') {
      input = document.createElement('select');
      if (!L.options.includes(v)) input.add(new Option('Not given', '', true, true));
      L.options.forEach(o => input.add(new Option(L.name(o), o, false, o === v)));
    } else if (L.type === 'toggle') {
      input = document.createElement('input'); input.type = 'checkbox'; input.checked = v === true;
    } else {
      input = document.createElement('input'); input.type = L.type;
      if (L.type !== 'text') { input.min = L.min; input.max = L.max; input.step = 1; }
      input.value = v ?? (L.type === 'text' ? '' : L.min);
      if (L.type === 'text') input.maxLength = 120;
    }
    input.name = L.key;
    if (L.type === 'toggle') { wrap.append(input, Object.assign(document.createElement('span'), { textContent: L.label })); }
    else {
      const head = document.createElement('span'); head.textContent = L.label;
      if (L.type === 'range') { const o = document.createElement('output'); o.textContent = input.value; head.appendChild(o);
        ['input', 'change'].forEach(ev => input.addEventListener(ev, () => { o.textContent = input.value; })); }
      wrap.append(head, input);
    }
    const changed = () => {
      const val = L.type === 'toggle' ? input.checked : input.value;
      if (L.type === 'select' && val === '') return;
      state.dirty[L.key] = val; wrap.classList.add('changed');
      clearTimeout(state.timer); state.timer = setTimeout(redesign, L.type === 'text' ? 700 : 350);
    };
    input.addEventListener(L.type === 'text' ? 'input' : 'change', changed);
    if (L.type === 'range') input.addEventListener('input', changed);
    form.appendChild(wrap);
  });
  $('#changes').textContent = 'No changes yet.';
}
function leverQuery() {
  const p = new URLSearchParams();
  Object.entries(state.dirty).forEach(([k, v]) => { if (v !== '' && v != null) p.set(k, v); });
  return p;
}
async function redesign() {
  const my = ++state.seq;
  const p = leverQuery();
  try {
    let res;
    if (state.mode === 'design') {
      Object.entries(state.design).forEach(([k, v]) => p.set(k, v));
      res = await api(`/api/design?${p}`);
    } else {
      p.set('nct_id', state.nct);
      res = await api(`/api/whatif?${p}`);
    }
    if (my !== state.seq) return;
    const after = { ...res.after, similar: res.after.similar || state.base.similar, similar_checked: res.after.similar_checked || state.base.similar_checked,
      similar_stopped: res.after.similar_stopped ?? state.base.similar_stopped, similar_reasons: res.after.similar_reasons || state.base.similar_reasons };
    const kind = state.mode === 'design' ? 'Your design. Not a real trial.' : `Redesigned version of ${state.nct}. The real trial is unchanged.`;
    renderSheet(after, { kind, was: res.before.risk });
    renderPrecedents(after);
    $('#changes').textContent = res.changes.length
      ? `${res.changes.length} change${res.changes.length > 1 ? 's' : ''}: ` + res.changes.map(fmtChange).join('; ') + '.'
      : 'No changes yet.';
    say('');
  } catch (e) { say(e.message, true); }
}
function fmtChange(c) {
  const L = LEVERS.find(l => l.key === c.lever) || { label: c.lever };
  const show = v => (v === true ? 'yes' : v === false ? 'no' : v == null || v === '' ? 'not given' : L.name ? (L.name(v) || v) : String(v));
  return `${L.label.toLowerCase()} ${show(c.from)} to ${show(c.to)}`;
}

/* ---------- loading a trial ---------- */
function working(on) {
  const w = $('#working'); w.hidden = !on; $('#sheet').setAttribute('aria-busy', on);
  clearInterval(state.stepTimer); clearTimeout(state.slowTimer);
  if (!on) return;
  const n = state.summary ? num(state.summary.data.study_group) : '169,743';
  const steps = ['Fetching the record from ClinicalTrials.gov', 'Reading the protocol with the AI model', "Checking the sponsor's past trials",
    `Comparing it with ${n} past trials`, 'Stamping the risk'];
  $('#steps').innerHTML = steps.map(s => `<li>${s}</li>`).join('');
  let i = 0;
  const tick = () => {
    [...$('#steps').children].forEach((li, j) => { li.className = j < i ? 'done' : j === i ? 'now' : ''; });
    $('.pencil-line').style.setProperty('--p', `${Math.min(95, (i + 1) * 19)}%`);
    if (i < steps.length - 1) i++;
  };
  tick(); state.stepTimer = setInterval(tick, 1400);
  $('#slow').hidden = true; state.slowTimer = setTimeout(() => { $('#slow').hidden = false; }, 8000);
}
function showResult(d, opts) {
  state.current = d; state.base = d; state.baseOpts = opts;
  renderSheet(d, opts); renderPrecedents(d); renderLevers(d.levers);
  document.querySelector('.redesign').hidden = !d.levers;
  $('#reset').textContent = state.mode === 'design' ? 'Start my design over' : 'Back to the real design';
  $('#re-title').textContent = state.mode === 'design' ? 'Shape your trial' : 'Redesign this trial';
}
async function loadTrial(id) {
  const nct = id.trim().toUpperCase();
  say(''); $('#found').hidden = true; markChip(null);
  document.getElementById('desk').scrollIntoView({ behavior: 'smooth', block: 'start' });
  working(true);
  try {
    const d = await api(`/api/trial/${encodeURIComponent(nct)}`);
    state.mode = 'trial'; state.nct = d.nct_id;
    let actual = null, right = false;
    if (d.finished) {
      const stopped = d.status !== 'COMPLETED';
      actual = stopped ? `${statusName(d.status)}${d.why_stopped ? `: "${d.why_stopped}"` : ''}` : 'It finished as planned.';
      right = stopped ? d.band !== 'Low' : d.band !== 'High';
    }
    showResult(d, { kind: `Live from ClinicalTrials.gov, reviewed in ${d.seconds.toFixed(1)} seconds.${d.finished ? ' This trial has already ended.' : ''}`, actual, right });
    history.replaceState(null, '', `?trial=${d.nct_id}#desk`);
  } catch (e) { say(e.message, true); }
  finally { working(false); }
}
async function searchTrials(q) {
  say('Searching ClinicalTrials.gov for trials recruiting now…');
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(q)}&limit=10`);
    say('');
    if (!r.count) { say(`No interventional trials are recruiting for "${q}" right now. Try another word or a trial number.`, true); return; }
    $('#found-title').textContent = `Recruiting now for "${q}": pick one to review`;
    $('#found-list').innerHTML = r.trials.map(t => `<li><button type="button" data-id="${esc(t.nct_id)}"><span class="fid">${esc(t.nct_id)}</span>
      <span>${esc(t.title)}<span class="fsp">${esc(t.sponsor || '')}</span></span><span class="fphase">${esc(t.phase)}</span></button></li>`).join('');
    $('#found').hidden = false;
    $('#found').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) { say(e.message, true); }
}
function openShowcase(i) {
  const d = state.showcase[i]; if (!d) return;
  state.mode = 'showcase'; state.nct = d.nct_id;
  $('#found').hidden = true; say('');
  const right = /^(Caught|Correctly)/.test(d.story);
  showResult(d, { kind: `Real case file from ${d.start?.slice(0, 4) || 'the test years'}. The model scored it without knowing how it ended.`, actual: d.actual, right });
  markChip(i);
}
function markChip(i) { document.querySelectorAll('.chip').forEach((c, j) => c.setAttribute('aria-pressed', String(j === i))); }

/* ---------- charts (plain SVG) ---------- */
function svg(w, h) { const s = document.createElementNS(NS, 'svg'); s.setAttribute('viewBox', `0 0 ${w} ${h}`); s.setAttribute('role', 'img'); return s; }
function el(tag, attrs, parent, text) {
  const e = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
  if (text != null) e.textContent = text;
  parent.appendChild(e); return e;
}
function tipOn(node, text) {
  node.setAttribute('tabindex', '0'); node.setAttribute('aria-label', text);
  const tip = $('#tip');
  const show = ev => { tip.textContent = text; tip.hidden = false; const r = node.getBoundingClientRect();
    const x = ev?.clientX ?? r.left + r.width / 2, y = ev?.clientY ?? r.top;
    tip.style.left = `${Math.min(x + 12, innerWidth - 270)}px`; tip.style.top = `${y - 40}px`; };
  node.addEventListener('mousemove', show); node.addEventListener('focus', () => show());
  node.addEventListener('mouseleave', () => { tip.hidden = true; }); node.addEventListener('blur', () => { tip.hidden = true; });
}
const BAND_COLOR = { Low: 'var(--blue)', Medium: 'var(--amber)', High: 'var(--red)' };
function chartBands(rows, avg) {
  const W = 520, H = 230, L = 150, R = 150, max = Math.max(...rows.map(r => r.stopped_pct)) * 1.1;
  const s = svg(W, H); s.setAttribute('aria-label', 'Share of trials that stopped early in each risk band');
  const x = v => L + (W - L - R) * v / max, bh = 38, gap = 26;
  rows.forEach((r, i) => {
    const band = r.band.split(' ')[0], y = 18 + i * (bh + gap);
    el('text', { x: L - 12, y: y + bh / 2 + 4, 'text-anchor': 'end', class: 'lbl' }, s, r.band);
    const bar = el('rect', { x: L, y, width: Math.max(2, x(r.stopped_pct) - L), height: bh, rx: 3, fill: BAND_COLOR[band] }, s);
    el('text', { x: x(r.stopped_pct) + 8, y: y + bh / 2 + 4, class: 'lbl' }, s, `${pct1(r.stopped_pct)} stopped`);
    el('text', { x: x(r.stopped_pct) + 8, y: y + bh / 2 + 19 }, s, `${Math.round(r['share_of_trials_%'])}% of trials`);
    tipOn(bar, `${r.band}: ${num(r.trials)} trials, ${pct1(r.stopped_pct)} really stopped early`);
  });
  if (avg) { const ax = x(avg); el('line', { x1: ax, x2: ax, y1: 8, y2: H - 22, class: 'axis', 'stroke-dasharray': '4 4' }, s);
    el('text', { x: ax, y: H - 6, 'text-anchor': 'middle' }, s, `average ${pct1(avg)}`); }
  $('#chart-bands').replaceChildren(s);
}
function chartCalibration(pts) {
  const W = 520, H = 300, L = 52, B = 40, T = 12, Rm = 16, max = Math.max(0.05, ...pts.flatMap(p => [p.predicted, p.actual])) * 1.12;
  const s = svg(W, H); s.setAttribute('aria-label', 'Predicted risk against the share of trials that really stopped');
  const x = v => L + (W - L - Rm) * v / max, y = v => H - B - (H - B - T) * v / max;
  const step = max > 0.5 ? 0.2 : max > 0.25 ? 0.1 : 0.05;
  for (let v = 0; v <= max + 1e-9; v += step) {
    el('line', { x1: L, x2: W - Rm, y1: y(v), y2: y(v), class: 'grid' }, s);
    el('text', { x: L - 8, y: y(v) + 4, 'text-anchor': 'end' }, s, `${Math.round(v * 100)}%`);
    el('text', { x: x(v), y: H - B + 18, 'text-anchor': 'middle' }, s, `${Math.round(v * 100)}%`);
  }
  el('line', { x1: x(0), y1: y(0), x2: x(max), y2: y(max), class: 'axis', 'stroke-dasharray': '5 5' }, s);
  el('text', { x: x(max * 0.62), y: y(max * 0.62) - 8, 'text-anchor': 'end' }, s, 'perfect');
  el('polyline', { points: pts.map(p => `${x(p.predicted)},${y(p.actual)}`).join(' '), fill: 'none', stroke: 'var(--blue)', 'stroke-width': 2 }, s);
  pts.forEach((p, i) => { const c = el('circle', { cx: x(p.predicted), cy: y(p.actual), r: 6, fill: 'var(--blue)', stroke: 'var(--paper)', 'stroke-width': 2 }, s);
    tipOn(c, `Group ${i + 1}: predicted ${pct1(p.predicted * 100)}, really stopped ${pct1(p.actual * 100)}`); });
  el('text', { x: (L + W) / 2, y: H - 4, 'text-anchor': 'middle' }, s, 'Predicted risk');
  el('text', { x: 14, y: (H - B) / 2, 'text-anchor': 'middle', transform: `rotate(-90 14 ${(H - B) / 2})` }, s, 'Really stopped');
  $('#chart-cal').replaceChildren(s);
}
function chartLeak(sm) {
  const rows = [];
  if (sm.phase3) rows.push({ name: 'Cheating model', note: 'used after-the-end facts', v: sm.phase3.cheating_auroc, c: 'var(--red)', rejected: true },
    { name: 'First honest model', note: 'design facts only', v: sm.phase3.honest_auroc, c: 'var(--ink-3)' });
  rows.push({ name: 'Final model', note: 'on 2018-2019 trials', v: sm.metrics.test_auroc, c: 'var(--blue)' });
  const W = 520, H = 40 + rows.length * 62, L = 165, R = 60, lo = 0.5, hi = 0.9;
  const s = svg(W, H); s.setAttribute('aria-label', 'AUROC of the cheating model and the honest models');
  const x = v => L + (W - L - R) * (v - lo) / (hi - lo);
  [0.5, 0.6, 0.7, 0.8, 0.9].forEach(v => { el('line', { x1: x(v), x2: x(v), y1: 6, y2: H - 22, class: 'grid' }, s);
    el('text', { x: x(v), y: H - 6, 'text-anchor': 'middle' }, s, v.toFixed(1)); });
  rows.forEach((r, i) => {
    const y = 12 + i * 62;
    el('text', { x: L - 12, y: y + 16, 'text-anchor': 'end', class: 'lbl' }, s, r.name);
    el('text', { x: L - 12, y: y + 32, 'text-anchor': 'end' }, s, r.note);
    const b = el('rect', { x: x(lo), y, width: x(r.v) - x(lo), height: 36, rx: 3, fill: r.c, opacity: r.rejected ? 0.35 : 1 }, s);
    el('text', { x: x(r.v) + 8, y: y + 23, class: 'lbl' }, s, r.v.toFixed(3));
    if (r.rejected) el('text', { x: x(lo) + 10, y: y + 23, class: 'lbl', fill: 'var(--red)', style: 'fill: var(--red); letter-spacing: .12em; font-weight: 800' }, s, 'REJECTED');
    tipOn(b, `${r.name}: AUROC ${r.v.toFixed(3)} (${r.note})`);
  });
  $('#chart-leak').replaceChildren(s);
}

/* ---------- page sections from the summary ---------- */
function renderSummary(sm) {
  const m = sm.metrics;
  $('#one-in').textContent = `1 in ${Math.round(1 / sm.data.stop_rate)}`;
  $('#r-test-n').textContent = num(m.test_trials);
  $('#r-auroc').textContent = m.test_auroc.toFixed(3);
  $('#r-auroc-nc').textContent = m.test_auroc_without_covid.toFixed(3);
  const bands = sm.risk_bands || [];
  const hi = bands.find(b => b.band.startsWith('High')), lo = bands.find(b => b.band.startsWith('Low'));
  if (hi && lo) { $('#r-bands').textContent = `${(hi.stopped_pct / lo.stopped_pct).toFixed(1)}×`; $('#r-high').textContent = pct1(hi.stopped_pct); $('#r-low').textContent = pct1(lo.stopped_pct); }
  $('#r-lift').textContent = `${m.top10_lift.toFixed(1)}×`;
  $('#r-cheat').textContent = sm.phase3 ? `AUROC ${sm.phase3.cheating_auroc.toFixed(3)}` : 'very high';
  const sus = sm.suspicious || {};
  $('#r-sus-w').textContent = signed(sus.suspicious_gain_withdrawn ?? 0);
  $('#r-sus-t').textContent = signed(sus.suspicious_gain_terminated ?? 0);
  if (bands.length) chartBands(bands, m.test_stop_rate * 100);
  if (sm.calibration?.length) chartCalibration(sm.calibration);
  chartLeak(sm);
  const GROUP = { phase: 'By phase', sponsor_class: 'By sponsor type' };
  let last = null;
  $('#fair tbody').innerHTML = (sm.subgroups || []).map(g => {
    const head = g.group !== last ? `<tr class="grp"><th colspan="4">${GROUP[g.group] || words(g.group)}</th></tr>` : '';
    last = g.group;
    const name = g.group === 'phase' ? phaseName(g.value === 'No phase (NA)' ? 'NA' : g.value) : SPONSOR_TYPES[g.value] || words(g.value);
    return `${head}<tr><td>${esc(name)}</td><td class="n">${num(g.trials)}</td><td class="n">${pct1(g['stopped_%'])}</td><td class="n">${(+g.AUROC).toFixed(3)}</td></tr>`;
  }).join('');
  const d = sm.data;
  const steps = [
    [`Downloaded ${num(d.trials_downloaded)} finished trials`, 'From the ClinicalTrials.gov API, page by page, saved as Parquet and queried with DuckDB.'],
    [`Kept ${num(d.study_group)} trials that tested a treatment`, `Interventional trials that started in 2008-2019. ${pct1(d.stop_rate * 100)} of them stopped early.`],
    ['Caught the leaks', 'Every field was tested on its own. Fields that only exist after a trial ends were removed, and suspicious ones were tested with and without.'],
    ['Turned each protocol into numbers', "Design, eligibility rules, oversight, and the sponsor's track record, counting only trials that had ended before (DuckDB ASOF joins)."],
    ['Read every protocol with AI', 'A small text model (BGE-small) turned each protocol into 384 numbers on a free GPU. The website runs the same model.'],
    ['Trained and tested on time', 'LightGBM tuned with Optuna. Trained on 2008-2015, calibrated on 2016-2017, tested once on 2018-2019.'],
    ['Explained every score', `SHAP values write the margin notes. A search index of ${sm.similar_index?.dims || 192} numbers per trial finds similar past trials.`],
    ['Served it live', 'FastAPI fetches any trial from ClinicalTrials.gov, scores it and explains it. Tested with pytest and shipped with Docker.'],
  ];
  $('#pipeline').innerHTML = steps.map(([b, t]) => `<li><b>${esc(b)}</b><span>${esc(t)}</span></li>`).join('');
}
const SHORT = s => (/^Caught/.test(s) ? ['up', 'Flagged high, then stopped'] : /^Correctly/.test(s) ? ['down', 'Rated low, finished']
  : /^Missed/.test(s) ? ['down', 'Missed: rated low, but stopped'] : ['up', 'False alarm: rated high, finished']);
function renderShowcase(list) {
  $('#chips').innerHTML = list.map((d, i) => { const [dir, label] = SHORT(d.story); const cond = (d.conditions || '').split(',')[0].slice(0, 26);
    return `<button type="button" class="chip" data-i="${i}" aria-pressed="false"><i class="${dir}">${dir === 'up' ? '▲' : '▼'}</i>${esc(label)}<br><small>${esc(cond)}</small></button>`; }).join('');
  $('#scorecard tbody').innerHTML = list.map(d => { const ok = /^(Caught|Correctly)/.test(d.story);
    return `<tr><td>${esc(d.nct_id)}<span class="sub">${esc(d.title.slice(0, 80))}</span></td><td><span class="tag-${d.band}">${d.band} ${pct(d.risk)}</span></td>
      <td>${esc(d.actual)}</td><td class="${ok ? 'ok' : 'bad'}">${esc(SHORT(d.story)[1].split(':')[0])}</td></tr>`; }).join('');
}

/* ---------- wiring ---------- */
$('#ask').addEventListener('submit', e => {
  e.preventDefault();
  const q = $('#q').value.trim();
  if (!q) { say('Type a trial number like NCT04368728, or a disease like asthma.', true); return; }
  if (/^nct\d{8}$/i.test(q)) loadTrial(q);
  else if (/^nct/i.test(q)) say('A trial number is NCT followed by 8 digits, for example NCT04368728.', true);
  else if (q.length < 2) say('Type at least two letters.', true);
  else searchTrials(q);
});
$('#found-list').addEventListener('click', e => { const b = e.target.closest('button[data-id]'); if (b) loadTrial(b.dataset.id); });
$('#found-close').addEventListener('click', () => { $('#found').hidden = true; });
$('#chips').addEventListener('click', e => { const b = e.target.closest('.chip'); if (b) { openShowcase(+b.dataset.i); document.getElementById('desk').scrollIntoView({ behavior: 'smooth' }); } });
$('#reset').addEventListener('click', () => {
  clearTimeout(state.timer); state.seq++;
  if (state.mode === 'design') { openDesign(state.design); return; }
  renderSheet(state.base, state.baseOpts); renderPrecedents(state.base); renderLevers(state.base.levers);
});
const dlg = $('#design-dialog');
$('#open-design').addEventListener('click', () => (dlg.showModal ? dlg.showModal() : dlg.setAttribute('open', '')));
dlg.addEventListener('close', () => {
  if (dlg.returnValue !== 'ok') return;
  const f = new FormData($('#design-form'));
  openDesign({ title: f.get('title'), condition: f.get('condition'), treatment: f.get('treatment'), sponsor: f.get('sponsor') || 'My Company' });
});
async function openDesign(design) {
  state.mode = 'design'; state.design = design; markChip(null);
  document.getElementById('desk').scrollIntoView({ behavior: 'smooth' });
  working(true);
  try {
    const p = new URLSearchParams(design);
    const res = await api(`/api/design?${p}`);
    showResult({ ...res.after, levers: res.levers }, { kind: 'Your design. Not a real trial.' });
  } catch (e) { say(e.message, true); }
  finally { working(false); }
}

(async function start() {
  try {
    const [sm, show] = await Promise.all([api('/api/summary'), api('/api/showcase')]);
    state.summary = sm; state.showcase = show;
    renderSummary(sm); renderShowcase(show);
    const wanted = new URLSearchParams(location.search).get('trial');
    if (wanted && /^NCT\d{8}$/i.test(wanted)) loadTrial(wanted); else openShowcase(0);
  } catch (e) { say('The server is waking up. Refresh the page in a minute.', true); }
})();
