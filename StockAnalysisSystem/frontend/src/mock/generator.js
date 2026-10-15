/**
 * Mock 数据引擎：seeded 随机游走 K 线生成。
 *
 * 所有数据由 ts_code 派生的固定种子生成，同一股票每次刷新数据一致，
 * 保证演示时图表、表格、指标之间互相吻合。
 *
 * 指标计算内核已抽至 utils/indicators.js（computeIndicators），
 * 演示端与真实行情共用同一套算法；本文件的 getIndicators 只是
 * “取 mock K 线 + 注入 seeded 随机换手率 + 缓存”的薄壳。
 */
import { computeIndicators } from '../utils/indicators.js'

/* ── 随机数 ─────────────────────────────────────────────── */

export function mulberry32(a) {
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function hashSeed(str) {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function gaussian(rand) {
  let u = 0
  let v = 0
  while (u === 0) u = rand()
  while (v === 0) v = rand()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

/* ── 股票池（mock，代码与名称为公开常识数据） ────────────── */

export const STOCK_POOL = [
  { ts_code: '000001.SZ', symbol: '000001', name: '平安银行', area: '深圳', industry: '银行', list_date: '1991-04-03', basePrice: 11.2, pe: 5.1, pb: 0.55, totalMv: 21800000 },
  { ts_code: '600519.SH', symbol: '600519', name: '贵州茅台', area: '贵州', industry: '白酒', list_date: '2001-08-27', basePrice: 1465, pe: 24.8, pb: 7.9, totalMv: 184000000 },
  { ts_code: '000858.SZ', symbol: '000858', name: '五粮液', area: '四川', industry: '白酒', list_date: '1998-04-27', basePrice: 128.5, pe: 16.2, pb: 3.6, totalMv: 49900000 },
  { ts_code: '601318.SH', symbol: '601318', name: '中国平安', area: '深圳', industry: '保险', list_date: '2007-03-01', basePrice: 48.6, pe: 8.4, pb: 0.98, totalMv: 88700000 },
  { ts_code: '600036.SH', symbol: '600036', name: '招商银行', area: '深圳', industry: '银行', list_date: '2002-04-09', basePrice: 34.8, pe: 6.3, pb: 0.92, totalMv: 87800000 },
  { ts_code: '000333.SZ', symbol: '000333', name: '美的集团', area: '广东', industry: '家电', list_date: '2013-09-18', basePrice: 62.4, pe: 12.1, pb: 3.1, totalMv: 43500000 },
  { ts_code: '002594.SZ', symbol: '002594', name: '比亚迪', area: '深圳', industry: '汽车', list_date: '2011-06-30', basePrice: 241.7, pe: 19.5, pb: 4.4, totalMv: 70400000 },
  { ts_code: '600900.SH', symbol: '600900', name: '长江电力', area: '北京', industry: '电力', list_date: '2003-11-18', basePrice: 28.9, pe: 20.6, pb: 3.3, totalMv: 70600000 },
  { ts_code: '601899.SH', symbol: '601899', name: '紫金矿业', area: '福建', industry: '有色金属', list_date: '2008-04-25', basePrice: 16.8, pe: 14.3, pb: 3.5, totalMv: 44700000 },
  { ts_code: '300750.SZ', symbol: '300750', name: '宁德时代', area: '福建', industry: '电气设备', list_date: '2018-06-11', basePrice: 182.3, pe: 18.7, pb: 4.1, totalMv: 80200000 },
]

export const INDUSTRY_LIST = [...new Set(STOCK_POOL.map((s) => s.industry))]

/* ── 交易日历 ───────────────────────────────────────────── */

const FIXED_TODAY = '2026-09-25' // 演示用固定“最新交易日”，保证数据稳定

function pad(n) {
  return String(n).padStart(2, '0')
}

function fmtDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function tradingDates(count, endDate = FIXED_TODAY) {
  const dates = []
  const d = new Date(endDate + 'T00:00:00')
  while (dates.length < count) {
    const day = d.getDay()
    if (day !== 0 && day !== 6) dates.unshift(fmtDate(d))
    d.setDate(d.getDate() - 1)
  }
  return dates
}

export { FIXED_TODAY }

/* ── K 线生成（随机游走） ───────────────────────────────── */

const barsCache = new Map()

export function getBars(tsCode, days = 260) {
  const key = `${tsCode}:${days}`
  if (barsCache.has(key)) return barsCache.get(key)

  const stock = STOCK_POOL.find((s) => s.ts_code === tsCode) || STOCK_POOL[0]
  const rand = mulberry32(hashSeed(tsCode))
  const dates = tradingDates(days)

  const drift = (rand() - 0.42) * 0.0008 // 轻微趋势差异
  const vola = 0.012 + rand() * 0.016 // 年化波动 ~19%-44%
  let close = stock.basePrice * (0.82 + rand() * 0.18)

  const bars = []
  for (let i = 0; i < dates.length; i++) {
    const preClose = close
    // 带均值回归的随机游走，避免价格漂移过远
    const pull = (stock.basePrice - close) / stock.basePrice * 0.02
    const ret = drift + pull + gaussian(rand) * vola
    close = Math.max(0.5, preClose * (1 + ret))
    const open = preClose * (1 + gaussian(rand) * vola * 0.4)
    const hi = Math.max(open, close) * (1 + Math.abs(gaussian(rand)) * vola * 0.5)
    const lo = Math.min(open, close) * (1 - Math.abs(gaussian(rand)) * vola * 0.5)
    const vol = Math.round((300000 + rand() * 900000) * (1 + Math.abs(ret) * 30)) // 手
    const change = close - preClose
    bars.push({
      trade_date: dates[i],
      open: round2(open),
      high: round2(hi),
      low: round2(lo),
      close: round2(close),
      pre_close: round2(preClose),
      change: round2(change),
      pct_chg: round4((change / preClose) * 100),
      vol,
      amount: round2((vol * close) / 10), // 千元
    })
  }
  barsCache.set(key, bars)
  return bars
}

function round2(v) {
  return Math.round(v * 100) / 100
}
function round4(v) {
  return Math.round(v * 10000) / 10000
}
function round6(v) {
  return Math.round(v * 1e6) / 1e6
}

/* ── 全量特征计算（内核在 utils/indicators.js，此处为 mock 薄壳） ── */

const indicatorCache = new Map()

export function getIndicators(tsCode) {
  if (indicatorCache.has(tsCode)) return indicatorCache.get(tsCode)
  const bars = getBars(tsCode)
  // 换手率（mock 日换手，由内核按 5/60/120 日窗口累计）
  const randT = mulberry32(hashSeed(tsCode + ':turnover'))
  const turnover = bars.map(() => round4(0.4 + randT() * 3.6))
  const features = computeIndicators(bars, { tsCode, turnover })
  indicatorCache.set(tsCode, features)
  return features
}

/* ── stock_daily_basic 行生成 ───────────────────────────── */

export function getDailyBasic(tsCode, tradeDate) {
  const stock = STOCK_POOL.find((s) => s.ts_code === tsCode) || STOCK_POOL[0]
  const rand = mulberry32(hashSeed(tsCode + tradeDate))
  const bars = getBars(tsCode)
  const bar = bars.find((b) => b.trade_date === tradeDate) || bars[bars.length - 1]
  const ratio = bar.close / stock.basePrice
  return {
    ts_code: tsCode,
    trade_date: tradeDate,
    turnover_rate: round4(0.4 + rand() * 3.6),
    pe: round4(stock.pe * ratio * (0.95 + rand() * 0.1)),
    pe_ttm: round4(stock.pe * ratio * (0.96 + rand() * 0.1)),
    pb: round4(stock.pb * ratio * (0.97 + rand() * 0.06)),
    ps: round4(stock.pb * 0.31 * ratio * (0.95 + rand() * 0.1)),
    total_mv: Math.round(stock.totalMv * ratio),
    source: 'TUSHARE',
  }
}

/* ── 横截面特征（对齐 panel_builder.compute_cross_sectional_features） ── */

const crossCache = new Map()

/**
 * 计算某交易日全池横截面特征，返回 { ts_code: {...8 个特征} }。
 * market_* 为全池等权均值；industry_* 为同行业等权均值；zscore 为行业内标准化。
 */
export function getCrossSectional(tradeDate) {
  if (crossCache.has(tradeDate)) return crossCache.get(tradeDate)

  const rows = STOCK_POOL.map((s) => {
    const feats = getIndicators(s.ts_code)
    const row = feats.find((f) => f.trade_date === tradeDate) || feats[feats.length - 1]
    return { ts_code: s.ts_code, industry: s.industry, row }
  })

  const mean = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null)
  const sd = (arr) => {
    const m = mean(arr)
    return arr.length > 1 ? Math.sqrt(arr.reduce((a, b) => a + (b - m) ** 2, 0) / (arr.length - 1)) : 0
  }

  const marketReturn = mean(rows.map((r) => r.row.return_1d).filter((v) => v != null))
  const marketVol = mean(rows.map((r) => r.row.volatility_20d).filter((v) => v != null))

  const out = {}
  for (const r of rows) {
    const peers = rows.filter((x) => x.industry === r.industry)
    const industryReturn = mean(peers.map((x) => x.row.return_1d).filter((v) => v != null))
    const peerRet = peers.map((x) => x.row.return_1d).filter((v) => v != null)
    const peerVol = peers.map((x) => x.row.volatility_20d).filter((v) => v != null)
    const retSd = sd(peerRet)
    const volSd = sd(peerVol)
    out[r.ts_code] = {
      market_return: marketReturn == null ? null : round6(marketReturn),
      market_volatility_20d: marketVol == null ? null : round6(marketVol),
      industry_return: industryReturn == null ? null : round6(industryReturn),
      return_vs_market: round6((r.row.return_1d ?? 0) - (marketReturn ?? 0)),
      return_vs_industry: round6((r.row.return_1d ?? 0) - (industryReturn ?? 0)),
      volatility_vs_market: round6((r.row.volatility_20d ?? 0) - (marketVol ?? 0)),
      return_zscore_industry: retSd === 0 ? 0 : round4(((r.row.return_1d ?? 0) - (industryReturn ?? 0)) / retSd),
      volatility_zscore_industry: volSd === 0 ? 0 : round4(((r.row.volatility_20d ?? 0) - mean(peerVol)) / volSd),
    }
  }
  crossCache.set(tradeDate, out)
  return out
}
