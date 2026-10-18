/** Admin-only real task calls; shares the login client's in-memory CSRF. */
import { authApi } from './auth.js'
const BASE = '/api/admin/training'
function identity(id) { if (!/^[0-9a-f]{32}$/.test(id)) throw new Error('任务编号无效'); return id }
function number(value, min = 1) { if (!Number.isSafeInteger(value) || value < min) throw new Error('分页参数无效'); return value }

export function createTrainingApi(client = authApi) {
  return {
    capabilities: options => client.request(`${BASE}/capabilities`, 'GET', undefined, options),
    list({ limit = 20, before, signal } = {}) {
      const query = new URLSearchParams({ limit: number(limit) })
      if (before != null) query.set('before', number(before))
      return client.request(`${BASE}/jobs?${query}`, 'GET', undefined, { signal })
    },
    create: (body, key, options = {}) => client.request(`${BASE}/jobs`, 'POST', body, { ...options, headers: { 'Idempotency-Key': key } }),
    get: (id, options) => client.request(`${BASE}/jobs/${identity(id)}`, 'GET', undefined, options),
    events(id, { after = 0, limit = 100, signal } = {}) {
      return client.request(`${BASE}/jobs/${identity(id)}/events?after=${number(after, 0)}&limit=${number(limit)}`, 'GET', undefined, { signal })
    },
    cancel: (id, options) => client.request(`${BASE}/jobs/${identity(id)}/cancel`, 'POST', undefined, options),
  }
}
export const trainingApi = createTrainingApi()
