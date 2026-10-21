<template>
  <div>
    <h2>全市场缺口补采</h2>
    <p class="muted">沪深 A 股 · 不含北交所 · 已有数据跳过 · 停牌及上市前不补造行情</p>
    <p v-if="!enabled" role="alert">请启用受保护模式并使用管理员账号登录。此页面不提供模拟采集。</p>
    <template v-else>
      <section class="card section">
        <div class="heading"><h3>提交日期范围</h3><button class="btn btn-sm" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新状态' }}</button></div>
        <form class="collection-form" @submit.prevent="submit">
          <div class="field"><label for="collect-start">开始日期</label><input id="collect-start" v-model="form.start" type="date" min="2023-01-01" :max="caps?.completed_date" required /></div>
          <div class="field"><label for="collect-end">结束日期</label><input id="collect-end" v-model="form.end" type="date" min="2023-01-01" :max="caps?.completed_date" required /></div>
          <button class="btn btn-primary" :disabled="busy || !caps">{{ busy ? '提交中…' : '提交缺口补采' }}</button>
        </form>
        <p class="muted">提交只会入队，不代表采集完成。后台先检查数据库，再补齐缺失行情及每日基本指标；关闭网页不影响 worker。</p>
        <p v-if="caps" class="runtime">采集 worker：<strong>{{ workerLabel(caps.worker) }}</strong> · 夜间计划 {{ caps.scheduler.run_at }}（上海时间） · 可提交至 {{ caps.completed_date }}</p>
        <p v-if="caps && caps.worker !== 'RUNNING'" role="status">worker 未确认运行，任务可以入队，但不会由网页启动采集进程。请管理员检查独立 worker。</p>
        <p v-if="caps?.scheduler.blocked_error" role="alert">夜间调度受阻：{{ caps.scheduler.blocked_error }}。网页提交不会清除原调度错误。</p>
        <p class="muted" v-if="caps">进程存活不代表已启用夜间参数；需按操作说明启动 --nightly worker。当前补采使用原 MySQL 缺口入库管线，不切换旧 CLI 的输出模式。</p>
      </section>
      <p v-if="error" role="alert" class="error">{{ error }}（列表可能尚未刷新，不代表任务完成或数据为空）</p>
      <p v-if="notice" role="status">{{ notice }}</p>
      <div v-if="summary.tasks" class="grid summary-grid section">
        <StatCard label="待处理网页请求" :value="requestPending" />
        <StatCard label="等待采集的交易日" :value="summary.tasks?.QUEUED || 0" />
        <StatCard label="正在采集" :value="summary.tasks?.RUNNING || 0" />
        <StatCard label="已完成交易日" :value="summary.tasks?.SUCCEEDED || 0" tone="good" />
        <StatCard label="部分完成 / 失败" :value="(summary.tasks?.PARTIAL || 0) + (summary.tasks?.FAILED || 0)" tone="warning" />
      </div>
      <section class="card section">
        <h3>网页提交记录</h3>
        <p class="muted">“已生成日任务”仅表示规划和入队结束，实际完成情况看下方交易日任务。“请求失败”可核查原因后重新提交原日期范围。</p>
        <DataTable :columns="requestColumns" :rows="requests.items" row-key="id">
          <template #cell-status="{ row }">{{ requestLabel(row.status) }}</template>
          <template #cell-act="{ row }"><button class="btn btn-sm" @click="selectRequest(row.id)">查看日任务</button></template>
        </DataTable>
        <div class="pager"><button class="btn btn-sm" :disabled="loading || requestOffset === 0" @click="moveRequests(-20)">上一页</button><span>{{ requestOffset + 1 }} / 共 {{ requests.total }} 条</span><button class="btn btn-sm" :disabled="loading || requestOffset + 20 >= requests.total" @click="moveRequests(20)">下一页</button></div>
      </section>
      <section class="card section">
        <div class="heading"><h3>真实交易日任务</h3><button v-if="selectedRequest" class="btn btn-sm" @click="selectRequest('')">查看全部日任务</button></div>
        <p class="muted">{{ selectedRequest ? `当前网页请求：${selectedRequest}` : '显示原缺口队列中的全部日任务，包含历史及夜间任务' }}。失败／部分完成支持单条重试；配额等待不强制绕过。</p>
        <DataTable :columns="taskColumns" :rows="tasks.items" row-key="task_id">
          <template #cell-inserted="{ row }">{{ row.result.inserted ?? row.progress.inserted ?? '—' }}</template>
          <template #cell-progress="{ row }">{{ row.progress.completed_requests ?? '—' }} / {{ row.progress.total_requests ?? '—' }}</template>
          <template #cell-act="{ row }"><button v-if="['FAILED','PARTIAL'].includes(row.status)" class="btn btn-sm" :disabled="busy" @click="retry(row)">重试此任务</button><span v-else>—</span></template>
        </DataTable>
        <div class="pager"><button class="btn btn-sm" :disabled="loading || taskOffset === 0" @click="moveTasks(-20)">上一页</button><span>{{ taskOffset + 1 }} / 共 {{ tasks.total }} 条</span><button class="btn btn-sm" :disabled="loading || taskOffset + 20 >= tasks.total" @click="moveTasks(20)">下一页</button></div>
      </section>
      <section class="card section">
        <div class="heading"><h3>真实数据预览（最多20行）</h3><label>数据表 <select v-model="table" @change="loadPreview"><option v-for="name in tables" :key="name" :value="name">{{ name }}</option></select></label></div>
        <p v-if="previewError" role="alert" class="error">{{ previewError }}</p>
        <p v-else-if="previewLoading" role="status">读取中…</p>
        <DataTable v-else :columns="previewColumns" :rows="preview" :page-size="10" />
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive, ref, onMounted, onBeforeUnmount, watch } from 'vue'
import DataTable from '../components/DataTable.vue'
import StatCard from '../components/StatCard.vue'
import { collectorApi as api } from '../api/collector.js'
import { authState } from '../stores/auth.js'

