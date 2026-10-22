import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequestClient } from '../src/api/client.js'
import { getMarketOverview } from '../src/mock/analysis.js'
import { STOCK_POOL, FIXED_TODAY, getBars } from '../src/mock/generator.js'

const read = (p) => readFile(new URL(p, import.meta.url), 'utf8')

/* ── 1. client 白名单：新端点在 hybrid 下走真实后端 ─────── */
test('daily-basic and market overview join the live allowlist', async () => {
  const paths = []
  const client = createRequestClient({
    mode: 'hybrid', mockDelayMs: 0,
    fetchImpl: async (path) => { paths.push(path); return { ok: true, json: async () => 'live' } },
  })
  for (const p of [
    '/api/analysis/daily-basic/000001.SZ',
    '/api/analysis/market/overview',
  ]) assert.equal(await client(() => 'mock', p), 'live')
  // 非法变体仍落演示数据（路径注入 / 带日期的旧格式不再放行）
  for (const p of [
    '/api/analysis/daily-basic/INVALID',
    '/api/analysis/daily-basic/000001.SZ/2026-09-25',
    '/api/analysis/market/overview/../stocks',
  ]) assert.equal(await client(() => 'mock', p), 'mock')
  assert.deepEqual(paths, ['/api/analysis/daily-basic/000001.SZ', '/api/analysis/market/overview'])
})

/* ── 2. mock 聚合与后端契约同构且数值自洽 ───────────────── */
test('mock market overview aggregates consistently with bar data', () => {
  const ov = getMarketOverview()
  assert.equal(ov.trade_date, FIXED_TODAY)
  assert.equal(ov.total, STOCK_POOL.length)
  assert.equal(ov.up_count + ov.down_count + ov.flat_count, ov.total)

  // 与逐股 bar 手工聚合对照
  const pcts = STOCK_POOL.map((s) => {
    const bars = getBars(s.ts_code)
    return bars[bars.length - 1].pct_chg ?? 0
  })
  assert.equal(ov.up_count, pcts.filter((p) => p > 0).length)
  assert.equal(ov.down_count, pcts.filter((p) => p < 0).length)
  const mean = pcts.reduce((a, b) => a + b, 0) / pcts.length
  assert.ok(Math.abs(ov.avg_pct_chg - mean) < 1e-3)
  assert.equal(ov.total_amount, +pcts.reduce((a, _, i) => {
    const bars = getBars(STOCK_POOL[i].ts_code)
    return a + (bars[bars.length - 1].amount ?? 0)
  }, 0).toFixed(2))

  // industries 全量降序；行业聚合与池内行业一致
  for (let i = 1; i < ov.industries.length; i++) {
    assert.ok(ov.industries[i - 1].avg_pct_chg >= ov.industries[i].avg_pct_chg)
  }
  const industryNames = new Set(STOCK_POOL.map((s) => s.industry).filter(Boolean))
  assert.equal(ov.industries.length, industryNames.size)

  // 排行榜：涨幅榜降序、跌幅榜最深在前；字段与后端 top_stocks/bottom_stocks 对齐
  assert.ok(ov.top_stocks.length <= 10 && ov.top_stocks.length > 0)
  for (let i = 1; i < ov.top_stocks.length; i++) {
    assert.ok(ov.top_stocks[i - 1].pct_chg >= ov.top_stocks[i].pct_chg)
  }
  for (let i = 1; i < ov.bottom_stocks.length; i++) {
    assert.ok(ov.bottom_stocks[i - 1].pct_chg <= ov.bottom_stocks[i].pct_chg)
  }
  assert.ok(ov.bottom_stocks[0].pct_chg <= ov.top_stocks.at(-1).pct_chg)
  for (const r of [...ov.top_stocks, ...ov.bottom_stocks]) {
    for (const k of ['ts_code', 'name', 'close', 'pct_chg', 'amount']) assert.ok(k in r, `排行行缺字段 ${k}`)
  }
})

/* ── 3. API 层与组件接线（源码级断言） ─────────────────── */
test('api layer exposes latest-row daily-basic and market overview', async () => {
  const api = await read('../src/api/analysis.js')
  assert.match(api, /\/api\/analysis\/daily-basic\/\$\{tsCode\}`/) // live 最新行语义，路径不带日期
  assert.doesNotMatch(api, /daily-basic\/\$\{tsCode\}\/\$\{tradeDate\}/)
  assert.match(api, /export function getMarketOverview/)
  assert.match(api, /\/api\/analysis\/market\/overview/)

  const proxy = await read('../vite.config.js')
  assert.match(proxy, /daily-basic.*marketTarget/s)
  assert.match(proxy, /market\/overview.*marketTarget/s)
})

test('user market and valuation panels wire into the user app', async () => {
  const view = await read('../src/views/UserAppView.vue')
  assert.match(view, /import UserMarket from '\.\/user\/UserMarket\.vue'/)
  assert.match(view, /import UserValuation from '\.\/user\/UserValuation\.vue'/)
  assert.match(view, /label: '市场概览'/)
  assert.match(view, /<UserValuation :ts-code="tsCode" \/>/)
  assert.match(view, /@pick="onPick"/)
  assert.doesNotMatch(view, /setDataSource/) // 依旧不暴露数据模式切换

  const market = await read('../src/views/user/UserMarket.vue')
  assert.match(market, /getMarketOverview/)
  assert.match(market, /createGenerationGuard/)
  assert.match(market, /settleDetailRequest/)
  assert.match(market, /emit\('pick', row\.ts_code\)/) // 排行榜点股切换主选股
  assert.match(market, /pickable/) // 池外股票（如当日新股）禁用点选
  assert.match(market, /getStockPoolCached/)
  assert.match(market, /adv-bar/) // 涨跌占比条
  assert.match(market, /不构成投资建议/)

  const valuation = await read('../src/views/user/UserValuation.vue')
  assert.match(valuation, /getDailyBasicRow/)
  assert.match(valuation, /createGenerationGuard/)
  assert.match(valuation, /fmtWanToYi/) // 总市值 万元 → 亿
  assert.match(valuation, /pe_ttm/)

  // 估值空数据与接口故障分别处理，不再依赖吞异常的旧写法。
  const basic = await read('../src/views/analysis/AnalysisBasic.vue')
  assert.match(basic, /valuationErrorMessage/)
  assert.match(basic, /basicEmpty/)
  assert.doesNotMatch(basic, /\.catch\(\(\) => \(\{\}\)\)/)
})

/* ── 4. settleDetailRequest 传参契约（防 run.current 运行时 TypeError 回归） ── */
test('user panels pass the whole guard run object to settleDetailRequest', async () => {
  for (const f of ['UserTechnical', 'UserStats', 'UserValuation', 'UserMarket']) {
    const src = await read(`../src/views/user/${f}.vue`)
    // 第二参数必须是 guard.next() 返回的 run 对象（settleDetailRequest 内部调 run.current()）
    assert.match(src, /const run = guard\.next\(\)/, `${f} 应持有整个 run 对象`)
    assert.match(src, /settleDetailRequest\([\s\S]+?, run, \{/, `${f} 应把 run 传给 settleDetailRequest`)
    assert.doesNotMatch(src, /\{ signal, current \} = guard\.next/, `${f} 不得解构 current 传给 settleDetailRequest`)
  }
})
