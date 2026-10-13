import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  buildSearchIndex, searchStocks, formatStockLabel,
  scoreField, subsequenceGaps, approxContainsDistance, maxDist,
} from '../src/utils/stockSearch.js'
import { STOCK_POOL } from '../src/mock/generator.js'

const pool = () => buildSearchIndex(STOCK_POOL)
const search = (q, opts) => searchStocks(pool(), q, opts)
const codes = (q, opts) => search(q, opts).map((r) => r.stock.ts_code)

const synth = (rows) => buildSearchIndex(rows.map(([ts_code, name, industry, area], i) => ({
  ts_code, symbol: ts_code.split('.')[0], name, industry, area, list_date: `200${i}-01-01`,
})))

/* ── 1. 精确匹配（含大小写、全角） ─────────────────────── */
test('exact code and name match, case- and width-insensitive', () => {
  for (const q of ['000001.SZ', '000001.sz', '平安银行']) assert.equal(search(q)[0].stock.ts_code, '000001.SZ')
  assert.equal(search('600519.SH')[0].stock.ts_code, '600519.SH')
  assert.equal(search('６００５１９')[0].stock.ts_code, '600519.SH') // 全角 NFKC 归一化
})

/* ── 2. 代码前缀/部分 ─────────────────────────────────── */
test('partial code matches by prefix and substring', () => {
  assert.equal(search('600519')[0].stock.ts_code, '600519.SH')
  assert.equal(search('6005')[0].stock.ts_code, '600519.SH')
  assert.equal(search('519')[0].stock.ts_code, '600519.SH')
})

/* ── 3. 名称子串 + 层级排序 ───────────────────────────── */
test('name substring ranks prefix above contains', () => {
  assert.equal(search('茅台')[0].stock.ts_code, '600519.SH')
  const r = codes('平安')
  assert.ok(r.indexOf('000001.SZ') < r.indexOf('601318.SH'), '平安银行(prefix) 应排在中国平安(contains)之前')
})

/* ── 4. 子序列 ────────────────────────────────────────── */
test('subsequence matching skips characters with gap penalty', () => {
  assert.equal(search('平银')[0].stock.ts_code, '000001.SZ')
  assert.equal(search('贵茅')[0].stock.ts_code, '600519.SH')
  assert.equal(subsequenceGaps('619', '600519'), 3)
  assert.ok(codes('619').includes('600519.SH'), '619 经子序列命中 600519（gaps=3 ≤ cap）')
  assert.equal(subsequenceGaps('619', '601899'), 2) // gap 更小者排更前（601899 先于 600519）
  assert.equal(subsequenceGaps('01', '000001'), 4)
  // gaps 4 > cap(2字 token)=2 → 子序列层拒绝；落到模糊层得 200（若子序列未拒会是 400−160=240）
  assert.equal(scoreField('01', '000021', 1), 200)
  assert.equal(subsequenceGaps('平安', '招商银行'), -1)
})

/* ── 5. 名称错字容忍 ──────────────────────────────────── */
test('typo tolerance on names via bounded edit distance', () => {
  assert.equal(search('矛台')[0].stock.ts_code, '600519.SH')   // d=1
  assert.equal(search('平安很行')[0].stock.ts_code, '000001.SZ') // d=1
  assert.equal(approxContainsDistance('贵州茅台', '矛台'), 1)
  assert.equal(approxContainsDistance('平安银行', '平安很行'), 1)
  assert.equal(maxDist('茅'), 0)      // 单字不容错
  assert.equal(maxDist('平安'), 1)
  assert.equal(maxDist('600528'), 2)  // ≥6 字符允许 d≤2
})

/* ── 6. 代码错字容忍 ──────────────────────────────────── */
test('typo tolerance on codes, 2-char tokens never allow d=2', () => {
  assert.equal(search('600529')[0].stock.ts_code, '600519.SH') // d=1
  assert.equal(search('600528')[0].stock.ts_code, '600519.SH') // d=2, len6→t2
  assert.equal(search('6013l8')[0].stock.ts_code, '601318.SH') // 字母 l 代 1
  // 2 字 token 允许 d=1（'矛台'→茅台 依赖此），但 d=2 不得命中（否则全库命中）
  const two = synth([['900001.SH', '乙丁', '综合', '上海'], ['900002.SH', '戊己', '综合', '北京']])
  assert.deepEqual(searchStocks(two, '甲丙').map((r) => r.stock.ts_code), [])
  assert.equal(scoreField('甲丙', '乙丁', 1), 0)  // d=2 拒绝
  assert.equal(scoreField('甲丙', '甲丁', 1), 200) // d=1 允许（FUZZY1）
})

/* ── 7. industry / area 字段 ──────────────────────────── */
test('industry and area participate in matching', () => {
  assert.deepEqual(codes('白酒').sort(), ['000858.SZ', '600519.SH'])
  const sz = codes('深圳')
  for (const c of ['000001.SZ', '601318.SH', '600036.SH', '002594.SZ']) assert.ok(sz.includes(c), `深圳应含 ${c}`)
  assert.deepEqual(codes('保险'), ['601318.SH'])
})

/* ── 8. 多 token AND ──────────────────────────────────── */
test('multi-token query has AND semantics', () => {
  assert.deepEqual(codes('深圳 银行').sort(), ['000001.SZ', '600036.SH'])
  const r = codes('平安 银行')
  assert.equal(r[0], '000001.SZ')
  assert.ok(!r.includes('601318.SH'), '中国平安(保险)不应命中 平安+银行')
})

