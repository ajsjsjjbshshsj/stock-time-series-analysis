/** Independent observations, not an atomic database snapshot or readiness authority. */
const keys = ['market', 'consumer', 'health', 'collection', 'runtime', 'models', 'results', 'jobs', 'indicators']
const empty = () => ({ data: null, error: '', loading: false, observedAt: null })
export function resultSignalRange(metadata) {
  const start = metadata.signal_start, end = metadata.signal_end
  return start && end && start !== end ? `${start} → ${end}` : end || start || '未记录'
}
export function createOverviewState() {
  return Object.fromEntries(keys.map(key => [key, empty()]))
}

export function createOverviewController(api, state, { enabled, now = Date.now }) {
  let generation = 0, controller, disposed = false
  function reset() {
    generation++
    controller?.abort()
    for (const key of keys) Object.assign(state[key], empty())
  }
  async function refresh() {
    reset()
    if (disposed || !enabled()) return
    const run = generation
    controller = new AbortController()
    const signal = controller.signal
    const current = () => !disposed && enabled() && run === generation
    await Promise.all(keys.map(async key => {
      state[key].loading = true
      try {
        const data = await api[key]({ signal })
        if (current()) Object.assign(state[key], { data, observedAt: now() })
      } catch (error) {
        if (current() && error.name !== 'AbortError') state[key].error = error.message || '读取失败，请刷新重试'
      } finally {
        if (current()) state[key].loading = false
      }
    }))
  }
  return { refresh, reset, dispose() { disposed = true; reset() } }
}
