/**
 * 股票近似搜索：纯函数打分引擎（无 Vue 依赖，node --test 直接可测）。
 *
 * 匹配层级（单 token × 单字段，命中即返回，天然按层降序）：
 *   EXACT 1000 > PREFIX 800 > CONTAINS 600−min(idx,50) > SUBSEQ 400−gaps×40 > FUZZY(d=1) 200 > FUZZY(d=2) 100
 * 字段权重：name/code ×1，industry ×0.9，area ×0.85。
 * 单 token 对预处理字段取 max（结构性避免 symbol/ts_code 重复计分）；
 * 多 token（空白分隔，AND 语义）求和，任一 token 零分即淘汰。
 *
 * 近似能力：
 * - 子序列：'平银'→平安银行（gap 上限：2 字 token≤2，≥3 字≤3，防短数字串大面积误报）
 * - 错字容忍：有界"近似子串"编辑距离，阈值随 token 长度自适应（len≥6 允许 d≤2，否则 d≤1）
 * - 归一化：NFKC（全角→半角）+ 小写（.SZ→.sz）
 */

const LAYER = { EXACT: 1000, PREFIX: 800, CONTAINS: 600, SUBSEQ: 400, FUZZY1: 200, FUZZY2: 100 }
const GAP_PENALTY = 40
const WEIGHT = { name: 1, code: 1, industry: 0.9, area: 0.85 }
const DEFAULT_LIMIT = 50
const MAX_TOKEN = 12        // 超过此长度的 token 不参与匹配（ts_code 最长 9 字符）
const MAX_FUZZY_TOKEN = 8   // 编辑距离仅对短 token 计算，控制最坏路径成本
const CONTAINS_IDX_CAP = 50

export function norm(s) {
  return typeof s === 'string' ? s.normalize('NFKC').toLowerCase() : ''
}

/**
 * 池加载时构建一次索引（O(n)），searchStocks 复用。
 * list_date 不参与匹配（防 '1991' 之类年份误报）。不改动入参。
 */
export function buildSearchIndex(stocks) {
  const entries = []
  for (const stock of Array.isArray(stocks) ? stocks : []) {
    if (!stock || typeof stock.ts_code !== 'string') continue
    entries.push({
      stock,
      name: norm(stock.name),
      code: norm(stock.symbol ?? stock.ts_code.split('.')[0]),
      codeFull: norm(stock.ts_code),
      codeFlat: norm(stock.ts_code).replace(/\./g, ''), // 匹配无点输入 '600519sh'
      industry: norm(stock.industry),
      area: norm(stock.area),
    })
  }
  return { entries }
}

/**
 * 贪心最左子序列匹配，返回首末命中字符跨度内的 gap 数；非子序列返回 -1。
 * 贪心非全局最优 span，但确定性强、O(n)、可测——极端下仅影响同层内排序。
 */
export function subsequenceGaps(pattern, text) {
  let i = 0
  let first = -1
  let last = -1
  for (let j = 0; j < text.length && i < pattern.length; j++) {
    if (text[j] === pattern[i]) {
      if (first < 0) first = j
      last = j
      i++
    }
  }
  return i === pattern.length ? (last - first + 1) - pattern.length : -1
}

/** 错字容忍阈值随 token 长度自适应：单字不容错（防全库命中），≥6 字符允许 d≤2 */
export function maxDist(token) {
  if (token.length < 2) return 0
  return token.length >= 6 ? 2 : 1
}

/**
 * pattern 与 text 任意子串的最小编辑距离（半全局 DP：首列置 0 允许任意起点）。
 * 字段 ≤12 字符、token ≤8 → 单次 DP ≤108 格；调用方保证前置层级已落空。
 * 注意不能按"行最小值>max"早退——cur[0]=0 使下一行 j=1 恒 ≤1。
 */
export function approxContainsDistance(text, pattern) {
  const m = pattern.length
  const n = text.length
  if (!m) return 0
  let prev = Array.from({ length: m + 1 }, (_, j) => j)
  let best = prev[m]
  for (let i = 1; i <= n; i++) {
    const cur = new Array(m + 1)
    cur[0] = 0
    for (let j = 1; j <= m; j++) {
      cur[j] = Math.min(
        prev[j] + 1,
        cur[j - 1] + 1,
        prev[j - 1] + (text[i - 1] === pattern[j - 1] ? 0 : 1),
      )
    }
    if (cur[m] < best) best = cur[m]
    prev = cur
  }
  return best
}

/** 单 token × 单字段分层打分；weight 为字段权重 */
export function scoreField(token, text, weight) {
  if (!text || token.length > MAX_TOKEN) return 0
  if (token === text) return Math.round(LAYER.EXACT * weight)
  const idx = text.indexOf(token)
  if (idx === 0) return Math.round(LAYER.PREFIX * weight)
  if (idx > 0) return Math.round((LAYER.CONTAINS - Math.min(idx, CONTAINS_IDX_CAP)) * weight)
  if (token.length >= 2) {
    const gaps = subsequenceGaps(token, text)
    const gapCap = token.length >= 3 ? 3 : 2
    if (gaps >= 0 && gaps <= gapCap) return Math.round((LAYER.SUBSEQ - gaps * GAP_PENALTY) * weight)
    const t = maxDist(token)
    if (t > 0 && token.length <= MAX_FUZZY_TOKEN) {
      const d = approxContainsDistance(text, token)
      if (d === 1) return Math.round(LAYER.FUZZY1 * weight)
      if (d === 2 && t >= 2) return Math.round(LAYER.FUZZY2 * weight)
    }
  }
  return 0
}

function scoreToken(token, e) {
  return Math.max(
    scoreField(token, e.name, WEIGHT.name),
    scoreField(token, e.code, WEIGHT.code),
    scoreField(token, e.codeFull, WEIGHT.code),
    scoreField(token, e.codeFlat, WEIGHT.code),
    scoreField(token, e.industry, WEIGHT.industry),
    scoreField(token, e.area, WEIGHT.area),
  )
}

export function queryTokens(query) {
  return norm(query).trim().split(/\s+/).filter(Boolean)
}

/**
 * 主入口：返回 [{ stock, score }]，按 score 降序、平局按 ts_code 升序（确定性），截断 limit。
 * 空/全空白查询返回池前 limit 只（score 0），保持可浏览性。
 * 性能：6000 行最坏全 miss 路径（全字段走 DP）<10ms，无需防抖；
 * 若未来实测卡顿，可在组件层加 ~120ms 防抖作为扩展点。
 */
export function searchStocks(index, query, { limit = DEFAULT_LIMIT } = {}) {
  const entries = index?.entries ?? []
  const tokens = queryTokens(query)
  if (!tokens.length) return entries.slice(0, limit).map((e) => ({ stock: e.stock, score: 0 }))
  const out = []
  for (const e of entries) {
    let total = 0
    let ok = true
    for (const t of tokens) {
      const s = scoreToken(t, e)
      if (!s) { ok = false; break }
      total += s
    }
    if (ok) out.push({ stock: e.stock, score: total })
  }
  out.sort((a, b) => b.score - a.score
    || (a.stock.ts_code < b.stock.ts_code ? -1 : a.stock.ts_code > b.stock.ts_code ? 1 : 0))
  return out.slice(0, limit)
}

export function formatStockLabel(stock) {
  return stock ? `${stock.ts_code} · ${stock.name}` : ''
}