const enabled = computed(() => authState.mode === 'protected' && authState.user?.role === 'admin' && !authState.user.must_change_password)
const caps = ref(null), loading = ref(false), busy = ref(false), error = ref(''), notice = ref('')
const form = reactive({ start: '2023-01-01', end: '' }), summary = ref({}), requests = reactive({ items: [], total: 0 }), tasks = reactive({ items: [], total: 0 })
const requestOffset = ref(0), taskOffset = ref(0), selectedRequest = ref('')
const tables = ['stock_basic', 'stock_daily', 'stock_daily_basic'], table = ref('stock_daily'), preview = ref([]), previewError = ref(''), previewLoading = ref(false)
const requestPending = computed(() => ['QUEUED','PLANNING','WAITING_RETRY'].reduce((n,k) => n + (summary.value.requests?.[k] || 0), 0))
const previewColumns = computed(() => Object.keys(preview.value[0] || {}).map(key => ({ key, label: key })))
const requestColumns = [{key:'id',label:'请求编号',ellipsis:true},{key:'start_date',label:'开始'},{key:'end_date',label:'结束'},{key:'status',label:'规划状态'},{key:'task_count',label:'已关联日任务'},{key:'error_kind',label:'错误原因'},{key:'act',label:'操作'}]
const taskColumns = [{key:'trade_date',label:'交易日'},{key:'status',label:'采集状态',type:'status'},{key:'phase',label:'阶段'},{key:'progress',label:'已完成 / 总请求'},{key:'inserted',label:'已入库行数'},{key:'error_kind',label:'错误原因'},{key:'retry_not_before',label:'配额重试时间'},{key:'act',label:'操作'}]
const requestLabel = s => ({QUEUED:'等待规划',PLANNING:'规划 / 入队中',WAITING_RETRY:'配额 / 网络等待',SUBMITTED:'已生成日任务',FAILED:'请求失败'}[s] || s)
const workerLabel = s => ({RUNNING:'进程存活',STOPPED:'未启动',STALE_LOCK:'进程已退出，锁待核查',UNKNOWN:'状态未知'}[s] || s)
let active = true, generation = 0, identityGeneration = 0, previewGeneration = 0, controller, previewController, timer
let submitKey = '', submitBody = ''
const retryKeys = new Map()
function current(run) { return active && enabled.value && run === generation }
function currentAction(run) { return active && enabled.value && run === identityGeneration }
async function refresh() {
  if (!enabled.value || !active || loading.value) return
  const run = ++generation; controller?.abort(); controller = new AbortController(); const options = {signal:controller.signal}
  loading.value = true; error.value = ''
  try {
    const values = await Promise.all([api.capabilities(options),api.summary(options),api.requests({...options,offset:requestOffset.value}),api.tasks({...options,offset:taskOffset.value,request_id:selectedRequest.value || undefined})])
    if (!current(run)) return
    caps.value=values[0]; summary.value=values[1]; Object.assign(requests,values[2]); Object.assign(tasks,values[3]); form.end ||= values[0].completed_date
  } catch(e) { if(current(run) && e.name!=='AbortError') error.value=e.message }
  finally { if(active && run===generation) loading.value=false }
}
async function submit() {
  if(busy.value || !enabled.value)return
  const run=identityGeneration; busy.value=true; error.value=''; notice.value=''
  try {
    const body=JSON.stringify(form)
    if(body!==submitBody || !submitKey){submitBody=body;submitKey=crypto.randomUUID().replaceAll('-','')}
    const result=await api.submit({...form},submitKey)
    if(!currentAction(run))return
    submitKey=''; notice.value=`请求 ${result.id} 已入队，尚未代表采集完成。`; requestOffset.value=0; await refresh()
  } catch(e){if(currentAction(run))error.value=e.message}
  finally{if(currentAction(run))busy.value=false}
}
async function retry(row) {
  if(busy.value || !enabled.value)return
  const run=identityGeneration; busy.value=true; error.value='';notice.value=''
  const intent=`${row.task_id}:${row.attempt_no}`
  if(!retryKeys.has(intent))retryKeys.set(intent,crypto.randomUUID().replaceAll('-',''))
  try {
    await api.retry(row.task_id,retryKeys.get(intent))
    if(!currentAction(run))return
    notice.value='重试请求已入队，实际状态以 worker 执行后的日任务为准。';await refresh()
  }catch(e){if(currentAction(run))error.value=e.message}
  finally{if(currentAction(run))busy.value=false}
}
function selectRequest(id){selectedRequest.value=id;taskOffset.value=0;restartRefresh()}
function moveRequests(n){requestOffset.value+=n;restartRefresh()}
function moveTasks(n){taskOffset.value+=n;restartRefresh()}
function restartRefresh(){generation++;controller?.abort();loading.value=false;refresh()}
async function loadPreview(){
  const run=++previewGeneration;previewController?.abort();preview.value=[];previewError.value=''
  if(!active || !enabled.value)return
  previewController=new AbortController();previewLoading.value=true
  try{const result=await api.preview(table.value,{signal:previewController.signal});if(active && enabled.value && run===previewGeneration)preview.value=result.items}
  catch(e){if(active && enabled.value && run===previewGeneration && e.name!=='AbortError')previewError.value=e.message}
  finally{if(active && run===previewGeneration)previewLoading.value=false}
}
function reset(){identityGeneration++;generation++;previewGeneration++;controller?.abort();previewController?.abort();caps.value=null;summary.value={};requests.items=[];tasks.items=[];preview.value=[];requests.total=0;tasks.total=0;loading.value=false;busy.value=false;error.value='';notice.value='';selectedRequest.value='';requestOffset.value=0;taskOffset.value=0;submitKey='';submitBody='';retryKeys.clear()}
watch(()=>[authState.mode,authState.user?.id,authState.user?.role,authState.user?.must_change_password],()=>{reset();refresh();loadPreview()},{flush:'sync'})
onMounted(()=>{refresh();loadPreview();timer=setInterval(()=>{if(!document.hidden && !busy.value)refresh()},15000)})
onBeforeUnmount(()=>{active=false;reset();clearInterval(timer)})
</script>

<style scoped>
.section{margin-top:18px}.heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.heading h3{margin:0 0 12px}.collection-form{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap}.field{min-width:180px}.runtime{padding:12px 0;border-top:1px solid var(--border)}.summary-grid{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}.pager{display:flex;align-items:center;justify-content:flex-end;gap:14px;margin-top:12px}.error{color:var(--critical)}
</style>
