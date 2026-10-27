/** Real administrator collection control plane; never falls back to mock. */
import { authApi } from './auth.js'
const BASE = '/api/admin/collector'
function identity(id) {
  if (typeof id !== 'string' || !/^[0-9a-f]{32}$/.test(id)) throw new Error('采集任务编号无效')
  return id
}
function page(client, path, { limit = 20, offset = 0, request_id, signal } = {}) {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 50 || !Number.isSafeInteger(offset) || offset < 0 || offset > 100000) throw new Error('采集分页参数无效')
  const query = new URLSearchParams({ limit, offset })
  if (request_id) query.set('request_id', identity(request_id))
  return client.request(`${BASE}/${path}?${query}`, 'GET', undefined, { signal })
}
export function createCollectorApi(client = authApi) {
  return {
    capabilities: options => client.request(`${BASE}/capabilities`, 'GET', undefined, options),
    summary: options => client.request(`${BASE}/summary`, 'GET', undefined, options),
    requests: options => page(client, 'requests', options),
    tasks: options => page(client, 'tasks', options),
    task: (id, options) => client.request(`${BASE}/tasks/${identity(id)}`, 'GET', undefined, options),
    submit: (body, key, options = {}) => {
      if (typeof body?.start !== 'string' || typeof body?.end !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(body.start) || !/^\d{4}-\d{2}-\d{2}$/.test(body.end) || body.start > body.end) throw new Error('请检查采集起止日期')
      return client.request(`${BASE}/requests`, 'POST', body, { ...options, headers: { 'Idempotency-Key': identity(key) } })
    },
    retry: (id, key, options = {}) => client.request(`${BASE}/tasks/${identity(id)}/retry`, 'POST', {}, { ...options, headers: { 'Idempotency-Key': identity(key) } }),
    preview: (table, options) => {
      if (!['stock_basic', 'stock_daily', 'stock_daily_basic'].includes(table)) throw new Error('数据表无效')
      return client.request(`${BASE}/preview/${table}?limit=20`, 'GET', undefined, options)
    },
  }
}
export const collectorApi = createCollectorApi()
