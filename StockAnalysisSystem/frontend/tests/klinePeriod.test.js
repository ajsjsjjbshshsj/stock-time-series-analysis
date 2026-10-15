import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequestClient } from '../src/api/client.js'
import { KLINE_PERIODS, aggregateBars, periodKeyOf } from '../src/utils/kline.js'
import { sma } from '../src/utils/indicators.js'
import { getBars } from '../src/mock/generator.js'

const read = (p) => readFile(new URL(p, import.meta.url), 'utf8')

const bar = (trade_date, open, high, low, close, vol = 100, amount = 1000) =>
  ({ trade_date, open, high, low, close, vol, amount })

/* ── 1. 分组键：周一/月/年，兼容两种日期格式 ─────────────── */
test('periodKeyOf groups by Monday / month / year in both date formats', () => {
  // 2026-10-12 为周一（FIXED_TODAY 2026-10-13 为周二）
  assert.equal(periodKeyOf('20261012', 'week'), '2026-10-12')
  assert.equal(periodKeyOf('20261011', 'week'), '2026-10-05') // 周日归上一周
  assert.equal(periodKeyOf('2026-10-13', 'week'), '2026-10-12') // ISO 格式同样支持
  assert.equal(periodKeyOf('20260101', 'week'), '2025-12-29') // 跨年周：元旦归上年周一
  assert.equal(periodKeyOf('20261013', 'month'), '2026-10')
  assert.equal(periodKeyOf('20261013', 'year'), '2026')
  assert.throws(() => periodKeyOf('20261013', 'hour'), /未知 K 线周期/)
  assert.throws(() => periodKeyOf('BAD', 'week'), /K 线日期无效/)
  assert.throws(() => periodKeyOf(20261013, 'month'), /K 线日期无效/)
})

/* ── 2. 周线聚合：OHLCV 守恒 + 周期口径涨跌幅 ────────────── */
test('aggregateBars folds daily bars into weekly bars with OHLCV conservation', () => {
  const bars = [
    bar('20261005', 10, 11, 9.5, 10.5),           // 周一
    bar('20261006', 10.5, 12, 10, 11.8),           // 周二
    bar('20261008', 11.8, 12.5, 11, 11.2, 50, 500),  // 周四
    bar('20261009', 11.2, 11.9, 10.8, 11.6, 60, 600), // 周五
    bar('20261012', 11.6, 13, 11.5, 12.9, 70, 700),   // 下周一
  ]
  const wk = aggregateBars(bars, 'week')
  assert.equal(wk.length, 2)
  assert.equal(wk[0].trade_date, '20261009') // 周期收官日
  assert.equal(wk[0].period_label, '2026-10-05')
  assert.equal(wk[0].open, 10)
  assert.equal(wk[0].close, 11.6)
  assert.equal(wk[0].high, 12.5)
  assert.equal(wk[0].low, 9.5)
  assert.equal(wk[0].vol, 310)      // 100+100+50+60
  assert.equal(wk[0].amount, 3100)  // 1000+1000+500+600
  // 周期涨跌幅以上一周期收盘为基准；首根以自身开盘兜底
  assert.equal(wk[0].pre_close, wk[0].open)
  assert.equal(wk[1].pre_close, 11.6)
  assert.equal(wk[1].pct_chg, +(((12.9 / 11.6) - 1) * 100).toFixed(2))
  // 缺量字段按 0 聚合，不产生 NaN
  const sparse = aggregateBars([{ trade_date: '20261005', open: 1, high: null, low: null, close: 2 }], 'week')
  assert.equal(sparse[0].vol, 0)
  assert.equal(sparse[0].high, null)
})

