<template>
  <section class="card prediction-panel" aria-labelledby="user-predict-title">
    <header><div><p class="eyebrow">股票池 / 已上线模型 / 预测排行</p><h2 id="user-predict-title">选择股票池，查看模型预测</h2></div><button class="btn btn-sm" :disabled="busy" @click="catalog">刷新可用模型</button></header>
    <p class="muted">相同条件优先读取已有结果或共用任务；没有则排队预测，不重新训练模型。</p>
    <p v-if="!allowed()" role="status">请登录后使用已上线模型；不提供训练功能。</p>
    <p v-else-if="!models.length&&!busy&&!error" role="status">暂时没有上线的预测模型。请联系管理员在模型工作台审核上线；系统不会用演示结果替代。</p>
    <p v-if="error" role="alert">{{error}}</p>
    <form @submit.prevent="submit"><label>股票池<select aria-label="股票池" v-model="pool" :disabled="busy||!models.length" required><option value="">请选择固定股票池</option><option v-for="p in pools" :key="p.pool_version" :value="p.pool_version">{{p.display_name}} · {{p.stock_count}}只</option></select></label><label>预测模型<select aria-label="预测模型" v-model="model" :disabled="busy||!pool" required><option value="">请选择支持此池的模型</option><option v-for="m in compatible" :key="m.id" :value="m.id">{{m.algorithm}} · {{m.title}} · {{m.id.slice(0,8)}}</option></select></label><button class="btn btn-primary" :disabled="busy||!model||!pool">{{busy?'正在处理…':'开始预测'}}</button></form>
    <p class="muted">只展示模型支持的完整股票池。沪深300、中证1000或行业池需有固定版本和兼容模型；不自动换池、缩池或补造数据。</p>
    <div v-if="job" class="prediction-status" aria-live="polite"><strong>{{labels[job.state]||job.state}}</strong><span>信号日期 {{job.signal_date}} · 模型 {{job.model_id.slice(0,12)}} · 所选池 {{job.selection.stock_count}}只</span><button class="btn btn-sm" :disabled="busy" @click="refreshJob">刷新状态</button></div>
    <p v-if="job&&['FAILED','CANCELLED','INTERRUPTED','NOT_READY'].includes(job.state)" role="alert">未得到完整结果，请联系管理员核查数据或任务。再次点击不会偷偷重训或反复重试。</p>
    <template v-if="result"><div class="result-heading"><h3>预测排行</h3><span class="muted">数据截止 {{result.report.data_cutoff}} · {{scoreLabel(result.report.score_units)}}</span></div><p v-if="result.report.sample_relation!=='post_model_selection'" class="research-note">这是历史研究结果，不等于独立样本外验证，也不是实时行情预测。</p><p class="muted">模型按原冻结池 {{job.inference_pool_count}}只计算，这里仅筛选所选池。停牌、上市时间或数据资格可能导致部分股票无分数；原模型排名不因筛选而重新归一化。</p>
      <ChartBox v-if="result.rows.length" :option="chart" :height="260" /><DataTable :columns="columns" :rows="result.rows" row-key="ts_code" :page-size="0" empty-text="所选池在该日期没有合格分数，不补造结果" /><div class="page-actions"><button v-if="after" class="btn btn-sm" :disabled="busy" @click="scores(0)">返回首屏</button><button v-if="result.next_cursor!=null" class="btn btn-sm" :disabled="busy" @click="scores(result.next_cursor)">下一页分数</button></div><p class="muted">分数只代表模型输出，不保证收益；不同量纲模型不可直接比较。结果直接向有权请求的用户展示，不代表投资建议。</p>
    </template>
  </section>
