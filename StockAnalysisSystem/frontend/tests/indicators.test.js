import test from 'node:test'
import assert from 'node:assert/strict'
import { getBars, getIndicators } from '../src/mock/generator.js'
import { getStatisticalAnalysis } from '../src/mock/analysis.js'
import { sma, ema, computeIndicators, computeStatistics, CORR_FEATURES } from '../src/utils/indicators.js'

/* ── 合成 bar 工具 ─────────────────────────────────────── */

function makeBars(closes, { dropPreClose = false, dropVol = false } = {}) {
  return closes.map((c, i) => {
    const bar = {
      trade_date: `D${String(i).padStart(3, '0')}`,
      open: +(c * 0.995).toFixed(4),
      high: +(c * 1.01).toFixed(4),
      low: +(c * 0.985).toFixed(4),
      close: c,
      vol: 100000 + i * 1000,
    }
    if (!dropPreClose) bar.pre_close = i > 0 ? closes[i - 1] : c
    if (dropVol) delete bar.vol
    return bar
  })
}

const rising = (n, start = 100, step = 0.005) =>
  Array.from({ length: n }, (_, i) => +(start * (1 + step) ** i).toFixed(4))

/* ── 1. mock 壳黄金回归（内核抽取前后输出必须逐位一致） ─── */

const GOLDEN_ROW_120 = {"ts_code":"000001.SZ","trade_date":"2026-03-16","ma5":13.202,"ma10":13.424,"ma20":13.461,"ma60":12.4197,"ema5":13.2659,"ema10":13.3436,"ema20":13.2723,"ema26":13.1696,"ema60":12.5506,"ema120":11.7845,"macd_dif":0.179,"macd_dea":0.2918,"macd_hist":-0.2257,"rsi":51.3954,"bb_upper":14.0679,"bb_middle":13.461,"bb_lower":12.8541,"bb_width":0.0902,"kdj_k":23.3178,"kdj_d":30.527,"kdj_j":8.8994,"vol_ma5":1289226,"vol_ma10":1110386,"volume_ratio":0.889,"mfi14":35.9438,"return_1d":0.006849,"return_5d":-0.037118,"return_10d":-0.041999,"log_return":0.006826,"future_return_1d":-0.018141,"future_return_5d":-0.067271,"future_direction_1d":0,"future_direction_5d":0,"turnover_rate_5":13.8214,"turnover_rate_60":134.9259,"turnover_rate_120":257.5347,"br":118.9531,"ar":129.5499,"volatility_20d":0.307609,"volatility_60d":0.32241,"volatility_120d":0.336025,"skewness_20d":-0.210264,"skewness_60d":-0.26302,"skewness_120d":-0.100544,"kurtosis_20d":-0.313582,"kurtosis_60d":0.182115,"kurtosis_120d":0.109389,"arron_up_25":68,"arron_down_25":8,"bear_power":-0.3362,"bull_power":-0.0562,"bias5":0.2121,"bias10":-1.4452,"bias20":-1.7161,"bias60":6.5246,"cci10":-84.2502,"cci15":-108.5207,"cci20":-81.3497,"cci88":95.1419,"cr20":109.4369,"mass":26.3328,"golden_cross":0,"death_cross":0,"macd_golden_cross":0,"rsi_oversold":0,"rsi_overbought":0}
const GOLDEN_ROW_259 = {"ts_code":"000001.SZ","trade_date":"2026-09-25","ma5":10.876,"ma10":10.749,"ma20":10.3785,"ma60":11.1375,"ema5":10.8844,"ema10":10.7397,"ema20":10.6386,"ema26":10.6615,"ema60":10.97,"ema120":11.2765,"macd_dif":0.0375,"macd_dea":-0.0882,"macd_hist":0.2514,"rsi":61.4937,"bb_upper":11.211,"bb_middle":10.3785,"bb_lower":9.546,"bb_width":0.1604,"kdj_k":76.952,"kdj_d":76.6162,"kdj_j":77.6237,"vol_ma5":1186971,"vol_ma10":1108335,"volume_ratio":1.8923,"mfi14":65.2655,"return_1d":0.036279,"return_5d":0.037244,"return_10d":0.054924,"log_return":0.035636,"future_return_1d":null,"future_return_5d":null,"future_direction_1d":null,"future_direction_5d":null,"turnover_rate_5":10.2492,"turnover_rate_60":117.5733,"turnover_rate_120":252.0234,"br":111.2266,"ar":122.9581,"volatility_20d":0.279025,"volatility_60d":0.339127,"volatility_120d":0.326972,"skewness_20d":1.437181,"skewness_60d":0.147013,"skewness_120d":0.363621,"kurtosis_20d":2.336788,"kurtosis_60d":0.790101,"kurtosis_120d":0.970321,"arron_up_25":8,"arron_down_25":56,"bear_power":-0.003,"bull_power":0.587,"bias5":2.4274,"bias10":3.6375,"bias20":7.3373,"bias60":0.0224,"cci10":163.667,"cci15":114.8588,"cci20":116.8505,"cci88":-35.5891,"cr20":102.3285,"mass":24.8767,"golden_cross":0,"death_cross":0,"macd_golden_cross":0,"rsi_oversold":0,"rsi_overbought":0}