/* ── 3. 月/年分组、日线恒等与非法输入 ───────────────────── */
test('aggregateBars handles month/year grouping, day identity and invalid input', () => {
  const bars = [
    bar('20251230', 1, 1, 1, 1), bar('20251231', 1, 1, 1, 1),
    bar('20260102', 1, 1, 1, 1), bar('20261013', 1, 1, 1, 1),
  ]
  assert.equal(aggregateBars(bars, 'day'), bars) // 日线原样返回（同一引用，不复制）
  assert.deepEqual(aggregateBars(bars, 'month').map((b) => b.period_label), ['2025-12', '2026-01', '2026-10'])
  assert.deepEqual(aggregateBars(bars, 'year').map((b) => b.period_label), ['2025', '2026'])
  assert.throws(() => aggregateBars('nope', 'week'), /K 线输入无效/)
  assert.throws(() => aggregateBars(bars, 'quarter'), /未知 K 线周期/)
  assert.equal(aggregateBars([], 'week').length, 0)
  assert.deepEqual(KLINE_PERIODS.map((p) => p.key), ['day', 'week', 'month', 'year'])
})

/* ── 4. 周期均线：聚合后 sma 与手工均值一致 ─────────────── */
test('moving averages on aggregated bars match hand-computed means', () => {
  const bars = getBars('000001.SZ') // 260 根升序日线（YYYY-MM-DD）
  const wk = aggregateBars(bars, 'week')
  assert.ok(wk.length >= 50 && wk.length <= 53)
  const ma5 = sma(wk.map((b) => b.close), 5)
  assert.equal(ma5.length, wk.length)
  assert.ok(ma5.slice(0, 4).every((v) => v === null)) // 预热期为 null
  const manual = wk.slice(0, 5).reduce((a, b) => a + b.close, 0) / 5
  assert.ok(Math.abs(ma5[4] - manual) < 1e-9)
  // 年 K：260 个交易日跨 2 个自然年，样本极少（前端会隐藏全 null 均线）
  const yr = aggregateBars(bars, 'year')
  assert.ok(yr.length >= 1 && yr.length <= 2)
})

/* ── 5. client 白名单：kline 带 limit 查询仍走真实后端 ──── */
test('kline with limit query stays on the live allowlist', async () => {
  const paths = []
  const client = createRequestClient({
    mode: 'hybrid', mockDelayMs: 0,
    fetchImpl: async (path) => { paths.push(path); return { ok: true, json: async () => 'live' } },
  })
  assert.equal(await client(() => 'mock', '/api/analysis/kline/000001.SZ?limit=2000'), 'live')
  assert.equal(await client(() => 'mock', '/api/analysis/kline/000001.SZ'), 'live')
  assert.equal(await client(() => 'mock', '/api/analysis/kline/INVALID?limit=2000'), 'mock')
  assert.deepEqual(paths, ['/api/analysis/kline/000001.SZ?limit=2000', '/api/analysis/kline/000001.SZ'])
})

/* ── 6. 接线（源码级断言）：周期切换 + 本地均线 ─────────── */
test('AnalysisOverview switches periods and computes MA locally', async () => {
  const view = await read('../src/views/analysis/AnalysisOverview.vue')
  assert.match(view, /KLINE_PERIODS/)
  assert.match(view, /aggregateBars/)
  assert.match(view, /import \{ sma \} from '\.\.\/\.\.\/utils\/indicators\.js'/)
  assert.match(view, /period-btn/) // 周期切换按钮（日K/周K/月K/年K）
  assert.match(view, /LIVE_LIMIT = 2000/) // live 拉长历史，周期线样本充足
  assert.match(view, /candlestick/) // 均线叠加在蜡烛图上
  // 不再依赖 indicators 接口（ClickHouse 未部署时 503 → "MA 不可用"）
  assert.doesNotMatch(view, /getIndicatorSeries/)
  assert.doesNotMatch(view, /alignIndicators/)
  assert.doesNotMatch(view, /MA 不可用/)

  const api = await read('../src/api/analysis.js')
  assert.match(api, /\?limit=\$\{limit\}/) // getKline 透传 limit 到查询串

  const proxy = await read('../vite.config.js')
  assert.match(proxy, /kline.*marketTarget/s) // kline 代理规则仍在（带 query 前缀匹配不受影响）
})
