import {element, labels, number, options, pairs, q} from './views.js';

// views.js 的 table 为模块私有，这里按同一约定实现归属表渲染
function table(box, records, columns, empty = '没有符合条件的记录。') {
  if (records.length === 0) {
    box.replaceChildren(element('p', empty, 'empty'));
    return;
  }
  const grid = element('table', null, 'ledger');
  const headers = grid.createTHead().insertRow();
  for (const [name, , css] of columns) {
    const th = element('th', name, css);
    th.scope = 'col';
    headers.append(th);
  }
  const body = grid.createTBody();
  for (const record of records) {
    const row = body.insertRow();
    for (const [, value, css] of columns) {
      const cell = row.insertCell();
      const item = typeof value === 'function' ? value(record) : record[value];
      if (css) cell.className = css;
      if (item instanceof Node) cell.append(item);
      else cell.textContent = item ?? '—';
    }
  }
  box.replaceChildren(grid);
}

const STRATEGY_NAME = {A_FIRST: '先保 A 单', B_FIRST: '先保 B 单'};
const STRATEGY_HINT = {
  A_FIRST: '留一片可返工片返修补 A，B 单晚交一片',
  B_FIRST: '可返工片整批锁给 B，A 单当场缺一片',
};
const DEST_NAME = {A: 'A 单', B: 'B 单', HELD: '留厂返修', NONE: '暂不入单'};
const GRADE_BADGE = {PASS: 'pass', REWORK: 'pending', CONFLICT: 'conflict', PENDING: 'fail'};

function gradeStatus(code) {
  return element('span', labels[code] ?? code, `status ${GRADE_BADGE[code] ?? ''}`);
}

function inventoryView(data) {
  text_id('split-inventory-meta', `${data.grades.length} 片 · A 需 ${data.demand.A} · B 需 ${data.demand.B}`);
  table(q('#split-inventory'), data.grades, [
    ['晶圆', 'wafer', 'mono'],
    ['等级', (row) => gradeStatus(row.grade)],
    ['通过', (row) => number(row.counts.PASS), 'num'],
    ['失败', (row) => number(row.counts.FAIL), 'num'],
    ['待测', (row) => number(row.counts.PENDING), 'num'],
    ['冲突', (row) => number(row.counts.CONFLICT), 'num'],
    ['缺陷簇', 'cluster_count', 'num'],
  ]);
}

function text_id(id, value) {
  q('#' + id).textContent = value ?? '—';
}

function orderBlock(orderId, info) {
  const card = element('div', null, 'order-card');
  const head = element('div', null, 'order-card__head');
  head.append(element('strong', `${orderId} 单 · 需 ${info.demand} 片`),
    element('span', info.short ? `缺 ${info.short} 片` : '齐套', `status ${info.short ? 'fail' : 'pass'}`));
  const wafers = element('div', info.wafers.length ? info.wafers.join(' · ') : '（无）', 'order-wafers mono');
  card.append(head, wafers, element('p', `已配 ${info.assigned} / ${info.demand}`, 'panel__meta'));
  return card;
}

function planCard(plan) {
  const card = element('article', null, 'plan-card');
  const head = element('div', null, 'plan-card__head');
  head.append(element('strong', STRATEGY_NAME[plan.strategy]),
    element('span', plan.feasible ? '两张单齐套' : '存在缺片，不能齐套发车',
      `status ${plan.feasible ? 'pass' : 'fail'}`));
  const grid = element('div', null, 'order-grid');
  grid.append(orderBlock('A', plan.orders.A), orderBlock('B', plan.orders.B));
  const notes = [];
  if (plan.held.length) notes.push(`留厂返修：${plan.held.join('、')}`);
  if (plan.blocked.length) notes.push(`不计入：${plan.blocked.join('、')}（冲突/待测）`);
  card.append(head, element('p', STRATEGY_HINT[plan.strategy], 'plan-hint'), grid,
    element('p', notes.join('　') || '无留厂或冻结片', 'plan-note'));
  return card;
}

let currentData = null;

