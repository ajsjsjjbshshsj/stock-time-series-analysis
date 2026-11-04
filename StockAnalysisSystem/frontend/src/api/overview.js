/** Administrator overview: existing GET endpoints only, never mock or computation. */
import { authApi } from './auth.js'
import { normalizeIndicatorReadiness } from '../utils/indicatorReadiness.js'

const invalid = () => { throw new Error('总览接口响应格式无效，请检查服务版本') }
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const count = value => Number.isSafeInteger(value) && value >= 0
function day(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) invalid()
  const parsed = new Date(`${value}T00:00:00Z`)
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) invalid()
}
function counts(value) {
  if (!object(value) || Object.values(value).some(n => !count(n))) invalid()
}
function page(value, kind) {
  if (!object(value) || !Array.isArray(value.items) || value.items.length > 5
      || !(value.next_cursor === null || Number.isSafeInteger(value.next_cursor) && value.next_cursor > 0)) invalid()
  for (const row of value.items) {
    if (!object(row) || typeof row.id !== 'string' || !/^[0-9a-f]{32}$/.test(row.id)) invalid()
    if (kind === 'jobs') {
      if (typeof row.state !== 'string' || !object(row.request)) invalid()
    } else {
      if (!['xgboost', 'lightgbm', 'transformer'].includes(row.algorithm)
          || typeof row.title !== 'string' || !object(row.metadata)) invalid()
      if (kind === 'results' && typeof row.published !== 'boolean') invalid()
      const dateFields = kind === 'models' ? ['train_start', 'train_end'] : ['signal_start', 'signal_end']
      for (const key of dateFields) if (row.metadata[key] != null) day(row.metadata[key])
      const [start, end] = dateFields.map(key => row.metadata[key])
      if (start && end && start > end) invalid()
    }
  }
  return value
}

export function createOverviewApi(client = authApi) {
  const read = (path, validate) => async (options = {}) => {
    try { return validate(await client.request(path, 'GET', undefined, options)) }
    catch (error) {
      // authApi's generic 503 copy describes auth; these dependencies are independent.
      if (error.status === 503) {
        const unavailable = new Error('当前模块依赖服务暂不可用（HTTP 503），请检查对应服务')
        unavailable.status = 503
        throw unavailable
      }
      throw error
    }
  }
  return {
    market: read('/api/analysis/market/overview', value => {
      if (!object(value)) invalid()
      if (Object.keys(value).length === 0) return null // Genuine empty database, not a zero-stock snapshot.
      day(value.trade_date)
      for (const key of ['total', 'up_count', 'down_count', 'flat_count']) if (!count(value[key])) invalid()
      if (value.up_count + value.down_count + value.flat_count > value.total) invalid()
      return value
    }),
    consumer: read('/api/consumer/statistics', value => {
      if (!object(value)) invalid()
      for (const key of ['totalConsumed', 'successCount', 'failureCount', 'dailyEventCount', 'basicEventCount',
        'jsonParseFailureCount', 'validationFailureCount', 'unsupportedSchemaCount', 'deadLetterPublishFailureCount']) {
        if (!count(value[key])) invalid()
      }
      return value
    }),
    health: read('/actuator/health', value => {
      if (!object(value) || !['UP', 'DOWN', 'OUT_OF_SERVICE', 'UNKNOWN'].includes(value.status)) invalid()
      return { status: value.status }
    }),
    collection: read('/api/admin/collector/summary', value => {
      if (!object(value)) invalid()
      counts(value.tasks); counts(value.requests)
      return value
    }),
    runtime: read('/api/admin/collector/capabilities', value => {
      if (!object(value) || !['RUNNING', 'STOPPED', 'STALE_LOCK', 'UNKNOWN'].includes(value.worker)
          || !object(value.scheduler) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value.scheduler.run_at)
          || !(value.scheduler.blocked_error === null || typeof value.scheduler.blocked_error === 'string')) invalid()
      day(value.completed_date)
      return value
    }),
    models: read('/api/admin/workflows/models?limit=5', value => page(value, 'models')),
    results: read('/api/admin/workflows/results?limit=5', value => page(value, 'results')),
    jobs: read('/api/admin/workflows/jobs?limit=5', value => page(value, 'jobs')),
    indicators: read('/api/analysis/indicators/000001.SZ/readiness', value => normalizeIndicatorReadiness(value, '000001.SZ')),
  }
}
export const overviewApi = createOverviewApi()
