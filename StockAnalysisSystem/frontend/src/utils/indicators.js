/**
 * 技术指标与统计纯计算内核（无副作用、无数据源依赖）。
 *
 * 输入为升序日线 bar 数组（trade_date/open/high/low/close/vol，pre_close 可选），
 * 输出字段名与 stock_features v2 列名一致，mock 演示与真实行情共用同一套算法：
 * - mock/generator.js 的 getIndicators 与 mock/analysis.js 的 getStatisticalAnalysis
 *   均为本文件的薄壳（壳内只负责取数与缓存）
 * - 真实模式（hybrid）下前端拉取 kline 后本地调用 computeIndicators/computeStatistics，
 *   不依赖 8083 的 indicators 接口（ClickHouse 未部署时该接口 503）
 *
 * 注意：
 * - turnover_rate_* 无法由 OHLCV 推导（需要流通股本），默认输出 null；
 *   调用方可通过 options.turnover 注入日换手率序列（mock 壳用 seeded 随机数注入）
 * - future_* 为训练标签（前视值），仅供离线演示/评估消费，实时展示层不得引用
 */

/* ── 通用滚动计算 ───────────────────────────────────────── */

export function sma(arr, n) {
  const out = new Array(arr.length).fill(null)
  let sum = 0
  for (let i = 0; i < arr.length; i++) {
    sum += arr[i]
    if (i >= n) sum -= arr[i - n]
    if (i >= n - 1) out[i] = sum / n
  }
  return out
}

