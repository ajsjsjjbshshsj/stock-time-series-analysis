<template>
  <div class="task-page">
    <section class="card task-heading">
      <div><p class="muted">后台研究与特征任务 · 单任务执行</p><h1>训练与准备任务</h1><p>任务在独立 worker 中执行，关闭页面不影响执行。特征任务只生成未启用暂存；模型任务不会自动发布或覆盖旧模型。</p></div>
      <button class="btn" @click="refreshPage">刷新任务与能力</button>
    </section>
    <p v-if="error" class="task-alert" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="caps?.execution.quarantined" class="task-alert" role="alert">执行槽已隔离：旧进程退出状态尚未确认。新任务不会开始，请在原机器核查后通过受控恢复命令处理。</p>
    <section class="card task-section">
      <h2>创建工程任务</h2><p class="muted">这是保留的工程验证入口。三模型正式工作流、参数、预测、回测与发布请使用 <RouterLink to="/models">模型工作台</RouterLink>。只读取已核验数据，不触发采集。</p>
      <p v-if="caps?.source" class="muted">可用输入：{{ caps.source.source_start }} → {{ caps.source.source_cutoff }} · 构建 {{ caps.source.build_id || '已配置' }}</p>
      <form class="task-form" @submit.prevent="createJob">
        <label>训练器<select v-model="draft.kind" aria-label="训练器"><option value="xgboost_single">单股 XGBoost · 工程验证</option><option value="lightgbm_all_market" :disabled="!marketCapability?.enabled">全市场 LightGBM · 保留探针筛选</option><option value="transformer" disabled>Transformer · 未接入</option></select></label>
        <label v-if="!isMarket">股票代码<input v-model="draft.ts_code" required maxlength="9" placeholder="000001.SZ" /></label>
        <label>输入开始<input v-model="draft.start_date" type="date" required :min="inputSource?.source_start" :max="inputSource?.source_cutoff" /></label>
        <label>输入截止<input v-model="draft.end_date" type="date" required :min="draft.start_date" :max="inputSource?.source_cutoff" /></label>
        <label v-if="!isMarket">树数量<input v-model.number="draft.n_estimators" type="number" required min="1" max="100" /></label>
        <label v-if="!isMarket">树深度<input v-model.number="draft.max_depth" type="number" required min="1" max="6" /></label>
        <button class="btn btn-primary" :disabled="actionBusy || !canCreate">{{ actionBusy ? '处理中…' : '提交工程任务' }}</button>
      </form>
      <div v-if="isMarket" class="task-market-policy">
        <h3>冻结完整沪深 A 股池 · 不含北交所</h3>
        <p v-if="marketCapability?.source">已入库 {{ marketCapability.source.pool_count }} 只 · {{ marketCapability.source.source_start }} → {{ marketCapability.source.source_cutoff }}</p>
        <p>股票池不随日期资格变化；上市前、预热、已证实停牌和标签未成熟会单独统计。发现未解释缺口或成熟特征异常即失败，不缩池、不补造数据。</p>
        <p>预测第 5 个市场交易日收益。探针 1 次 × 3 任务、10 个噪声列、各最多 30 轮；最终回归最多 20 轮。CPU 2 线程，需空闲内存 ≥8 GiB、临时磁盘 ≥16 GiB；训练树上限 6 GiB / 60 分钟。</p>
        <p class="task-hash" v-if="marketCapability?.source">池摘要：{{ marketCapability.source.pool_identity }}</p>
      </div>
    </section>
    <div class="task-workspace">
      <section class="card task-section task-list">
        <div class="task-title"><h2>任务队列</h2><span class="muted">活动页面每 5 秒更新</span></div>
        <p v-if="listError" role="alert">{{ listError }}</p><p v-if="listLoading" role="status">正在读取任务…</p>
        <div class="task-table"><table><thead><tr><th>股票 / 编号</th><th>状态</th><th>提交时间（北京时间）</th><th>操作</th></tr></thead><tbody>
          <tr v-for="job in jobs" :key="job.id" :class="{ selected: detail.selectedId === job.id }">
            <td><strong>{{ taskTitle(job) }}</strong><code>{{ job.id.slice(0, 12) }}</code></td>
            <td><span class="task-state" :data-state="job.state">{{ taskStatus(job) }}</span></td>
            <td>{{ time(job.created_at) }}</td><td><button class="btn btn-sm" @click="control.select(job.id)">查看</button></td>
          </tr>
        </tbody></table></div>
        <p v-if="!jobs.length && !listLoading && !listError" class="muted">暂无任务。配置好特征库和交易日历后，可在上方提交首个工程任务。</p>
        <div class="task-actions"><button class="btn btn-sm" :disabled="!pageBefore" @click="loadList(null)">返回最新</button><button class="btn btn-sm" :disabled="!nextCursor" @click="loadList(nextCursor)">下一页</button></div>
      </section>
      <section class="card task-section task-detail" aria-live="polite">
        <h2>任务详情</h2><p v-if="!detail.selectedId" class="muted">选择左侧任务，查看数据绑定、执行阶段和事件。</p>
        <p v-if="detail.error" role="alert">{{ detail.error }}</p><p v-if="detail.loading && !detail.job" role="status">正在读取详情…</p>
        <template v-if="detail.job">
          <div class="task-title"><strong>{{ taskTitle(detail.job) }}</strong><span class="task-state" :data-state="detail.job.state">{{ taskStatus(detail.job) }}</span></div>
          <p v-if="detail.job.request.kind === 'model_workflow'"><RouterLink to="/models">这是模型工作流任务，请在模型工作台查看模型和结果、审核发布。</RouterLink></p>
          <code class="task-id">{{ detail.job.id }}</code>
          <ol class="task-stages"><li v-for="stage in stages" :key="stage" :class="{ active: detail.job.stage === stage }">{{ stageText(stage) }}</li></ol>
          <p v-if="formatTaskProgress(detail.job.progress)" role="status">{{ formatTaskProgress(detail.job.progress) }}<span class="muted"> · 服务端已确认计数</span></p>
          <p v-if="failureLabels[detail.job.failure_code]" role="alert">{{ failureLabels[detail.job.failure_code] }}</p>
          <dl><dt>用途</dt><dd>{{ isFeatureJob ? '特征暂存 · 不启用、不训练' : '工程候选 · 未发布' }}</dd><dt>输入范围</dt><dd>{{ isFeatureJob ? detail.job.binding.source_start : detail.job.request.start_date }} → {{ isFeatureJob ? detail.job.binding.source_cutoff : detail.job.request.end_date }}</dd>
            <dt>特征构建</dt><dd class="task-hash">{{ isFeatureJob ? detail.job.id : detail.job.binding.build_id }}</dd><dt>源摘要</dt><dd class="task-hash">{{ detail.job.candidate?.source_sha256 || detail.job.binding.source_sha256 || '由worker快照后封存' }}</dd>
            <dt>最近心跳</dt><dd>{{ time(detail.job.heartbeat_at) }}</dd><dt v-if="detail.job.failure_code">失败码</dt><dd v-if="detail.job.failure_code">{{ detail.job.failure_code }}</dd></dl>
          <p v-if="detail.job.state === 'QUEUED'" class="muted">等待独立 worker 领取；入队不代表训练已开始。</p>
          <p v-if="detail.job.state === 'CANCEL_REQUESTED'" class="muted">取消已请求，等待 worker 确认执行进程退出。</p>
          <p v-if="detail.job.state === 'INTERRUPTED'" class="task-alert">任务已中断，不会自动重训。请核查执行槽和原 worker。</p>
          <div v-if="detail.job.candidate" class="task-candidate"><h3>{{ isFeatureJob ? '特征暂存已保存，尚未启用' : '候选完整性已验证' }}</h3><p>{{ isFeatureJob ? '来源快照、暂存元数据与报告已核对；当前特征库不变，未训练模型，也不代表模型就绪。' : '模型已保存并重加载校验；这不代表策略效果合格，也不代表已发布。' }}</p><p v-if="isFeatureJob">{{ detail.job.candidate.stock_count }}只来源股票 · {{ detail.job.candidate.row_count }}行特征。上方准备池只数不是全局构建只数。</p><code>{{ detail.job.candidate.manifest_sha256 }}</code></div>
          <dl v-if="detail.job.candidate?.coverage"><dt>固定池</dt><dd>{{ detail.job.candidate.coverage.pool_count }} 只 · {{ detail.job.candidate.coverage.zero_eligible_stocks }} 只无合格样本</dd>
            <dt>合格样本</dt><dd>{{ detail.job.candidate.coverage.eligible_rows }} 行 · 净化排除 {{ detail.job.candidate.coverage.purged_rows }} 行</dd>
            <dt>分区行数</dt><dd>训练 {{ detail.job.candidate.coverage.splits.train }} / 验证 {{ detail.job.candidate.coverage.splits.val }} / 测试 {{ detail.job.candidate.coverage.splits.test }}</dd>
            <dt>峰值内存</dt><dd>{{ (detail.job.candidate.resources.peak_rss_bytes / 1024**3).toFixed(2) }} GiB</dd></dl>
          <dl v-if="detail.job.candidate?.metrics"><template v-for="(value,key) in detail.job.candidate.metrics" :key="key"><dt>{{ key }}</dt><dd>{{ Number(value).toFixed(4) }}</dd></template></dl>
          <div class="task-actions"><button class="btn btn-sm" @click="control.refresh">刷新详情 / 后续事件</button><button v-if="canCancel" class="btn btn-sm" :disabled="actionBusy" @click="cancelJob">取消任务</button></div>
          <h3 class="task-events-title">执行事件</h3><ol class="task-events"><li v-for="event in detail.events" :key="event.id"><time>{{ time(event.created_at) }}</time><span>{{ formatTaskProgress(event.detail) || stageText(event.event) || event.event }}</span></li></ol>
        </template>
      </section>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { authState } from '../../stores/auth.js'
