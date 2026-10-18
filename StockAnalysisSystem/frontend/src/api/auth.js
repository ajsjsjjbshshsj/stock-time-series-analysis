/** Authentication never uses mock data, URL passwords or browser token storage. */
export function createAuthClient({ fetchImpl = globalThis.fetch, base = '' } = {}) {
  let csrf = null
  async function call(path, method = 'GET', body, options = {}) {
    const controller = new AbortController()
    let timedOut = false
    const abort = () => controller.abort()
    if (options.signal?.aborted) controller.abort()
    options.signal?.addEventListener('abort', abort, { once: true })
    // Binding the full immutable pool verifies source identities before enqueue.
    // This is still a bounded HTTP create, never synchronous model training.
    const timeoutMs = method === 'POST' && path === '/api/admin/training/jobs' && body?.kind === 'lightgbm_all_market' ? 60000 : 10000
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, timeoutMs)
    try {
      const headers = { ...options.headers, Accept: 'application/json' }
      if (method !== 'GET') {
        if (!csrf) throw new Error('会话尚未就绪，请刷新后重试')
        headers['Content-Type'] = 'application/json'
        headers['X-CSRF-Token'] = csrf
      }
      const response = await fetchImpl(base + path, { method, headers, credentials: 'same-origin',
        signal: controller.signal, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
      if (!response.ok) {
        if (response.status === 401 && path !== '/api/auth/login' && typeof window !== 'undefined') window.dispatchEvent(new Event('sas-auth-required'))
        const messages = { 400: '输入无效，请检查提交的字段及范围', 401: '账号或密码错误，或会话已失效',
          403: '操作无权限，或会话已变更，请刷新后重试', 429: '尝试过于频繁，请在 15 分钟后重试', 503: '认证服务未就绪，请联系管理员检查配置和数据库迁移' }
        if (path.startsWith('/api/admin/training')) Object.assign(messages, { 404: '任务不存在', 409: '任务状态或重复提交内容冲突，请刷新后重试', 503: '训练服务未就绪，请检查任务表、特征库与交易日历配置' })
        const error = new Error(messages[response.status] || `接口返回 HTTP ${response.status}`)
        error.status = response.status
        throw error
      }
      const result = await response.json()
      if (Object.hasOwn(result, 'csrf')) csrf = result.csrf
      return result
    } catch (error) {
      if (error.name === 'AbortError' && timedOut) throw new Error('请求超时，请检查服务后重试')
      if (error instanceof TypeError) throw new Error('无法连接认证服务，请检查网络后重试')
      throw error
    } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort) }
  }
  return {
    request: call,
    session: () => call('/api/auth/session'),
    login: (username, password) => call('/api/auth/login', 'POST', { username, password }),
    logout: () => call('/api/auth/logout', 'POST'),
    password: (old_password, new_password) => call('/api/auth/password', 'POST', { old_password, new_password }),
    users: () => call('/api/admin/users'),
    createUser: body => call('/api/admin/users', 'POST', body),
    updateUser: (id, body) => call(`/api/admin/users/${encodeURIComponent(id)}`, 'PATCH', body),
    clear: () => { csrf = null },
  }
}

export const authApi = createAuthClient()
