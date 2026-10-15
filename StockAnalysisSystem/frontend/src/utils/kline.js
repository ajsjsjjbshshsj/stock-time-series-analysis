/**
 * K 线周期工具（对齐同花顺：日 K / 周 K / 月 K / 年 K）。
 * 周期 bar 由日线在浏览器本地聚合；均线用 utils/indicators.js 的 sma() 本地计算，
 * 不再依赖后端 indicators 接口（ClickHouse 未部署时会 503）。
 */

/** 周期选项：key 供 aggregateBars 使用，label 用于切换按钮展示 */
export const KLINE_PERIODS = [
  { key: 'day', label: '日K' },
  { key: 'week', label: '周K' },
  { key: 'month', label: '月K' },
  { key: 'year', label: '年K' },
]

const PERIOD_KEYS = new Set(KLINE_PERIODS.map((p) => p.key))

/** 兼容 YYYYMMDD（normalizeKline 输出）与 YYYY-MM-DD（mock 生成器原始格式） */
function toIso(tradeDate) {
  if (typeof tradeDate !== 'string') throw new Error('K 线日期无效')
  const d = tradeDate.replaceAll('-', '')
  if (!/^\d{8}$/.test(d)) throw new Error('K 线日期无效')
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`
}

/**
 * 分组键：week = 该周周一的日期（周一分组与 ISO 周分组等价，
 * 跨月、跨年周天然正确）；month = YYYY-MM；year = YYYY。
 */
export function periodKeyOf(tradeDate, period) {
  const iso = toIso(tradeDate)
  if (period === 'month') return iso.slice(0, 7)
  if (period === 'year') return iso.slice(0, 4)
  if (period !== 'week') throw new Error('未知 K 线周期')
  const dt = new Date(`${iso}T00:00:00`)
  if (Number.isNaN(dt.getTime())) throw new Error('K 线日期无效')
  dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7)) // 回退到本周一（本地时区，勿用 toISOString 防时区漂移）
  const p = (n) => String(n).padStart(2, '0')
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`
}

/**
 * 日线聚合为周期 bar。入参必须按交易日升序（getKline / mock getBars 均已保证）。
 * day 原样返回；week/month/year：open=组内首日开盘、close=组内末日收盘、
 * high/low=组内极值、vol/amount=组内合计、trade_date=周期收官日。
 * pct_chg 以上一周期收盘为基准重算（首根以自身开盘兜底），与同花顺周期线口径一致。
 */
export function aggregateBars(bars, period = 'day') {
  if (!Array.isArray(bars)) throw new Error('K 线输入无效')
  if (period === 'day') return bars
  if (!PERIOD_KEYS.has(period)) throw new Error('未知 K 线周期')

  const byKey = new Map() // Map 保序：插入顺序 = 分组顺序（输入升序）
  for (const b of bars) {
    const k = periodKeyOf(b.trade_date, period)
    if (!byKey.has(k)) byKey.set(k, [])
    byKey.get(k).push(b)
  }

  const out = []
  for (const [key, list] of byKey) {
    const first = list[0]
    const last = list[list.length - 1]
    const highs = list.map((x) => x.high).filter((v) => v != null)
    const lows = list.map((x) => x.low).filter((v) => v != null)
    out.push({
      trade_date: last.trade_date,
      period_label: key,
      open: first.open,
      close: last.close,
      high: highs.length ? Math.max(...highs) : null,
      low: lows.length ? Math.min(...lows) : null,
      vol: list.reduce((a, x) => a + (x.vol ?? 0), 0),
      amount: list.reduce((a, x) => a + (x.amount ?? 0), 0),
    })
  }
  for (let i = 0; i < out.length; i++) {
    const pre = i ? out[i - 1].close : out[i].open
    out[i].pre_close = pre
    out[i].pct_chg = pre ? +(((out[i].close / pre) - 1) * 100).toFixed(2) : null
  }
  return out
}
