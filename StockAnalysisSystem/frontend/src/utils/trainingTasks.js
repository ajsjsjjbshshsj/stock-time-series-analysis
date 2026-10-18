/** One detail selection owns its requests and timers. Late data cannot cross tasks. */
export const terminalStates = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'])
export const stateLabels = { QUEUED: '等待领取', RUNNING: '执行中', CANCEL_REQUESTED: '等待取消确认', CANCELLED: '已取消', SUCCEEDED: '工程候选已验证', FAILED: '执行失败', INTERRUPTED: '执行中断' }
export const stageLabels = { CREATED: '已入队', QUEUED: '排队', PREFLIGHT: '预检', INPUT: '准备输入', TRAIN: '训练', VERIFY: '重加载验证', SAVE: '候选已保存', DONE: '结束', RECOVERED: '执行槽已恢复', ...stateLabels }

export function createTaskController(api, state = {}, { visible = () => true, enabled = () => true, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  Object.assign(state, { selectedId: null, job: null, events: [], cursor: 0, loading: false, error: '' })
  let version = 0, disposed = false, pending = null, request = null, timer = null
  const current = run => !disposed && run === version && enabled()
  function clear() { if (timer !== null) clearTimer(timer); timer = null }
  function pause() { version++; clear(); request?.abort(); pending = null; state.loading = false }
  function refresh() {
    if (disposed || !state.selectedId || !visible() || !enabled()) return Promise.resolve()
    if (pending) return pending
    clear()
    const run = version, id = state.selectedId, controller = new AbortController()
    request = controller; state.loading = true
    pending = (async () => {
      try {
        const job = await api.get(id, { signal: controller.signal })
        if (!current(run)) return
        const events = await api.events(id, { after: state.cursor, signal: controller.signal })
        if (!current(run)) return
        state.job = job
        const merged = new Map(state.events.map(event => [event.id, event]))
        events.items.forEach(event => merged.set(event.id, event))
        state.events = [...merged.values()].slice(-2000); state.cursor = events.next_cursor; state.error = ''
      } catch (error) { if (current(run) && error.name !== 'AbortError') state.error = error.message }
      finally {
        if (current(run)) {
          pending = null; state.loading = false
          if (visible() && !terminalStates.has(state.job?.state)) timer = setTimer(() => { timer = null; refresh() }, 5000)
        }
      }
    })()
    return pending
  }
  function select(id) {
    pause(); Object.assign(state, { selectedId: id, job: null, events: [], cursor: 0, error: '' })
    return refresh()
  }
  async function cancel() {
    if (!state.selectedId || disposed || !enabled()) throw new Error('尚未选择可操作的任务')
    const id = state.selectedId, run = version
    await api.cancel(id)
    if (current(run) && state.selectedId === id) { pause(); await refresh() }
  }
  return { state, select, refresh, pause, cancel, dispose() { disposed = true; pause() } }
}