export function ema(arr, n) {
  const out = new Array(arr.length).fill(null)
  const k = 2 / (n + 1)
  let prev = arr[0]
  for (let i = 0; i < arr.length; i++) {
    prev = i === 0 ? arr[0] : arr[i] * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

export function stdev(arr, i, n) {
  if (i < n - 1) return null
  let sum = 0
  for (let j = i - n + 1; j <= i; j++) sum += arr[j]
  const mean = sum / n
  let sq = 0
  for (let j = i - n + 1; j <= i; j++) sq += (arr[j] - mean) ** 2
  return Math.sqrt(sq / n)
}

export function rollingMoment(rets, i, n, moment) {
  if (i < n - 1) return null
  const win = rets.slice(i - n + 1, i + 1).filter((v) => v != null)
  if (win.length < n) return null
  const mean = win.reduce((a, b) => a + b, 0) / n
  const sd = Math.sqrt(win.reduce((a, b) => a + (b - mean) ** 2, 0) / n)
  if (sd === 0) return null
  // volatility_* 为年化标准差（v2 语义）
  if (moment === 2) return round6(sd * Math.sqrt(252))
  if (moment === 3) return round6(win.reduce((a, b) => a + ((b - mean) / sd) ** 3, 0) / n)
  return round6(win.reduce((a, b) => a + ((b - mean) / sd) ** 4, 0) / n - 3)
}

function round4(v) {
  return Math.round(v * 10000) / 10000
}
function round6(v) {
  return Math.round(v * 1e6) / 1e6
}

/* ── 全量特征计算（对齐 stock_features 表字段） ─────────── */

/**
 * 由日线 bar 计算全部可推导的技术特征行。
 * @param {Array} bars 升序 bar 数组（normalizeKline 输出或 mock getBars 输出）
 * @param {Object} [options]
 * @param {string|null} [options.tsCode] 写入每行 ts_code
 * @param {Array<number>|null} [options.turnover] 日换手率序列（与 bars 等长），缺省时 turnover_rate_* 为 null
 */
export function computeIndicators(bars, options = {}) {
  if (!Array.isArray(bars)) throw new Error('K 线输入无效')
  const { tsCode = null, turnover = null } = options
  const n = bars.length
  const close = bars.map((b) => b.close)
  const high = bars.map((b) => b.high)
  const low = bars.map((b) => b.low)
  const vol = bars.map((b) => b.vol ?? 0)
  const open = bars.map((b) => b.open)
  // 真实数据 pre_close 可能缺失：回退前收（首行回退自身收盘，AR/BR 从 i=25 起才消费）
  const preClose = bars.map((b, i) => b.pre_close ?? (i > 0 ? close[i - 1] : close[0]))
  const r2 = (v) => (v == null ? null : round4(v))

  const ma = { 5: sma(close, 5), 10: sma(close, 10), 20: sma(close, 20), 60: sma(close, 60) }
  const emaMap = {
    5: ema(close, 5), 10: ema(close, 10), 20: ema(close, 20),
    26: ema(close, 26), 60: ema(close, 60), 120: ema(close, 120),
  }

  // MACD: DIF = EMA12 - EMA26, DEA = EMA9(DIF), 柱 = 2*(DIF-DEA)
  const e12 = ema(close, 12)
  const e26 = ema(close, 26)
  const dif = e12.map((v, i) => v - e26[i])
  const dea = ema(dif, 9)
  const hist = dif.map((v, i) => round4((v - dea[i]) * 2))

  // RSI(14) Wilder
  const rsi = new Array(n).fill(null)
  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i < n; i++) {
    const ch = close[i] - close[i - 1]
    const gain = Math.max(ch, 0)
    const loss = Math.max(-ch, 0)
    if (i <= 14) {
      avgGain += gain / 14
      avgLoss += loss / 14
      if (i === 14) rsi[i] = round4(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss))
    } else {
      avgGain = (avgGain * 13 + gain) / 14
      avgLoss = (avgLoss * 13 + loss) / 14
      rsi[i] = round4(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss))
    }
  }

  // BOLL(20, 2)
  const bbUpper = new Array(n).fill(null)
  const bbLower = new Array(n).fill(null)
  const bbWidth = new Array(n).fill(null)
  for (let i = 0; i < n; i++) {
    const sd = stdev(close, i, 20)
    if (sd != null && ma[20][i] != null) {
      bbUpper[i] = round4(ma[20][i] + 2 * sd)
      bbLower[i] = round4(ma[20][i] - 2 * sd)
      bbWidth[i] = round4((4 * sd) / ma[20][i])
    }
  }

  // KDJ(9,3,3)
  const kArr = new Array(n).fill(null)
  const dArr = new Array(n).fill(null)
  const jArr = new Array(n).fill(null)
  let k = 50
  let d = 50
  for (let i = 0; i < n; i++) {
    if (i >= 8) {
      let hh = -Infinity
      let ll = Infinity
      for (let j = i - 8; j <= i; j++) {
        hh = Math.max(hh, high[j])
        ll = Math.min(ll, low[j])
      }
      const rsv = hh === ll ? 50 : ((close[i] - ll) / (hh - ll)) * 100
      k = (2 / 3) * k + (1 / 3) * rsv
      d = (2 / 3) * d + (1 / 3) * k
      kArr[i] = round4(k)
      dArr[i] = round4(d)
      jArr[i] = round4(3 * k - 2 * d)
    }
  }

  // 成交量指标
  const volMa5 = sma(vol, 5)
  const volMa10 = sma(vol, 10)
  const volumeRatio = vol.map((v, i) => (volMa5[i] ? round4(v / volMa5[i]) : null))

  // MFI(14)
  const mfi = new Array(n).fill(null)
  const tp = high.map((h, i) => (h + low[i] + close[i]) / 3)
  for (let i = 14; i < n; i++) {
    let pos = 0
    let neg = 0
    for (let j = i - 13; j <= i; j++) {
      const flow = tp[j] * vol[j]
      if (tp[j] > tp[j - 1]) pos += flow
      else neg += flow
    }
    mfi[i] = neg === 0 ? 100 : round4(100 - 100 / (1 + pos / neg))
  }

  // 收益率与标签
  const ret1 = close.map((c, i) => (i === 0 ? null : c / close[i - 1] - 1))
  const ret5 = close.map((c, i) => (i < 5 ? null : c / close[i - 5] - 1))
  const ret10 = close.map((c, i) => (i < 10 ? null : c / close[i - 10] - 1))
  const logRet = close.map((c, i) => (i === 0 ? null : Math.log(c / close[i - 1])))
  const futRet1 = ret1.map((_, i) => (i + 1 < n ? ret1[i + 1] : null))
  const futRet5 = ret5.map((_, i) => (i + 5 < n ? round6(close[i + 5] / close[i] - 1) : null))
  const futDir1 = futRet1.map((v) => (v == null ? null : v > 0 ? 1 : 0))
  const futDir5 = futRet5.map((v) => (v == null ? null : v > 0 ? 1 : 0))

  // 换手率：仅当调用方注入日换手序列时可算（真实数据需 daily-basic 接口），否则为 null
  const dailyTurnover = Array.isArray(turnover) && turnover.length === n ? turnover : null
  const cumTurnover = (win) =>
    dailyTurnover == null
      ? new Array(n).fill(null)
      : dailyTurnover.map((_, i) =>
          i < win - 1 ? null : round4(dailyTurnover.slice(i - win + 1, i + 1).reduce((a, b) => a + b, 0)),
        )

  // 情绪指标 AR / BR (26)
  const ar = new Array(n).fill(null)
  const br = new Array(n).fill(null)
  for (let i = 25; i < n; i++) {
    let ho = 0
    let ol = 0
    let hpc = 0
    let pcl = 0
    for (let j = i - 25; j <= i; j++) {
      ho += high[j] - open[j]
      ol += open[j] - low[j]
      hpc += Math.max(high[j] - preClose[j], 0)
      pcl += Math.max(preClose[j] - low[j], 0)
    }
    ar[i] = ol === 0 ? null : round4((ho / ol) * 100)
    br[i] = pcl === 0 ? null : round4((hpc / pcl) * 100)
  }

  // 多空力道（13 日 EMA）
  const e13 = ema(close, 13)
  const bullPower = high.map((h, i) => round4(h - e13[i]))
  const bearPower = low.map((l, i) => round4(l - e13[i]))

  // BIAS（乖离率）
  const biasOf = (win) => {
    const m = ma[win] || sma(close, win)
    return close.map((c, i) => (m[i] ? round4(((c - m[i]) / m[i]) * 100) : null))
  }
  const bias5 = biasOf(5)
  const bias10 = biasOf(10)
  const bias20 = biasOf(20)
  const bias60 = biasOf(60)

  // CCI(n)
  function cciCalc(win) {
    const tpMa = sma(tp, win)
    return tp.map((_, i) => {
      if (i < win - 1 || tpMa[i] == null) return null
      let md = 0
      for (let j = i - win + 1; j <= i; j++) md += Math.abs(tp[j] - tpMa[i])
      md /= win
      return md === 0 ? null : round4((tp[i] - tpMa[i]) / (0.015 * md))
    })
  }
  const cci10 = cciCalc(10)
  const cci15 = cciCalc(15)
  const cci20 = cciCalc(20)
  const cci88 = cciCalc(88)

  // 梅斯线 Mass Index (9日EMA/9日EMA的EMA, 25日累计)
  const emaRange = ema(high.map((h, i) => h - low[i]), 9)
  const emaRange2 = ema(emaRange, 9)
  const massRatio = emaRange.map((v, i) => (emaRange2[i] ? v / emaRange2[i] : null)).map((v) => (v == null ? 0 : v))
  const mass = massRatio.map((_, i) => (i < 24 ? null : round4(massRatio.slice(i - 24, i + 1).reduce((a, b) => a + b, 0))))

  // Aroon(25)
  const aroonUp = new Array(n).fill(null)
  const aroonDown = new Array(n).fill(null)
  for (let i = 24; i < n; i++) {
    let hiIdx = i
    let loIdx = i
    for (let j = i - 24; j <= i; j++) {
      if (high[j] >= high[hiIdx]) hiIdx = j
      if (low[j] <= low[loIdx]) loIdx = j
    }
    aroonUp[i] = round4(((25 - (i - hiIdx)) / 25) * 100)
    aroonDown[i] = round4(((25 - (i - loIdx)) / 25) * 100)
  }

  // 风险指标（滚动波动率/偏度/峰度，年化）
  const risk = (win, moment) => ret1.map((_, i) => rollingMoment(ret1, i, win, moment))
  const vol20 = risk(20, 2)
  const vol60 = risk(60, 2)
  const vol120 = risk(120, 2)
  const skew20 = risk(20, 3)
  const skew60 = risk(60, 3)
  const skew120 = risk(120, 3)
  const kurt20 = risk(20, 4)
  const kurt60 = risk(60, 4)
  const kurt120 = risk(120, 4)

  // 技术信号
  const goldenCross = ma[5].map((v, i) =>
    i > 0 && v != null && ma[10][i] != null && ma[5][i - 1] <= ma[10][i - 1] && v > ma[10][i] ? 1 : 0,
  )
  const deathCross = ma[5].map((v, i) =>
    i > 0 && v != null && ma[10][i] != null && ma[5][i - 1] >= ma[10][i - 1] && v < ma[10][i] ? 1 : 0,
  )
  const macdGolden = dif.map((v, i) => (i > 0 && dif[i - 1] <= dea[i - 1] && v > dea[i] ? 1 : 0))
  const rsiOversold = rsi.map((v) => (v != null && v < 30 ? 1 : 0))
  const rsiOverbought = rsi.map((v) => (v != null && v > 70 ? 1 : 0))

  const turn5 = cumTurnover(5)
  const turn60 = cumTurnover(60)
  const turn120 = cumTurnover(120)

  return bars.map((b, i) => ({
    ts_code: tsCode,
    trade_date: b.trade_date,
    ma5: r2(ma[5][i]), ma10: r2(ma[10][i]), ma20: r2(ma[20][i]), ma60: r2(ma[60][i]),
    ema5: r2(emaMap[5][i]), ema10: r2(emaMap[10][i]), ema20: r2(emaMap[20][i]),
    ema26: r2(emaMap[26][i]), ema60: r2(emaMap[60][i]), ema120: r2(emaMap[120][i]),
    macd_dif: r2(dif[i]), macd_dea: r2(dea[i]), macd_hist: hist[i],
    rsi: rsi[i],
    bb_upper: bbUpper[i], bb_middle: r2(ma[20][i]), bb_lower: bbLower[i], bb_width: bbWidth[i],
    kdj_k: kArr[i], kdj_d: dArr[i], kdj_j: jArr[i],
    vol_ma5: volMa5[i] == null ? null : Math.round(volMa5[i]),
    vol_ma10: volMa10[i] == null ? null : Math.round(volMa10[i]),
    volume_ratio: volumeRatio[i], mfi14: mfi[i],
    return_1d: ret1[i] == null ? null : round6(ret1[i]),
    return_5d: ret5[i] == null ? null : round6(ret5[i]),
    return_10d: ret10[i] == null ? null : round6(ret10[i]),
    log_return: logRet[i] == null ? null : round6(logRet[i]),
    future_return_1d: futRet1[i] == null ? null : round6(futRet1[i]),
    future_return_5d: futRet5[i],
    future_direction_1d: futDir1[i], future_direction_5d: futDir5[i],
    turnover_rate_5: turn5[i],
    turnover_rate_60: turn60[i],
    turnover_rate_120: turn120[i],
    br: br[i], ar: ar[i],
    volatility_20d: vol20[i], volatility_60d: vol60[i], volatility_120d: vol120[i],
    skewness_20d: skew20[i], skewness_60d: skew60[i], skewness_120d: skew120[i],
    kurtosis_20d: kurt20[i], kurtosis_60d: kurt60[i], kurtosis_120d: kurt120[i],
    arron_up_25: aroonUp[i], arron_down_25: aroonDown[i],
    bear_power: bearPower[i], bull_power: bullPower[i],
    bias5: bias5[i], bias10: bias10[i], bias20: bias20[i], bias60: bias60[i],
    cci10: cci10[i], cci15: cci15[i], cci20: cci20[i], cci88: cci88[i],
    cr20: br[i] == null ? null : round4(br[i] * 0.92),
    mass: mass[i],
    golden_cross: goldenCross[i], death_cross: deathCross[i],
    macd_golden_cross: macdGolden[i],
    rsi_oversold: rsiOversold[i], rsi_overbought: rsiOverbought[i],
  }))
}

