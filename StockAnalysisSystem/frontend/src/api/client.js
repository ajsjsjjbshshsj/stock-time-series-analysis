import { computed, ref } from 'vue'

// 只接入后端已提供的接口；演示页面不会请求尚未实现的端点。
const LIVE_PATHS = new Set(['/api/consumer/statistics', '/api/consumer/errors', '/actuator/health'])
const env = import.meta.env ?? {}
const normalizeMode = (value) => value === 'hybrid' || value === 'http' ? 'hybrid' : 'mock'
let initialMode = normalizeMode(env.VITE_DATA_SOURCE)
try {
  initialMode = normalizeMode(window.localStorage.getItem('sas-data-source') ?? initialMode)
} catch { /* 禁用存储时仍使用环境配置。 */ }

const preference = ref(initialMode)
const userContext = ref(false)
export const protectedContext = ref(false)
const identity = ref('anonymous')
let identityEpoch = 0
export const dataSource = computed({
  get: () => userContext.value || protectedContext.value ? 'hybrid' : preference.value,
  set: value => { preference.value = normalizeMode(value) },
})
export const requestScope = computed(() => `${dataSource.value}:${identity.value}:${identityEpoch}`)
export function setUserContext(value) { userContext.value = Boolean(value) }
export function setProtectedContext(value) { protectedContext.value = Boolean(value) }
export function setRequestIdentity(value) { identity.value = `${value}:${++identityEpoch}` }

export function setDataSource(value) {
  preference.value = normalizeMode(value)
  try { window.localStorage.setItem('sas-data-source', preference.value) } catch { /* 配置仍在当前会话生效。 */ }
}

export function createRequestClient({ mode = 'mock', base = '', fetchImpl = globalThis.fetch, timeoutMs = 8000, mockDelayMs = 200, allowMock = true } = {}) {
  return async (mockProducer, path, options = {}) => {
    const selected = typeof mode === 'function' ? mode() : mode
    const pathname = path.split('?')[0]
    const evaluationList = pathname === '/api/analysis/evaluations'
    const evaluationDetail = /^\/api\/analysis\/evaluations\/[0-9]{6}\.(?:SZ|SH|BJ)\/eval_[0-9a-f]{32}$/.test(pathname)
    const live = LIVE_PATHS.has(pathname) || pathname === '/api/analysis/stocks' || pathname === '/api/analysis/models' || pathname === '/api/analysis/market/overview' || pathname === '/api/analysis/transformer/signals' || /^\/api\/analysis\/(kline|indicators|prediction|results|daily-basic)\/\d{6}\.(SZ|SH|BJ)$/.test(pathname) || /^\/api\/analysis\/models\/[A-Za-z0-9_-]+\/importance$/.test(pathname)
    if (normalizeMode(selected) === 'mock' || !(live || evaluationList || evaluationDetail)) {
      if (!(typeof allowMock === 'function' ? allowMock() : allowMock)) throw new Error('此功能尚未接入真实接口，当前不可用')
      if (mockDelayMs) await new Promise((resolve) => setTimeout(resolve, mockDelayMs))
      return mockProducer()
    }

    const controller = new AbortController()
    let timedOut = false
    const abort = () => controller.abort()
    if (options.signal?.aborted) controller.abort()
    options.signal?.addEventListener('abort', abort, { once: true })
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, timeoutMs)
    try {
      const response = await fetchImpl(base.replace(/\/$/, '') + path, { credentials: 'same-origin', ...options, signal: controller.signal })
      if (!response.ok && !(path === '/actuator/health' && response.status === 503)) {
        if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('sas-auth-required'))
        const error = new Error(`接口返回 HTTP ${response.status}`)
        error.status = response.status
        // Preserve only a bounded machine code, never reflect backend error text.
        try {
          const body = await response.json()
          if (typeof body?.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(body.code)) error.code = body.code
        } catch { /* HTML/empty errors remain ordinary HTTP failures. */ }
        throw error
      }
      try { return await response.json() } catch { throw new Error('接口返回的内容不是有效 JSON') }
    } catch (error) {
      if (timedOut) throw new Error('请求超时，请检查后端服务是否启动')
      if (error.name === 'AbortError') throw error
      if (error instanceof TypeError) throw new Error('无法连接后端服务，请检查服务地址和代理配置')
      throw error
    } finally {
      clearTimeout(timer)
      options.signal?.removeEventListener('abort', abort)
    }
  }
}

export const request = createRequestClient({ mode: () => dataSource.value, base: env.VITE_API_BASE || '',
  allowMock: () => !userContext.value && !protectedContext.value })