import { trainingApi } from '../../api/training.js'
import { createTaskController, taskTitle, taskStatus, stageLabels, buildTrainingRequest, formatTaskProgress, failureLabels } from '../../utils/trainingTasks.js'
const caps = ref(null), error = ref(''), notice = ref(''), jobs = ref([]), nextCursor = ref(null), pageBefore = ref(null), listLoading = ref(false), listError = ref(''), actionBusy = ref(false)
const draft = reactive({ kind: 'xgboost_single', ts_code: '000001.SZ', start_date: '', end_date: '', n_estimators: 20, max_depth: 3 })
const detail = reactive({})
const route=useRoute()
const isFeatureJob=computed(()=>detail.job?.request.kind==='feature_build')
const stages = computed(() => isFeatureJob.value ? ['PREFLIGHT','INPUT','VERIFY','SAVE'] : detail.job?.request.kind === 'lightgbm_all_market' ? ['PREFLIGHT', 'INPUT', 'PROBE', 'TRAIN', 'VERIFY', 'SAVE'] : ['PREFLIGHT', 'INPUT', 'TRAIN', 'VERIFY', 'SAVE'])
const stageText=stage=>(isFeatureJob.value&&({INPUT:'构建特征',VERIFY:'核对暂存证据',SAVE:'保存暂存报告',SUCCEEDED:'特征暂存完成 · 未启用'})[stage])||stageLabels[stage]
const isMarket = computed(() => draft.kind === 'lightgbm_all_market')
const marketCapability = computed(() => caps.value?.kinds.find(k => k.kind === 'lightgbm_all_market'))
const inputSource = computed(() => isMarket.value ? marketCapability.value?.source : caps.value?.source)
const enabled = () => authState.mode === 'protected' && authState.user?.role === 'admin'
const visible = () => !document.hidden
const control = createTaskController(trainingApi, detail, { visible, enabled })
const canCreate = computed(() => caps.value?.kinds.some(k => k.kind === draft.kind && k.enabled))
const canCancel = computed(() => ['QUEUED', 'RUNNING'].includes(detail.job?.state))
let active = true, listRun = 0, listRequest = null, listTimer = null, submitBody = '', submitKey = ''
const time = value => value ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'
async function loadCaps() {
  try { const data = await trainingApi.capabilities(); if (!active || !enabled()) return; caps.value = data; error.value = ''; draft.start_date ||= data.source.source_start; draft.end_date ||= data.source.source_cutoff }
  catch (e) { if (active) { caps.value = null; error.value = e.message } }
}
async function loadList(before = pageBefore.value) {
  if (!active || !visible() || !enabled()) return
  clearTimeout(listTimer); listRequest?.abort(); listRequest = new AbortController()
  const run = ++listRun; pageBefore.value = before; listLoading.value = true
  try { const data = await trainingApi.list({ before, signal: listRequest.signal }); if (active && run === listRun && enabled()) { jobs.value = data.items; nextCursor.value = data.next_cursor; listError.value = '' } }
  catch (e) { if (active && run === listRun && e.name !== 'AbortError') listError.value = e.message }
  finally { if (active && run === listRun) { listLoading.value = false; if (visible() && enabled()) listTimer = setTimeout(() => loadList(), 5000) } }
}
async function createJob() {
  if (!canCreate.value || actionBusy.value) return
  actionBusy.value = true; error.value = ''; notice.value = ''
  const request = buildTrainingRequest(draft)
  const body = JSON.stringify(request)
  if (body !== submitBody || !submitKey) { submitBody = body; submitKey = crypto.randomUUID().replaceAll('-', '') }
  try { const job = await trainingApi.create(request, submitKey); if (!active || !enabled()) return; notice.value = '任务已持久化入队，等待 worker；尚未发布任何模型。'; submitKey = ''; await Promise.all([loadList(null), control.select(job.id)]) }
  catch (e) { if (active) error.value = e.message }
  finally { actionBusy.value = false }
}
async function cancelJob() {
  if (!canCancel.value || !window.confirm('请求取消此任务？正在执行时，需要等待 worker 确认进程退出。')) return
  actionBusy.value = true; error.value = ''
  try { await control.cancel(); notice.value = '取消请求已处理，请以服务端任务状态为准。'; await loadList() }
  catch (e) { if (active) error.value = e.message }
  finally { actionBusy.value = false }
}
function refreshPage() { return Promise.all([loadCaps(), loadList()]) }
function visibility() { if (!visible()) { control.pause(); clearTimeout(listTimer); listRun++; listRequest?.abort(); listLoading.value = false } else { control.refresh(); loadList() } }
function stop() { active = false; listRun++; clearTimeout(listTimer); listRequest?.abort(); control.dispose() }
watch(() => authState.user?.id, stop, { flush: 'sync' })
watch(()=>route.query.task,id=>{if(enabled()&&typeof id==='string'&&/^[0-9a-f]{32}$/.test(id))control.select(id)},{immediate:true})
onMounted(() => { refreshPage(); document.addEventListener('visibilitychange', visibility) })
onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', visibility) })
</script>
<style scoped>
.task-page { display: grid; gap: 20px; min-width: 0; }.task-heading { display: flex; gap: 24px; align-items: start; justify-content: space-between; padding: 24px; }.task-heading h1 { margin: 6px 0 10px; }.task-section { padding: 24px; min-width: 0; }.task-section > p { margin: 10px 0; }.task-form { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 20px; align-items: end; }.task-form label { display: grid; gap: 8px; min-width: 0; }.task-form input,.task-form select { width: 100%; min-width: 0; min-height: 40px; }.task-workspace { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(320px, 1fr); gap: 20px; min-width: 0; }.task-title { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }.task-table { overflow-x: auto; }table { width: 100%; border-collapse: collapse; }th,td { text-align: left; padding: 12px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }td code { display: block; color: var(--text-3); font-size: 11px; }.selected { background: var(--accent-soft); }.task-state { border: 1px solid var(--border-strong); border-radius: 5px; padding: 3px 7px; font-size: 12px; }.task-state[data-state=RUNNING] { border-color: var(--accent); color: var(--accent); }.task-state[data-state=FAILED],.task-state[data-state=INTERRUPTED] { color: var(--serious); }.task-state[data-state=SUCCEEDED] { color: var(--good); }.task-alert,[role=alert] { color: var(--serious); }.task-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }.task-id,.task-hash,.task-candidate code { overflow-wrap: anywhere; font-size: 11px; color: var(--text-3); }.task-stages { list-style: none; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 5px; margin: 20px 0; }.task-stages li { border-top: 3px solid var(--border-strong); padding-top: 8px; font-size: 11px; color: var(--text-3); }.task-stages .active { border-color: var(--accent); color: var(--accent); }dl { display: grid; grid-template-columns: 86px minmax(0, 1fr); gap: 10px; margin-bottom: 16px; }dt { color: var(--text-3); }.task-candidate { background: var(--accent-soft); padding: 14px; border-radius: 6px; }.task-candidate p { margin: 8px 0; }.task-events-title { margin-top: 24px; }.task-events { list-style: none; margin-top: 10px; }.task-events li { display: grid; grid-template-columns: minmax(0, 1fr) 1fr; gap: 12px; border-left: 2px solid var(--border-strong); padding: 8px 0 8px 14px; font-size: 12px; }.task-events time { color: var(--text-3); }@media(max-width: 1100px) { .task-workspace { grid-template-columns: minmax(0, 1fr); } }@media(max-width: 640px) { .task-form { grid-template-columns: minmax(0, 1fr); }.task-heading { flex-direction: column; }.task-section,.task-heading { padding: 16px; } }
</style>
<style scoped>
.task-market-policy { margin-top: 18px; padding: 16px; border-left: 3px solid var(--accent); background: var(--accent-soft); }
.task-market-policy p { margin-top: 10px; line-height: 1.7; }
.task-stages { grid-template-columns: repeat(auto-fit, minmax(60px, 1fr)); }
</style>