test('mock getIndicators matches golden rows after kernel extraction', () => {
  const feats = getIndicators('000001.SZ')
  assert.equal(feats.length, 260)
  assert.deepStrictEqual(feats[120], GOLDEN_ROW_120)
  assert.deepStrictEqual(feats[259], GOLDEN_ROW_259)
})

test('mock getStatisticalAnalysis matches golden values after kernel extraction', () => {
  const st = getStatisticalAnalysis('000001.SZ')
  assert.deepStrictEqual(st.descriptive, { mean: 0.0744, std: 2.0871, min: -5.551, q25: -1.2661, q50: 0.1685, q75: 1.1824, max: 6.3441, skewness: 0.1236, kurtosis: 0.4135 })
  assert.deepStrictEqual(st.trend, { slope: -0.3299, r_squared: 0.6679, direction: 'down', window: 60 })
  assert.equal(st.drawdown.max_drawdown, -29.73)
  assert.equal(st.drawdown.max_drawdown_date, '2026-08-31')
  assert.deepStrictEqual(st.drawdown.series[0], { date: '2025-09-29', value: 0 })
  assert.deepStrictEqual(st.drawdown.series[259], { date: '2026-09-25', value: -20.2 })
  assert.deepStrictEqual(st.histogram.slice(0, 3), [
    { range: -5.55, count: 1 }, { range: -5.06, count: 4 }, { range: -4.56, count: 4 },
  ])
  assert.equal(st.histogram.reduce((a, b) => a + b.count, 0), 259)
  const last = st.volatility.vol20.length - 1
  assert.equal(st.volatility.vol20[last], 27.9)
  assert.equal(st.volatility.vol60[last], 33.91)
  assert.equal(st.volatility.vol120[last], 32.7)
  assert.equal(st.volatility.dates[0], '2025-09-29')
  assert.equal(st.correlation.matrix[0][1], 0.37)
  assert.deepEqual(st.correlation.matrix.map((r, i) => r[i]), [1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
})

/* ── 2. sma / ema 基础 ─────────────────────────────────── */

test('sma computes trailing means with null warmup', () => {
  const out = sma([1, 2, 3, 4, 5], 5)
  assert.deepEqual(out.slice(0, 4), [null, null, null, null])
  assert.equal(out[4], 3)
  assert.equal(sma([2, 4, 6], 2)[2], 5)
})

test('ema seeds with first value and recurses with k=2/(n+1)', () => {
  const out = ema([1, 2, 3], 2)
  assert.equal(out[0], 1)
  assert.ok(Math.abs(out[1] - 5 / 3) < 1e-12)
  assert.ok(Math.abs(out[2] - (3 * 2 / 3 + (5 / 3) * 1 / 3)) < 1e-12)
})

/* ── 3. computeIndicators：真实数据侧数学与健壮性 ───────── */

test('computeIndicators derives indicators from raw bars', () => {
  const closes = rising(100)
  const bars = makeBars(closes)
  const feats = computeIndicators(bars, { tsCode: '600519.SH' })
  assert.equal(feats.length, 100)
  assert.equal(feats[0].ts_code, '600519.SH')
  assert.equal(feats[0].trade_date, 'D000')

  const last = feats[99]
  // ma5 = 最近 5 收盘均值
  const expectMa5 = closes.slice(95).reduce((a, b) => a + b, 0) / 5
  assert.ok(Math.abs(last.ma5 - expectMa5) < 1e-3)
  // 单边上涨：RSI(14) = 100（avgLoss=0 口径），且无超卖
  assert.equal(last.rsi, 100)
  assert.equal(last.rsi_overbought, 1)
  assert.equal(last.rsi_oversold, 0)
  // MACD 柱 = 2*(DIF-DEA)
  for (const f of feats.slice(30)) {
    assert.ok(Math.abs(f.macd_hist - 2 * (f.macd_dif - f.macd_dea)) < 2e-3)
  }
  // BOLL 对称性与带宽
  assert.ok(Math.abs(last.bb_upper + last.bb_lower - 2 * last.bb_middle) < 1e-2)
  assert.ok(Math.abs(last.bb_width - (last.bb_upper - last.bb_lower) / last.bb_middle) < 1e-3)
  // KDJ: j = 3k - 2d
  assert.ok(Math.abs(last.kdj_j - (3 * last.kdj_k - 2 * last.kdj_d)) < 1e-2)
  // 收益率
  assert.ok(Math.abs(feats[1].return_1d - (closes[1] / closes[0] - 1)) < 1e-6)
  assert.equal(feats[0].return_1d, null)
  // future_* 标签：末行为 null，前视值正确
  assert.equal(last.future_return_5d, null)
  assert.ok(Math.abs(feats[94].future_return_5d - (closes[99] / closes[94] - 1)) < 1e-6)
  // 未注入换手率 → turnover_rate_* 全 null（真实数据不可由 OHLCV 推导）
  assert.ok(feats.every((f) => f.turnover_rate_5 == null && f.turnover_rate_60 == null && f.turnover_rate_120 == null))
})

test('computeIndicators accepts injected turnover series', () => {
  const bars = makeBars(rising(30))
  const feats = computeIndicators(bars, { turnover: bars.map(() => 1) })
  assert.equal(feats[3].turnover_rate_5, null)
  assert.equal(feats[4].turnover_rate_5, 5)
  assert.equal(feats[29].turnover_rate_5, 5)
})

test('computeIndicators tolerates missing pre_close / vol and empty input', () => {
  const closes = rising(40)
  const noPre = computeIndicators(makeBars(closes, { dropPreClose: true }))
  assert.equal(noPre.length, 40)
  assert.equal(typeof noPre[39].ar, 'number')
  const noVol = computeIndicators(makeBars(closes, { dropVol: true }))
  assert.equal(noVol[39].vol_ma5, 0)
  assert.equal(noVol[39].volume_ratio, null)
  assert.deepEqual(computeIndicators([]), [])
  assert.throws(() => computeIndicators(null), /K 线输入无效/)
})

/* ── 4. computeStatistics：结构、语义与边界 ─────────────── */

test('computeStatistics returns the AnalysisStats contract', () => {
  const closes = rising(200)
  const bars = makeBars(closes)
  const feats = computeIndicators(bars)
  const st = computeStatistics(bars, feats)

  for (const key of ['descriptive', 'histogram', 'drawdown', 'volatility', 'correlation', 'trend']) {
    assert.ok(key in st, `缺少 ${key}`)
  }
  // 单边上涨：均值>0、无回撤、趋势向上、r² 接近 1（指数增长的线性拟合）
  assert.ok(st.descriptive.mean > 0)
  assert.equal(st.drawdown.max_drawdown, 0)
  assert.equal(st.drawdown.max_drawdown_date, null)
  assert.equal(st.trend.direction, 'up')
  assert.equal(st.trend.window, 60)
  assert.ok(st.trend.r_squared > 0.9)
  // 直方图 24 桶，总计数 = 非 null 收益数（首行 null 被剔除）
  assert.equal(st.histogram.length, 24)
  assert.equal(st.histogram.reduce((a, b) => a + b.count, 0), 199)
  // 波动率序列与 bars 对齐
  assert.equal(st.volatility.dates.length, 200)
  assert.equal(st.volatility.vol20.length, 200)
  assert.equal(st.volatility.vol20[19], null)
  assert.equal(typeof st.volatility.vol20[20], 'number')
  // 相关性：矩阵对称、值域 [-1,1]；turnover 列全 null → 0
  // （对角线=1 的断言放在下面的非退化数据用例：恒定指数上涨下 rsi/mfi 等为常数序列，pearson 常数列口径为 0）
  const m = st.correlation.matrix
  assert.deepEqual(st.correlation.features, CORR_FEATURES)
  assert.equal(m[9][9], 0)
  for (let i = 0; i < 10; i++) {
    for (let j = 0; j < 10; j++) {
      assert.equal(m[i][j], m[j][i])
      assert.ok(m[i][j] >= -1 && m[i][j] <= 1)
    }
  }
})

test('correlation diagonal is 1 for non-degenerate features', () => {
  // 线性上涨 + 双重正弦扰动：所有 CORR_FEATURES 均为非常数序列
  const closes = Array.from({ length: 200 }, (_, i) =>
    +(100 + i * 0.4 + 3 * Math.sin(i * 0.7) + 1.5 * Math.sin(i * 2.3)).toFixed(4))
  const bars = makeBars(closes)
  const st = computeStatistics(bars, computeIndicators(bars))
  const m = st.correlation.matrix
  for (let i = 0; i < 9; i++) {
    assert.equal(m[i][i], 1, `${CORR_FEATURES[i]} 自相关应为 1`)
  }
  assert.equal(m[9][9], 0) // turnover_rate_5 未注入 → 仍为 0
})

test('computeStatistics rejects invalid input', () => {
  const bars = makeBars(rising(10))
  const feats = computeIndicators(bars)
  assert.throws(() => computeStatistics([], feats), /统计分析输入无效/)
  assert.throws(() => computeStatistics(bars, []), /统计分析输入无效/)
  assert.throws(() => computeStatistics(null, feats), /统计分析输入无效/)
})

/* ── 5. 真实链路：normalizeKline 输出可直接喂内核 ───────── */

test('computeIndicators consumes normalized live kline shape', async () => {
  const { normalizeKline } = await import('../src/utils/market.js')
  // 8083 live 返回：trade_date 为 YYYYMMDD，字段可能带字符串数值
  const rows = rising(80).map((c, i) => ({
    trade_date: `2026${String(Math.floor(i / 28) + 1).padStart(2, '0')}${String((i % 28) + 1).padStart(2, '0')}`,
    open: String(c * 0.995), high: String(c * 1.01), low: String(c * 0.985), close: String(c),
    pre_close: null, change: null, pct_chg: null, vol: String(500000 + i), amount: null,
  }))
  const bars = normalizeKline({ ts_code: '600519.SH', bars: rows }, '600519.SH')
  const feats = computeIndicators(bars, { tsCode: '600519.SH' })
  assert.equal(feats.length, 80)
  assert.equal(feats[79].trade_date, bars[79].trade_date)
  assert.match(feats[79].trade_date, /^\d{8}$/)
  assert.equal(typeof feats[79].rsi, 'number')
  assert.equal(feats[79].turnover_rate_5, null)
})