/* ── 统计分析（描述统计/直方图/回撤/滚动波动率/相关性/趋势） ── */

export const CORR_FEATURES = ['return_1d', 'rsi', 'macd_dif', 'kdj_k', 'bias20', 'cci20', 'volatility_20d', 'volume_ratio', 'mfi14', 'turnover_rate_5']

function quantile(sorted, q) {
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

function pearson(x, y) {
  const pairs = x.map((v, i) => [v, y[i]]).filter(([a, b]) => a != null && b != null && !isNaN(a) && !isNaN(b))
  const n = pairs.length
  if (n < 3) return 0
  const mx = pairs.reduce((a, p) => a + p[0], 0) / n
  const my = pairs.reduce((a, p) => a + p[1], 0) / n
  let sxy = 0, sxx = 0, syy = 0
  for (const [a, b] of pairs) { sxy += (a - mx) * (b - my); sxx += (a - mx) ** 2; syy += (b - my) ** 2 }
  return sxx && syy ? sxy / Math.sqrt(sxx * syy) : 0
}

/**
 * 由 bar 序列 + computeIndicators 特征行计算统计分析包
 * （返回结构与 AnalysisStats.vue 的消费契约一致）。
 */
export function computeStatistics(bars, feats) {
  if (!Array.isArray(bars) || !Array.isArray(feats) || !bars.length || !feats.length) throw new Error('统计分析输入无效')
  const rets = feats.map((f) => (f.return_1d == null ? null : f.return_1d * 100)).filter((v) => v != null)
  const sorted = [...rets].sort((a, b) => a - b)
  const mean = rets.reduce((a, b) => a + b, 0) / rets.length
  const std = Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / rets.length)
  const m3 = rets.reduce((a, b) => a + ((b - mean) / std) ** 3, 0) / rets.length
  const m4 = rets.reduce((a, b) => a + ((b - mean) / std) ** 4, 0) / rets.length - 3

  // 直方图（24 桶）
  const min = sorted[0], max = sorted[sorted.length - 1]
  const binW = (max - min) / 24
  const bins = Array.from({ length: 24 }, (_, i) => ({
    range: +(min + i * binW).toFixed(2),
    count: 0,
  }))
  for (const r of rets) {
    const idx = Math.min(23, Math.floor((r - min) / binW))
    bins[idx].count++
  }

  // 回撤曲线
  let peak = -Infinity, maxDd = 0, maxDdDate = null
  const drawdown = bars.map((b) => {
    peak = Math.max(peak, b.close)
    const dd = ((b.close - peak) / peak) * 100
    if (dd < maxDd) { maxDd = dd; maxDdDate = b.trade_date }
    return { date: b.trade_date, value: +dd.toFixed(2) }
  })

  // 滚动波动率（年化 %）：rets 已剔除首行 null，与 feats 存在 1 位索引偏移（沿袭既有口径）
  const roll = (w) => feats.map((f, i) => {
    if (i < w) return null
    const win = rets.slice(i - w, i)
    const m = win.reduce((a, b) => a + b, 0) / w
    return +(Math.sqrt(win.reduce((a, b) => a + (b - m) ** 2, 0) / w) * Math.sqrt(252)).toFixed(2)
  })
  const volatility = {
    dates: bars.map((b) => b.trade_date),
    vol20: roll(20), vol60: roll(60), vol120: roll(120),
  }

  // 相关性热力图
  const corr = CORR_FEATURES.map((f1) =>
    CORR_FEATURES.map((f2) => +pearson(feats.map((r) => r[f1]), feats.map((r) => r[f2])).toFixed(2)),
  )

  // 趋势（近 60 日线性回归）
  const win = bars.slice(-60)
  const n = win.length
  const xs = win.map((_, i) => i)
  const ys = win.map((b) => b.close)
  const mx = xs.reduce((a, b) => a + b, 0) / n
  const my = ys.reduce((a, b) => a + b, 0) / n
  let sxy = 0, sxx = 0, syy = 0
  for (let i = 0; i < n; i++) { sxy += (xs[i] - mx) * (ys[i] - my); sxx += (xs[i] - mx) ** 2; syy += (ys[i] - my) ** 2 }
  const slope = sxy / sxx
  const r2 = (sxy * sxy) / (sxx * syy)
  const slopePct = (slope / my) * 100

  return {
    descriptive: {
      mean: +mean.toFixed(4), std: +std.toFixed(4),
      min: +min.toFixed(4), q25: +quantile(sorted, 0.25).toFixed(4),
      q50: +quantile(sorted, 0.5).toFixed(4), q75: +quantile(sorted, 0.75).toFixed(4),
      max: +max.toFixed(4), skewness: +m3.toFixed(4), kurtosis: +m4.toFixed(4),
    },
    histogram: bins,
    drawdown: { series: drawdown, max_drawdown: +maxDd.toFixed(2), max_drawdown_date: maxDdDate },
    volatility,
    correlation: { features: CORR_FEATURES, matrix: corr },
    trend: {
      slope: +slopePct.toFixed(4), r_squared: +r2.toFixed(4),
      direction: slopePct > 0.05 ? 'up' : slopePct < -0.05 ? 'down' : 'flat',
      window: 60,
    },
  }
}
