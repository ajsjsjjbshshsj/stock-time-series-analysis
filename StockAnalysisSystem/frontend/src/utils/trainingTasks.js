/** One detail selection owns its requests and timers. Late data cannot cross tasks. */
export const terminalStates = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'])
export const stateLabels = { QUEUED: '等待领取', RUNNING: '执行中', CANCEL_REQUESTED: '等待取消确认', CANCELLED: '已取消', SUCCEEDED: '工程候选已验证', FAILED: '执行失败', INTERRUPTED: '执行中断' }
export const stageLabels = { CREATED: '已入队', QUEUED: '排队', PREFLIGHT: '预检', INPUT: '准备输入', PROBE: '探针筛选', PROGRESS: '进度', TRAIN: '训练', VERIFY: '重加载验证', SAVE: '候选已保存', DONE: '结束', RECOVERED: '执行槽已恢复', ...stateLabels }

export function buildTrainingRequest(draft) {
  const common = { kind: draft.kind, purpose: 'engineering_smoke', start_date: draft.start_date, end_date: draft.end_date }
  if (draft.kind === 'lightgbm_all_market') return { ...common, scope: 'all_shsz_a', profile: 'bounded_v1' }
  if (draft.kind === 'xgboost_single') return { ...common, ts_code: draft.ts_code.trim().toUpperCase(), n_estimators: draft.n_estimators, max_depth: draft.max_depth }
  throw new Error('此训练器尚未接入')
}

export function formatTaskProgress(value) {
  if (!value || !['INPUT', 'PROBE', 'TRAIN'].includes(value.phase) || !Number.isInteger(value.completed) || !Number.isInteger(value.total) || value.total < 1 || value.total > 10000000 || value.completed < 0 || value.completed > value.total) return ''
  return `${stageLabels[value.phase]}：${value.completed} / ${value.total}`
}

export const failureLabels = {
  RESOURCE_UNAVAILABLE: '可用内存不足 8 GiB 或临时磁盘不足 16 GiB，未开始构建矩阵。',
  RESOURCE_LIMIT: '训练进程树超过 6 GiB 内存上限，或临时磁盘剩余不足 2 GiB；任务已停止，不会缩小股票池重试。',
  TIME_LIMIT: '全市场工程任务超过 60 分钟上限，已停止。',
  INPUT_INVALID: '输入资格校验失败，请检查后台保存的缺口、上市日期或成熟特征诊断；未静默丢弃异常股票。',
  SOURCE_CHANGED: '绑定的数据、参考、日历或运行代码发生变化；请核对后重新创建任务。',
}

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
