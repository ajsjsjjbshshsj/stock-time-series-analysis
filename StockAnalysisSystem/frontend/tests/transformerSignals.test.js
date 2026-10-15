import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeTransformerSignals } from '../src/utils/transformerSignals.js'
import { getTransformerSignals, TF_SIGNAL_UNIVERSE } from '../src/mock/analysis.js'

const clone = (v) => JSON.parse(JSON.stringify(v))
const dto = () => getTransformerSignals('2026-10-08')

test('演示信号包与 live schema 完全同形且通过严格归一化', () => {
  const signals = dto()
  assert.equal(normalizeTransformerSignals(signals), signals)
  // 确定性：同日期两次生成逐字节一致（种子随机）
  assert.deepEqual(dto(), signals)
  assert.equal(signals.signal_date, '2026-10-08')
  assert.equal(signals.status, 'LATE_DIAGNOSTIC')
  assert.equal(signals.prospective_eligible, false)
  assert.equal(signals.primary_policy, 'raw')
  assert.deepEqual(signals.universe, TF_SIGNAL_UNIVERSE)
  assert.equal(signals.universe.length, 20)
  assert.equal(signals.models.length, 6)
  assert.equal(signals.consensus.length, 20)
  assert.deepEqual([...signals.available_dates], ['2026-10-08'])
  // 排名是 1..20 的排列；主排名与 mean_raw 降序一致（tie-break 代码升序）
  for (const key of ['rank_raw', 'rank_nonnegative_variance']) {
    assert.deepEqual(signals.consensus.map((r) => r[key]).sort((a, b) => a - b),
      Array.from({ length: 20 }, (_, i) => i + 1))
  }
  const byRank = [...signals.consensus].sort((a, b) => a.rank_raw - b.rank_raw)
  byRank.forEach((row, i) => {
    if (i > 0) {
      const prev = byRank[i - 1]
      assert.ok(prev.mean_raw > row.mean_raw
        || (prev.mean_raw === row.mean_raw && prev.ts_code < row.ts_code))
    }
  })
  // 6 模型身份：3 seed × 2 mode，平等保留
  const identities = signals.models.map((m) => `${m.seed}:${m.mode}`).sort()
  assert.deepEqual(identities, [42, 123, 2026]
    .flatMap((s) => ['adjusted', 'unadjusted_control'].map((m) => `${s}:${m}`)).sort())
  // 分数是排序分：允许为负，绝不是 0..1 的"收益率"
  assert.ok(signals.consensus.some((r) => r.scores.some((s) => s.raw < 0)))
})

test('归一化对契约破坏一律抛错，绝不静默降级', () => {
  assert.throws(() => normalizeTransformerSignals(null), /信号包格式无效/)
  assert.throws(() => normalizeTransformerSignals([]), /信号包格式无效/)
  const cases = [
    [/信号状态无效/, (d) => { d.status = 'ALMOST_PROSPECTIVE' }],
    [/前瞻资格与状态矛盾/, (d) => { d.status = 'PROSPECTIVE_CANDIDATE' }],
    [/发布时间与截止矛盾/, (d) => { d.published_at = '2026-10-09T08:00:00+08:00' }],
    [/信号日期无效/, (d) => { d.signal_date = '2026-1-8' }],
    [/持有窗口日期无效/, (d) => { d.exit_date = d.entry_date }],
    [/信号日必须晚于数据截止/, (d) => { d.cutoff = d.signal_date }],
    [/信号库标识无效/, (d) => { d.store = 'F:\\secrets\\forward_signals_x' }],
    [/可用信号日期无效/, (d) => { d.available_dates = ['2026-10-09'] }],
    [/主排序口径无效/, (d) => { d.primary_policy = 'nonnegative_variance' }],
    [/试点股票池无效/, (d) => { d.universe.pop() }],
    [/冻结模型身份无效/, (d) => { d.models[0].seed = 7 }],
    [/模型信号日期与信号包不一致/, (d) => { d.models[0].date = '2026-10-09' }],
    [/模型指纹无效/, (d) => { d.models[0].model_sha256 = 'abc123' }],
    [/Top5 榜单无效/, (d) => { d.models[0].top5.raw[0] = '600519.SH' }],
    [/共识排行与股票池不一致/, (d) => { d.consensus[0].ts_code = '000003.SZ' }],
    [/非有限数值/, (d) => { d.consensus[0].mean_raw = NaN }],
    [/共识模型明细无效/, (d) => { d.consensus[0].scores.pop() }],
    [/共识均值与明细不一致/, (d) => { d.consensus[0].mean_raw += 0.5 }],
    [/主口径排名无效/, (d) => { d.consensus[0].rank_raw = 99 }],
    [/次级口径排名无效/, (d) => { d.consensus[3].rank_nonnegative_variance = 21 }],
    [/完整性复核未通过/, (d) => { d.integrity.verified = false }],
    [/Marker 指纹无效/, (d) => { d.integrity.marker_sha256 = 'x'.repeat(64) }],
    [/Receipt 指纹无效/, (d) => { d.integrity.receipt_sha256 = 'X'.repeat(64) }],
    [/Payload 指纹无效/, (d) => { delete d.integrity.payload_sha256 }],
  ]
  for (const [pattern, mutate] of cases) {
    const bad = clone(dto())
    mutate(bad)
    assert.throws(() => normalizeTransformerSignals(bad), pattern)
  }
})
