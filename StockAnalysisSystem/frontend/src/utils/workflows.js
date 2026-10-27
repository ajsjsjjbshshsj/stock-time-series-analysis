export const operationLabels = { train: '训练模型', predict: '生成预测', backtest: '运行回测', import: '导入旧候选' }
/** Display verified saved metadata only; never infer a date from the clock. */
export function resultDisplayContext(value = {}) {
  const context = value.context || {}, report = value.report || {}
  const count = Number.isInteger(context.pool_count) && context.pool_count > 0 ? context.pool_count : null
  const registry = context.scope === 'fixed_registry' && typeof context.pool_version === 'string' && /^(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})$/.test(context.pool_version)
  return {
    dataCutoff: report.data_cutoff || context.source_cutoff || '未记录',
    signalEnd: report.signal_end || '未记录',
    poolLabel: count == null ? '未记录' : `${context.scope === 'all_shsz_a' ? '沪深 A 股全市场' : '固定股票池'} · ${count} 只`,
    stockCodes: Array.isArray(context.stock_codes) && context.stock_codes.length <= 20 ? context.stock_codes.join('、') : '',
    poolVersion: registry ? context.pool_version : '',
    poolProvider: registry ? context.provider || '未记录' : '',
    poolSnapshotDate: registry ? context.provider_snapshot_date || '无历史成分日期' : '',
    poolGroup: registry ? context.group_code || '未记录' : '',
    registryStockCount: registry && Number.isInteger(context.registry_stock_count) ? context.registry_stock_count : null,
    provenance: context.provenance === 'sealed_binding' ? '来自已核验封存元数据；源数据截止不等于模型训练截止或信号日期' : '旧报告未记录来源边界',
  }
}
export function scoreLabel(unit) {
  return ({ probability_up_1d: '下一交易日上涨概率（0–1）', daily_rank: '截面排名分数（不是概率）', within_day_centered_rank: '中心化截面排名分数（不是概率）', raw_return_5d: '第5交易日收益预测', return_5d: '第5交易日收益预测' })[unit] || `${unit || '未知量纲'}（不同模型分数不可直接比较）`
}
export function buildWorkflowRequest(d) {
  const r = { kind: 'model_workflow', operation: d.operation, algorithm: d.algorithm }
  if (d.operation === 'import') {
    if (d.import_task_id?.trim()) r.import_task_id = d.import_task_id.trim()
    if (d.experiment_id?.trim()) { r.experiment_id = d.experiment_id.trim(); r.trial = d.trial }
    if (Boolean(r.import_task_id) === Boolean(r.experiment_id)) throw new Error('请选择一种导入来源')
    return r
  }
  Object.assign(r, { start_date: d.start_date, end_date: d.end_date })
  if (d.feature_build_id) {
    if (!/^[0-9a-f]{32}$/.test(d.feature_build_id)) throw new Error('特征版本无效')
    r.feature_build_id = d.feature_build_id
  }
  if (!r.start_date || !r.end_date || r.start_date > r.end_date) throw new Error('请检查输入日期')
  if (d.operation !== 'train') {
    if (!/^[0-9a-f]{32}$/.test(d.model_id || '')) throw new Error('请选择已保存的模型版本')
    r.model_id = d.model_id
    if (d.operation === 'backtest') Object.assign(r, { top_n: d.top_n, initial_capital: d.initial_capital, fee_bps: d.fee_bps, slippage_bps: d.slippage_bps })
    return r
  }
  if (d.pool_version != null && d.pool_version !== '') {
    if (typeof d.pool_version !== 'string' || !/^(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})$/.test(d.pool_version)) throw new Error('请选择有效的固定股票池版本')
    r.pool_version = d.pool_version
  }
  if (d.algorithm === 'xgboost') Object.assign(r, { ts_code: d.ts_code.trim().toUpperCase(), n_estimators: d.n_estimators, max_depth: d.max_depth })
  else if (d.algorithm === 'lightgbm') Object.assign(r, { n_estimators: d.n_estimators, label: d.label, early_metric: d.early_metric, patience: d.patience })
  else if (d.algorithm === 'transformer') {
    const stocks = d.stock_codes.trim().toUpperCase().split(/[\s,，;；]+/).filter(Boolean)
    if (stocks.length < 10 || stocks.length > 20 || new Set(stocks).size !== stocks.length || stocks.some(s => !/^[0-9]{6}\.(SH|SZ)$/.test(s))) throw new Error('Transformer 需要10–20只不重复的沪深股票')
    Object.assign(r, { stock_codes: stocks.sort(), num_epochs: d.num_epochs, batch_size: d.batch_size, use_probe_selection: true })
  } else throw new Error('训练器无效')
  return r
}
/** Abort + generation ownership prevents late results crossing logout/page changes. */
export function createWorkflowController(api, state = {}, { enabled = () => true, visible = () => true, poll = false, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  Object.assign(state, { items: [], next_cursor: null, before: null, error: '', loading: false })
  let generation = 0, request, timer, disposed = false
  function pause() { generation++; request?.abort(); clearTimer(timer); state.loading = false }
  function reset() { pause(); Object.assign(state, { items: [], next_cursor: null, before: null, error: '' }) }
  async function refresh(before = state.before) {
    pause()
    if (disposed || !enabled() || !visible()) return
    const run = generation; request = new AbortController(); state.loading = true; state.before = before
    try {
      const data = await api.list({ before, signal: request.signal })
      if (!disposed && run === generation && enabled()) Object.assign(state, { items: data.items, next_cursor: data.next_cursor, error: '' })
    } catch (e) { if (!disposed && run === generation && enabled() && e.name !== 'AbortError') { state.items = []; state.error = e.message } }
    finally { if (!disposed && run === generation) { state.loading = false; if (poll && enabled() && visible()) timer = setTimer(() => refresh(), 5000) } }
  }
  return { refresh, pause, reset, dispose() { disposed = true; reset() } }
}