function linesView(strategy) {
  const plan = currentData.plans[strategy];
  text_id('split-lines-meta', STRATEGY_NAME[strategy]);
  table(q('#split-lines'), plan.lines, [
    ['晶圆', 'wafer', 'mono'],
    ['等级', (row) => gradeStatus(row.grade)],
    ['落点', (row) => element('strong', DEST_NAME[row.destination], `dest dest-${row.destination}`)],
    ['依据', (row) => row.notes.join('；')],
  ]);
  // 互斥自检：同一晶圆不能同时出现在 A、B 两张单
  const overlap = plan.orders.A.wafers.filter((w) => plan.orders.B.wafers.includes(w));
  if (overlap.length) throw new Error(`互斥校验失败：${overlap.join('、')}`);
}

function publishView(api, base, signal, rerender) {
  const box = q('#split-publish');
  box.replaceChildren();
  const form = element('form', null, 'publish-form');
  const select = element('select');
  select.setAttribute('aria-label', '取舍方案');
  options(select, [['A_FIRST', STRATEGY_NAME.A_FIRST], ['B_FIRST', STRATEGY_NAME.B_FIRST]], 'A_FIRST');
  const submit = element('button', '发布归属单', 'btn primary');
  submit.type = 'submit';
  const simulate = element('button', '模拟发布中断', 'btn');
  simulate.type = 'button';
  form.append(select, submit, simulate);
  box.append(form);

  const send = async (simulateFailure) => {
    submit.disabled = simulate.disabled = true;
    try {
      const result = await api(`${base}/splits/publish`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({strategy: select.value, simulate_failure: simulateFailure}),
      });
      box.append(element('p',
        `${result.created ? '已发布新版本' : '同内容重跑，复用既有归属单'} v${result.current.version}`, 'publish-ok'));
      await rerender();
    } catch (error) {
      if (!signal.aborted) box.append(element('p', error.message, 'inline-error'));
      await rerender();
    } finally {
      if (!signal.aborted) submit.disabled = simulate.disabled = false;
    }
  };
  form.addEventListener('submit', (event) => { event.preventDefault(); send(false); }, {signal});
  simulate.addEventListener('click', () => send(true), {signal});
}

async function currentView(api, base) {
  const box = q('#split-current');
  const historyBox = q('#split-history');
  let history;
  try {
    history = await api(`${base}/splits/history`);
  } catch (error) {
    box.replaceChildren(element('p', error.message, 'inline-error'));
    return;
  }
  if (history.current_version == null) {
    box.replaceChildren(element('p', '尚未发布归属单，对比方案后可发布。', 'empty'));
    historyBox.replaceChildren();
    return;
  }
  const current = await api(`${base}/splits/current`);
  const plan = current.plan;
  box.replaceChildren(pairs([
    ['版本', `v${current.version}`],
    ['方案', STRATEGY_NAME[current.strategy]],
    ['发布 UTC', current.published_at],
    ['A 单', `${plan.orders.A.wafers.join('、') || '—'}（缺 ${plan.orders.A.short} 片）`],
    ['B 单', `${plan.orders.B.wafers.join('、') || '—'}（缺 ${plan.orders.B.short} 片）`],
    ['留厂返修', plan.held.join('、') || '—'],
    ['冻结片', plan.blocked.join('、') || '—'],
    ['内容摘要', current.plan_hash.slice(0, 16)],
  ]));
  table(historyBox, history.versions, [
    ['版本', (row) => `v${row.version}${row.version === history.current_version ? '（当前）' : ''}`, 'mono'],
    ['方案', (row) => STRATEGY_NAME[row.strategy]],
    ['摘要', (row) => row.plan_hash.slice(0, 16), 'mono'],
    ['发布 UTC', 'published_at', 'mono'],
  ]);
}

export async function splits(context) {
  const {api, base, signal} = context;
  currentData = await api(`${base}/splits/comparison`);
  inventoryView(currentData);
  const plansBox = q('#split-plans');
  plansBox.replaceChildren(...Object.values(currentData.plans).map(planCard));
  const filter = q('#split-plan-filter');
  options(filter, Object.keys(currentData.plans).map((key) => [key, STRATEGY_NAME[key]]), 'A_FIRST');
  const drawLines = () => linesView(filter.value);
  filter.addEventListener('change', drawLines, {signal});
  drawLines();
  const rerender = async () => {
    await currentView(api, base);
  };
  publishView(api, base, signal, rerender);
  await currentView(api, base);
}