/* ── 9. 五层级严格排序 ────────────────────────────────── */
test('layer ordering: exact > prefix > contains > subsequence > fuzzy', () => {
  const idx = synth([
    ['900001.SH', '平安', '综合', '上海'],
    ['900002.SH', '平安证券', '证券', '上海'],
    ['900003.SH', '中国平安', '保险', '深圳'],
    ['900004.SH', '平和安顺', '综合', '贵州'],
    ['900005.SH', '萍安银行', '银行', '江西'],
  ])
  assert.deepEqual(searchStocks(idx, '平安').map((r) => r.stock.ts_code),
    ['900001.SH', '900002.SH', '900003.SH', '900004.SH', '900005.SH'])
})

/* ── 10. 截断与 limit ─────────────────────────────────── */
test('results are limited and scores non-increasing', () => {
  const many = synth(Array.from({ length: 200 }, (_, i) => [
    `${String(800000 + i)}.SZ`, `测试${i}`, '综合', '深圳',
  ]))
  const r10 = searchStocks(many, '测试', { limit: 10 })
  assert.equal(r10.length, 10)
  for (let i = 1; i < r10.length; i++) assert.ok(r10[i - 1].score >= r10[i].score)
  assert.equal(searchStocks(many, '测试').length, 50) // 默认 limit=50
})

/* ── 11. 空查询默认列表 ───────────────────────────────── */
test('empty or blank query returns default browsable list', () => {
  for (const q of ['', '   ', '\t']) {
    const r = search(q)
    assert.equal(r.length, STOCK_POOL.length)
    assert.deepEqual(r.map((x) => x.stock.ts_code), STOCK_POOL.map((s) => s.ts_code)) // 保持原顺序
    assert.ok(r.every((x) => x.score === 0))
  }
})

/* ── 12. 无效输入 ─────────────────────────────────────── */
test('invalid input yields empty results without throwing', () => {
  for (const q of ['zzzzz', '%%%', '999999999', 'x'.repeat(100)]) assert.deepEqual(search(q), [])
  assert.equal(search('  茅台  ')[0].stock.ts_code, '600519.SH') // 首尾空格
})

/* ── 13. 索引健壮性 ───────────────────────────────────── */
test('index build is pure and tolerant of missing fields', () => {
  const rows = [{ ts_code: '000001.SZ', symbol: '000001', name: '平安银行', area: '深圳', industry: '银行', list_date: '1991-04-03' }]
  const snapshot = structuredClone(rows)
  buildSearchIndex(rows)
  assert.deepEqual(rows, snapshot) // 不改动原数组
  const loose = buildSearchIndex([{ ts_code: '000002.SZ', name: 'x', area: null, industry: null, symbol: null }])
  assert.equal(searchStocks(loose, '000002').length, 1) // symbol 缺失回退 ts_code 前缀
  assert.equal(buildSearchIndex(null).entries.length, 0)
  assert.deepEqual(searchStocks(buildSearchIndex([]), '平安'), [])
  assert.deepEqual(searchStocks(null, '平安'), [])
  assert.equal(formatStockLabel(null), '')
  assert.equal(formatStockLabel(rows[0]), '000001.SZ · 平安银行')
})

/* ── 14. 平局确定性 ───────────────────────────────────── */
test('ties break by ts_code ascending', () => {
  const r = search('白酒') // 茅台/五粮液 industry 同为 exact
  assert.equal(r[0].score, r[1].score)
  assert.deepEqual(r.map((x) => x.stock.ts_code), ['000858.SZ', '600519.SH'])
})

/* ── 15. 性能冒烟 ─────────────────────────────────────── */
test('6000-row pool stays fast on worst-case queries', () => {
  const big = buildSearchIndex(Array.from({ length: 6000 }, (_, i) => ({
    ts_code: `${String(i).padStart(6, '0')}.SZ`, symbol: String(i).padStart(6, '0'),
    name: `股票${i}`, industry: '综合', area: '深圳', list_date: '2000-01-01',
  })))
  const t0 = performance.now()
  searchStocks(big, '6005')
  searchStocks(big, '茅')
  searchStocks(big, 'zzzz') // 最坏路径：全字段走编辑距离 DP
  const elapsed = performance.now() - t0
  assert.ok(elapsed < 2000, `三次搜索耗时应 <2000ms，实际 ${elapsed.toFixed(0)}ms`)
})

/* ── 16. 源码级断言（组件层无挂载测试设施，沿用 evaluation.test.js 模式） ── */
test('combobox component wires a11y, IME guards and listener cleanup', async () => {
  const component = await readFile(new URL('../src/components/StockSearch.vue', import.meta.url), 'utf8')
  for (const pattern of [
    /role="combobox"/, /aria-autocomplete="list"/, /aria-activedescendant/, /role="listbox"/,
    /isComposing/, /compositionstart/, /compositionend/,          // IME 三重防护
    /mousedown\.prevent/,                                          // 点选不夺焦点
    /pointerdown/, /onBeforeUnmount/,                              // 点击外部关闭 + 卸载清理
    /buildSearchIndex/, /searchStocks/, /formatStockLabel/,
  ]) assert.match(component, pattern)

  const view = await readFile(new URL('../src/views/AnalysisView.vue', import.meta.url), 'utf8')
  assert.match(view, /<StockSearch/)
  assert.match(view, /v-model="tsCode"/)
  assert.match(view, /:stocks="pool"/)
  assert.match(view, /:disabled="loading \|\| !pool\.length"/)
  assert.match(view, /id="stock-code"/)          // 外部 label for 关联保留
  assert.match(view, /:ts-code="tsCode"/)        // 子组件仍按选中代码渲染
  assert.match(view, /000001\.SZ/)               // 默认选中逻辑保留
  assert.doesNotMatch(view, /<select/)           // 6000 option 下拉已移除

  const icon = await readFile(new URL('../src/components/Icon.vue', import.meta.url), 'utf8')
  assert.match(icon, /name === 'search'/)
})
