<template>
  <section class="card preparation">
    <header><h2>固定池数据准备任务</h2><button type="button" class="btn" :disabled="listLoading || busy" @click="refresh(0)">刷新准备任务</button></header>
    <p>显式提交后由采集worker检查并补齐所选池的缺失行情和每日基本指标，不重新核对全部旧价格。核心行情补齐不等于模型就绪：此操作不构建特征、不训练、不发布。</p>
    <p v-if="poolVersion"><code>{{ poolVersion }}</code> · {{ start }} → {{ end }}</p><p v-else>先选择已保存的固定版本及日期范围。</p>
    <button type="button" class="btn btn-primary" :disabled="busy || !poolVersion || !start || !end" @click="submit">补齐当前固定池缺失数据</button>
    <p v-if="writeError || listError || detailError" role="alert">{{ writeError || listError || detailError }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="listLoading" role="status">读取任务列表…</p><p v-else-if="!requests.length && !listError">暂无准备任务。选择池或日期不会自动提交。</p>
    <div class="request-list"><button type="button" v-for="row in requests" :key="row.id" class="request" :disabled="busy" @click="choose(row.id)"><strong>{{ row.pool_version.slice(-8) }} · 固定{{ row.registry_stock_count }}只 · {{ row.start_date }} → {{ row.end_date }}</strong><span>{{ state(row.data_state) }} · {{ row.id.slice(0,12) }}</span></button></div>
    <div class="pager"><button type="button" class="btn btn-sm" :disabled="busy || listLoading || offset===0" @click="refresh(offset-20)">上一页准备任务</button><span>共{{ total }}项</span><button type="button" class="btn btn-sm" :disabled="busy || listLoading || offset+20>=total" @click="refresh(offset+20)">下一页准备任务</button></div>
    <template v-if="detail">
      <h3>所选准备任务</h3><code>{{ detail.id }}</code><p><code>{{ detail.pool_version }}</code> · {{ detail.start_date }} → {{ detail.end_date }}</p>
      <button type="button" class="btn btn-sm" :disabled="busy || detailLoading" @click="loadDetail()">刷新所选任务详情</button>
      <p v-if="detail.pool_version!==poolVersion">正在查看另一固定版本的历史任务；不会随上方选择改变其成员或日期。</p>
      <p>{{ state(detail.data_state) }} · {{ detail.status }} · {{ detail.task_count }}个交易日任务。模型输入仍需独立特征与样本校验。</p>
      <button v-if="detail.data_state==='READY_CORE'" type="button" class="btn" :disabled="busy" @click="buildFeatures">构建特征暂存（不启用）</button>
      <p v-if="featureTaskId"><code>{{ featureTaskId }}</code> · <RouterLink :to="{path:'/training',query:{task:featureTaskId}}">查看特征任务</RouterLink></p>
      <p v-if="detail.error_kind" role="alert">{{ detail.error_kind }}<span v-if="detail.retry_not_before"> · 最早重试 {{ detail.retry_not_before }}</span></p>
      <button v-if="['FAILED','WAITING_RETRY'].includes(detail.status)" type="button" class="btn" :disabled="busy" @click="retryParent">重试准备规划</button>
      <p v-if="detailLoading" role="status">读取当日任务…</p>
      <div class="days"><div v-for="task in tasks" :key="task.task_id"><span>{{ task.trade_date }} · {{ task.status }} · {{ task.phase }}<template v-if="task.error_kind"> · {{ task.error_kind }}</template></span><button v-if="['FAILED','PARTIAL'].includes(task.status)" type="button" class="btn btn-sm" :disabled="busy" @click="retryTask(task.task_id)">重试当日任务</button></div></div>
      <div class="pager"><button type="button" class="btn btn-sm" :disabled="busy || detailLoading || taskOffset===0" @click="loadDetail(taskOffset-20)">上一页交易日</button><span>共{{ taskTotal }}个交易日任务</span><button type="button" class="btn btn-sm" :disabled="busy || detailLoading || taskOffset+20>=taskTotal" @click="loadDetail(taskOffset+20)">下一页交易日</button></div>
    </template>
  </section>
</template>
<script setup>
import {ref,watch,onMounted,onBeforeUnmount} from 'vue'
import {authState} from '../stores/auth.js'
import {preparationApi as api} from '../api/preparation.js'
const props=defineProps({poolVersion:{type:String,default:''},start:{type:String,default:''},end:{type:String,default:''}})
const requests=ref([]),total=ref(0),offset=ref(0),selected=ref(''),detail=ref(null),tasks=ref([]),taskOffset=ref(0),taskTotal=ref(0)
const listLoading=ref(false),detailLoading=ref(false),busy=ref(false),listError=ref(''),detailError=ref(''),writeError=ref(''),notice=ref('')
const featureTaskId=ref('')
const enabled=()=>authState.mode==='protected'&&authState.user?.role==='admin'&&!authState.user.must_change_password
const state=value=>({NOT_STARTED:'等待worker规划',RUNNING:'数据任务进行中',READY_CORE:'核心行情已补齐（非模型就绪）',NO_APPLICABLE_SESSIONS:'区间无适用交易日',NEEDS_CHECK:'存在失败或待核对任务',FAILED:'准备失败'}[value]||value)
let active=true,epoch=0,listRun=0,detailRun=0,listAbort,detailAbort,timer,submitBody='',submitKey=''
const retryKeys=new Map()
const current=e=>active&&enabled()&&e===epoch
const newKey=()=>crypto.randomUUID().replaceAll('-','')
async function refresh(next=offset.value){
  const e=epoch,r=++listRun;listAbort?.abort();listAbort=new AbortController()
  if(!current(e))return
  listLoading.value=true;listError.value=''
  try{const value=await api.list({offset:next,signal:listAbort.signal});if(current(e)&&r===listRun){requests.value=value.items;total.value=value.total;offset.value=next}}
  catch(error){if(current(e)&&r===listRun&&error.name!=='AbortError'){listError.value=error.message;requests.value=[]}}
  finally{if(current(e)&&r===listRun)listLoading.value=false}
}
async function loadDetail(next=taskOffset.value){
  const e=epoch,r=++detailRun,id=selected.value;clearTimeout(timer);detailAbort?.abort();detailAbort=new AbortController()
  if(!current(e)||!id)return
  detailLoading.value=true;detailError.value=''
  try{const [value,days]=await Promise.all([api.get(id,{signal:detailAbort.signal}),api.tasks(id,{offset:next,signal:detailAbort.signal})]);if(!current(e)||r!==detailRun||id!==selected.value)return
    detail.value=value;tasks.value=days.items;taskTotal.value=days.total;taskOffset.value=next;requests.value=requests.value.map(row=>row.id===id?value:row)}
  catch(error){if(current(e)&&r===detailRun&&error.name!=='AbortError'){detailError.value=error.message;detail.value=null;tasks.value=[]}}
  finally{if(current(e)&&r===detailRun){detailLoading.value=false;if(!document.hidden&&detail.value&&['NOT_STARTED','RUNNING'].includes(detail.value.data_state))timer=setTimeout(()=>loadDetail(),5000)}}
}
function choose(id){selected.value=id;detail.value=null;tasks.value=[];taskOffset.value=0;featureTaskId.value='';loadDetail(0)}
async function buildFeatures(){
  if(busy.value||!enabled()||detail.value?.data_state!=='READY_CORE')return
  if(!window.confirm('构建新的特征暂存？会计算2023年起的全局已入库历史，截止不早于当前特征库；可能耗时较长，不启用、不训练、不发布。'))return
  const e=epoch,id=detail.value.id,mapKey='feature:'+id;busy.value=true;writeError.value=''
  if(!retryKeys.has(mapKey))retryKeys.set(mapKey,newKey())
  try{const value=await api.featureBuild(id,retryKeys.get(mapKey));if(current(e)){retryKeys.delete(mapKey);featureTaskId.value=value.id;notice.value='特征构建已入队；由独立worker执行，完成后仅保留暂存，不会替换当前输入或训练模型。'}}
  catch(error){if(current(e))writeError.value=error.message}finally{if(current(e))busy.value=false}
}
async function submit(){
  if(busy.value||!enabled())return
  const value={pool_version:props.poolVersion,start:props.start,end:props.end}
  if(!window.confirm(`确认补齐固定池 ${value.pool_version} 在 ${value.start} 至 ${value.end} 的缺失数据？可能请求上游接口；不训练或发布。`))return
  const e=epoch;busy.value=true;writeError.value='';notice.value=''
  try{const serialized=JSON.stringify(value);if(serialized!==submitBody||!submitKey){submitBody=serialized;submitKey=newKey()}
    const result=await api.create(value,submitKey);if(!current(e))return;submitKey='';notice.value='已提交数据准备任务；采集worker未运行时将保持排队。';choose(result.id);await refresh(0)}
  catch(error){if(current(e))writeError.value=error.message}
  finally{if(current(e))busy.value=false}
}
async function retryParent(){
  if(busy.value||!enabled()||!detail.value||!window.confirm('重试所选准备任务规划？保留原股票池、日期及已有批次。'))return
  const e=epoch,id=detail.value.id;busy.value=true;writeError.value=''
  try{await api.retry(id);if(current(e)){notice.value='已提交原准备任务规划重试。';await Promise.all([refresh(),loadDetail()])}}
  catch(error){if(current(e))writeError.value=error.message}finally{if(current(e))busy.value=false}
}
async function retryTask(task){
  if(busy.value||!enabled()||!detail.value||!window.confirm('重试原批次中的这个交易日？不会重新规划全市场或改变固定池。'))return
  const e=epoch,id=detail.value.id,mapKey=id+':'+task;busy.value=true;writeError.value=''
  if(!retryKeys.has(mapKey))retryKeys.set(mapKey,newKey())
  try{await api.retryTask(id,task,retryKeys.get(mapKey));if(current(e)){retryKeys.delete(mapKey);notice.value='已提交当日任务重试，仍使用原固定批次。';await loadDetail()}}
  catch(error){if(current(e))writeError.value=error.message}finally{if(current(e))busy.value=false}
}
function reset(){epoch++;listRun++;detailRun++;listAbort?.abort();detailAbort?.abort();clearTimeout(timer);requests.value=[];selected.value='';detail.value=null;tasks.value=[];total.value=0;busy.value=false;listLoading.value=false;detailLoading.value=false;listError.value='';detailError.value='';writeError.value='';notice.value='';submitBody='';submitKey='';featureTaskId.value='';retryKeys.clear()}
function visibility(){if(document.hidden){detailRun++;detailAbort?.abort();clearTimeout(timer);detailLoading.value=false}else if(enabled()){refresh();loadDetail()}}
watch(()=>[authState.user?.id,authState.user?.role,authState.mode,authState.user?.must_change_password],()=>{reset();if(enabled())refresh(0)},{flush:'sync'})
onMounted(()=>{refresh(0);document.addEventListener('visibilitychange',visibility)})
onBeforeUnmount(()=>{active=false;reset();document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.preparation{padding:24px;display:grid;gap:14px;min-width:0}header,.pager{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}h2,h3,p{margin:0}p{line-height:1.7;color:var(--text-3)}code{overflow-wrap:anywhere;font-size:12px}.request-list,.days{display:grid;gap:8px}.request{display:grid;gap:6px;text-align:left;padding:12px;color:var(--text);background:transparent;border:1px solid var(--border);cursor:pointer}.request:hover,.request:focus-visible{border-color:var(--accent)}.days>div{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:8px;border-bottom:1px solid var(--border)}[role=alert]{color:var(--serious)}@media(max-width:640px){.preparation{padding:16px}.days>div{align-items:start;flex-direction:column}}
</style>
