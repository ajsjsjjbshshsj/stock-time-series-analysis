/**
 * 冻结 Transformer 信号包（GET /api/analysis/transformer/signals）的严格归一化。
 * 文化对齐 utils/prediction.js：任何契约破坏直接抛错，绝不静默降级。
 * 分数是无量纲排序分（可为负），任何 UI 都不得将其呈现为收益率。
 */
import { finiteJson } from './prediction.js'

const DAY = /^\d{4}-\d{2}-\d{2}$/
const MOMENT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+08:00$/
const SHA256 = /^[0-9a-f]{64}$/
const CODE = /^[0-9]{6}\.(?:SZ|SH|BJ)$/
const STATUSES = ['PROSPECTIVE_CANDIDATE', 'LATE_DIAGNOSTIC']
const IDENTITIES = [42, 123, 2026]
  .flatMap((seed) => ['adjusted', 'unadjusted_control'].map((mode) => `${seed}:${mode}`))
  .sort()
  .join(',')

function day(value, message) {
  if (typeof value !== 'string' || !DAY.test(value) || Number.isNaN(Date.parse(value))) throw new Error(message)
}

function moment(value, message) {
  if (typeof value !== 'string' || !MOMENT.test(value) || Number.isNaN(Date.parse(value))) throw new Error(message)
}

function sha(value, message) {
  if (typeof value !== 'string' || !SHA256.test(value)) throw new Error(message)
}

function sameIdentities(list, message) {
  if (!Array.isArray(list) || list.length !== 6) throw new Error(message)
  const got = list.map((x) => `${x?.seed}:${x?.mode}`).sort().join(',')
  if (got !== IDENTITIES) throw new Error(message)
}

function ranksMatch(consensus, meanKey, rankKey, message) {
  const ordered = [...consensus].sort((a, b) => b[meanKey] - a[meanKey] || (a.ts_code < b.ts_code ? -1 : 1))
  ordered.forEach((row, i) => {
    if (!Number.isSafeInteger(row[rankKey]) || row[rankKey] !== i + 1) throw new Error(message)
  })
}

export function normalizeTransformerSignals(dto) {
  finiteJson(dto)
  if (!dto || typeof dto !== 'object' || Array.isArray(dto)) throw new Error('信号包格式无效')
  day(dto.signal_date, '信号日期无效')
  if (!STATUSES.includes(dto.status)) throw new Error('信号状态无效')
  if (typeof dto.prospective_eligible !== 'boolean'
    || dto.prospective_eligible !== (dto.status === 'PROSPECTIVE_CANDIDATE')) throw new Error('前瞻资格与状态矛盾')
  moment(dto.published_at, '发布时间无效')
  moment(dto.deadline, '开盘截止无效')
  if ((Date.parse(dto.published_at) < Date.parse(dto.deadline)) !== dto.prospective_eligible) throw new Error('发布时间与截止矛盾')
  day(dto.entry_date, '入场日无效')
  day(dto.exit_date, '出场日无效')
  if (!(dto.entry_date > dto.signal_date && dto.exit_date > dto.entry_date)) throw new Error('持有窗口日期无效')
  moment(dto.frozen_at, '模型冻结时间无效')
  day(dto.cutoff, '数据截止日无效')
  if (!(dto.signal_date > dto.cutoff)) throw new Error('信号日必须晚于数据截止')
  if (typeof dto.store !== 'string' || !/^forward_signals_[A-Za-z0-9_-]+$/.test(dto.store)) throw new Error('信号库标识无效')
  if (!Array.isArray(dto.available_dates) || !dto.available_dates.length
    || !dto.available_dates.includes(dto.signal_date)) throw new Error('可用信号日期无效')
  dto.available_dates.forEach((d) => day(d, '可用信号日期无效'))
  if (dto.primary_policy !== 'raw') throw new Error('主排序口径无效')

  const universe = dto.universe
  if (!Array.isArray(universe) || universe.length !== 20
    || universe.some((c) => typeof c !== 'string' || !CODE.test(c))
    || new Set(universe).size !== 20
    || universe.join() !== [...universe].sort().join()) throw new Error('试点股票池无效')

  sameIdentities(dto.models, '冻结模型身份无效')
  dto.models.forEach((m) => {
    day(m.date, '模型信号日期无效')
    if (m.date !== dto.signal_date) throw new Error('模型信号日期与信号包不一致')
    sha(m.model_sha256, '模型指纹无效')
    for (const policy of ['raw', 'nonnegative_variance']) {
      const top = m.top5?.[policy]
      if (!Array.isArray(top) || top.length !== 5 || new Set(top).size !== 5
        || top.some((c) => !universe.includes(c))) throw new Error('Top5 榜单无效')
    }
  })

  const consensus = dto.consensus
  if (!Array.isArray(consensus) || consensus.length !== 20
    || consensus.map((r) => r.ts_code).join() !== universe.join()) throw new Error('共识排行与股票池不一致')
  consensus.forEach((row) => {
    if (typeof row.mean_raw !== 'number' || typeof row.mean_nonnegative_variance !== 'number') throw new Error('共识分数无效')
    sameIdentities(row.scores, '共识模型明细无效')
    row.scores.forEach((s) => {
      if (typeof s.raw !== 'number' || typeof s.nonnegative_variance !== 'number') throw new Error('模型分数无效')
    })
    for (const [key, meanKey] of [['raw', 'mean_raw'], ['nonnegative_variance', 'mean_nonnegative_variance']]) {
      const avg = row.scores.reduce((a, s) => a + s[key], 0) / row.scores.length
      if (Math.abs(avg - row[meanKey]) > 1e-6) throw new Error('共识均值与明细不一致')
    }
  })
  ranksMatch(consensus, 'mean_raw', 'rank_raw', '主口径排名无效')
  ranksMatch(consensus, 'mean_nonnegative_variance', 'rank_nonnegative_variance', '次级口径排名无效')

  if (dto.integrity?.verified !== true) throw new Error('完整性复核未通过')
  sha(dto.integrity.marker_sha256, 'Marker 指纹无效')
  sha(dto.integrity.receipt_sha256, 'Receipt 指纹无效')
  sha(dto.integrity.binding_sha256, 'Binding 指纹无效')
  sha(dto.integrity.payload_sha256, 'Payload 指纹无效')
  return dto
}
