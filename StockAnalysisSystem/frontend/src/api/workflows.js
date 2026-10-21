/** Always real, same-origin session calls. No mock or client-side model execution. */
import { authApi } from './auth.js'
import { createTrainingApi } from './training.js'
const ADMIN = '/api/admin/workflows', PUBLIC = '/api/analysis/published-results'
function identity(id) { if (!/^[0-9a-f]{32}$/.test(id)) throw new Error('版本编号无效'); return id }
function integer(n, min, max) { if (!Number.isSafeInteger(n) || n < min || n > max) throw new Error('分页参数无效'); return n }
function list(client, base, { limit = 20, before, signal } = {}) {
  const query = new URLSearchParams({ limit: integer(limit, 1, 20) })
  if (before != null) query.set('before', integer(before, 1, Number.MAX_SAFE_INTEGER))
  return client.request(`${base}?${query}`, 'GET', undefined, { signal })
}
function get(client, base, id, { section = 'scores', limit = 50, after = 0, ts_code, signal } = {}) {
  identity(id)
  if (!['scores', 'equity', 'trades', 'orders', 'writeoffs', 'requirements'].includes(section)) throw new Error('结果分区无效')
  const query = new URLSearchParams({ section, limit: integer(limit, 1, 100), after: integer(after, 0, 10000000) })
  if (ts_code) { if (!/^[0-9]{6}\.(SH|SZ)$/.test(ts_code)) throw new Error('仅支持沪深股票'); query.set('ts_code', ts_code) }
  return client.request(`${base}/${id}?${query}`, 'GET', undefined, { signal })
}
export function createPublishedApi(client = authApi) {
  return { list: options => list(client, PUBLIC, options), get: (id, options) => get(client, PUBLIC, id, options) }
}
export function createWorkflowApi(client = authApi) {
  const tasks = createTrainingApi(client)
  return {
    capabilities: options => client.request(`${ADMIN}/capabilities`, 'GET', undefined, options),
    list: options => list(client, `${ADMIN}/jobs`, options),
    models: options => list(client, `${ADMIN}/models`, options),
    results: options => list(client, `${ADMIN}/results`, options),
    result: (id, options) => get(client, `${ADMIN}/results`, id, options),
    create: (body, key, options = {}) => client.request(`${ADMIN}/jobs`, 'POST', body, { ...options, headers: { 'Idempotency-Key': key } }),
    get: tasks.get, events: tasks.events, cancel: tasks.cancel,
    publish: (id, options) => client.request(`${ADMIN}/results/${identity(id)}/publish`, 'POST', undefined, options),
    withdraw: (id, options) => client.request(`${ADMIN}/results/${identity(id)}/withdraw`, 'POST', undefined, options),
  }
}
export const workflowApi = createWorkflowApi()
export const publishedApi = createPublishedApi()