</template>
<script setup>
import {computed,ref,watch,onMounted,onBeforeUnmount} from 'vue'
import {authState} from '../stores/auth.js'
import {userPredictionApi as api} from '../api/userPredictions.js'
import {scoreLabel} from '../utils/workflows.js'
import DataTable from './DataTable.vue'
import ChartBox from './ChartBox.vue'
const models=ref([]),pool=ref(''),model=ref(''),job=ref(null),result=ref(null),error=ref(''),busy=ref(false),after=ref(0)
const pools=computed(()=>[...new Map(models.value.flatMap(m=>m.pools).map(p=>[p.pool_version,p])).values()])
const compatible=computed(()=>models.value.filter(m=>m.pools.some(p=>p.pool_version===pool.value)))
const labels={QUEUED:'等待后台计算',RUNNING:'正在预测',CANCEL_REQUESTED:'后台正在停止任务',SUCCEEDED:'预测结果已保存',FAILED:'预测失败',CANCELLED:'任务已取消',INTERRUPTED:'执行中断',NOT_READY:'结果未就绪'}
const pending=()=>job.value&&['QUEUED','RUNNING','CANCEL_REQUESTED'].includes(job.value.state)
const columns=[{key:'ts_code',label:'股票代码'},{key:'trade_date',label:'信号日期'},{key:'score',label:'预测分数',type:'number',format:v=>Number(v).toFixed(6)},{key:'rank',label:'原模型排名'}]
const chart=computed(()=>({title:{text:'当前页前10只 · 模型分数',textStyle:{fontSize:13,color:'#9099ab'}},tooltip:{trigger:'axis'},grid:{left:85,right:30,top:40,bottom:24},xAxis:{type:'value'},yAxis:{type:'category',inverse:true,data:(result.value?.rows||[]).slice(0,10).map(r=>r.ts_code)},series:[{type:'bar',data:(result.value?.rows||[]).slice(0,10).map(r=>r.score),itemStyle:{color:'#4f8fca'}}]}))
let live=true,generation=0,request,timer
const allowed=()=>live&&authState.mode==='protected'&&!!authState.user&&!authState.user.must_change_password
const current=run=>allowed()&&run===generation
function pause(){generation++;request?.abort();clearTimeout(timer);busy.value=false}
function clear(){pause();job.value=null;result.value=null;error.value='';after.value=0}
function begin(){pause();request=new AbortController();busy.value=true;error.value='';return generation}
async function loadScores(run,cursor=0){const data=await api.scores(job.value.id,{after:cursor,signal:request.signal});if(current(run)){result.value=data;after.value=cursor}}
function schedule(run){if(current(run)&&pending()&&!document.hidden)timer=setTimeout(refreshJob,3000)}
async function catalog(){if(!allowed())return;const run=begin();job.value=null;result.value=null
  try{const value=await api.catalog({signal:request.signal});if(current(run)){models.value=value.models;if(!models.value.some(m=>m.id===model.value)){pool.value='';model.value=''}}}catch(e){if(current(run)&&e.name!=='AbortError'){models.value=[];error.value=e.message}}finally{if(current(run))busy.value=false}}
async function submit(){if(busy.value||!allowed()||!model.value||!pool.value)return;const run=begin();result.value=null;job.value=null
  try{const value=await api.submit({model_id:model.value,pool_version:pool.value},{signal:request.signal});if(!current(run))return;job.value=value;if(value.result_ready)await loadScores(run);schedule(run)}catch(e){if(current(run)&&e.name!=='AbortError')error.value=e.message}finally{if(current(run))busy.value=false}}
async function refreshJob(){if(!allowed()||!job.value)return;const id=job.value.id,run=begin()
  try{const value=await api.status(id,{signal:request.signal});if(!current(run))return;job.value=value;if(value.result_ready)await loadScores(run);schedule(run)}catch(e){if(current(run)&&e.name!=='AbortError'){result.value=null;error.value=e.message}}finally{if(current(run))busy.value=false}}
async function scores(cursor){if(!allowed()||!job.value||busy.value)return;const run=begin();try{await loadScores(run,cursor)}catch(e){if(current(run)&&e.name!=='AbortError'){result.value=null;error.value=e.message}}finally{if(current(run))busy.value=false}}
watch(pool,()=>{clear();if(!compatible.value.some(m=>m.id===model.value))model.value=''},{flush:'sync'})
watch(model,clear,{flush:'sync'})
watch(()=>[authState.mode,authState.user?.id,authState.user?.role,authState.user?.must_change_password],()=>{clear();models.value=[];pool.value='';model.value='';catalog()},{flush:'sync'})
function visibility(){if(document.hidden)pause();else if(job.value)refreshJob()}
onMounted(()=>{catalog();document.addEventListener('visibilitychange',visibility)})
onBeforeUnmount(()=>{live=false;clear();document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.prediction-panel{padding:26px;display:grid;gap:16px;border-top:3px solid var(--accent,#4f8fca);min-width:0}.prediction-panel header,.prediction-status,.result-heading,.page-actions{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap}.prediction-panel h2,.prediction-panel h3,.prediction-panel p{margin:0}.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--text-3);margin-bottom:8px!important}.prediction-panel form{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:16px;align-items:end}.prediction-panel label{display:grid;gap:8px}.prediction-panel select{width:100%;min-height:42px;min-width:0}.muted{color:var(--text-3);font-size:13px;line-height:1.75}.prediction-status{border-block:1px solid var(--border);padding:16px 0}.prediction-status span{font-size:13px}.research-note{border-left:3px solid var(--warning,#c59a47);padding:10px 14px;line-height:1.7}[role=alert]{color:var(--serious);line-height:1.7}@media(max-width:720px){.prediction-panel{padding:18px}.prediction-panel form{grid-template-columns:1fr}}
.prediction-panel{grid-template-columns:minmax(0,1fr)}
.prediction-panel>* , .prediction-panel label{min-width:0}
.prediction-panel header>div,.prediction-status span,.result-heading span{overflow-wrap:anywhere;min-width:0}
@media(max-width:720px){.prediction-panel form{grid-template-columns:minmax(0,1fr)}}
</style>
