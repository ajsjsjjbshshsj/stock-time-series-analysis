import { STOCK_CODE } from './market.js'

const invalid = () => { throw new Error('指标验收响应格式或状态不一致') }
function day(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value < '2023-01-01') invalid()
  const parsed = new Date(`${value}T00:00:00Z`)
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) invalid()
  return value
}

/** A snapshot upper bound is never translated into a perpetual latest claim. */
export function normalizeIndicatorReadiness(dto, code) {
  if (!STOCK_CODE.test(code) || !dto || dto.ts_code !== code || dto.guarantees_latest !== false || typeof dto.indicator_ready !== 'boolean') invalid()
  const result = { tsCode: code, mode: dto.mode, state: dto.state, indicatorReady: dto.indicator_ready,
    guaranteesLatest: false, verifiedThrough: null, latestObservation: null }
  if (dto.mode === 'LEGACY') {
    if (dto.state !== 'UNVERIFIED' || dto.indicator_ready || dto.verified_through !== null || dto.latest_observation !== null) invalid()
    return Object.freeze(result)
  }
  if (dto.mode !== 'GENERATION' || typeof dto.generation !== 'string' || !/^[0-9a-f]{32}$/.test(dto.generation)
      || !Number.isSafeInteger(dto.epoch) || dto.epoch < 1
      || !Number.isSafeInteger(dto.verified_at) || dto.verified_at < 0) invalid()
  result.verifiedThrough = day(dto.verified_through)
  if (dto.state === 'VERIFIED_SNAPSHOT') {
    if (!dto.indicator_ready) invalid()
    result.latestObservation = day(dto.latest_observation)
    if (result.latestObservation > result.verifiedThrough || typeof dto.is_warmup !== 'boolean') invalid()
    result.isWarmup = dto.is_warmup
  } else if (['NO_SEED', 'REPAIR_REQUIRED', 'SEED_COMPLETE', 'SOURCE_PENDING'].includes(dto.state)) {
    if (dto.indicator_ready || dto.latest_observation !== null) invalid()
  } else invalid()
  return Object.freeze({ ...result, generation: dto.generation, epoch: dto.epoch, verifiedAt: dto.verified_at })
}
