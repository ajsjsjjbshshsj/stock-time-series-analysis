<template>
  <div class="workflow">
    <section class="card box"><header><div><h1>模型工作台</h1><p>训练 → 保存模型 → 预测 / 回测 → 审核发布。收益不作为功能成功或发布的门槛。</p></div><button class="btn" @click="refresh">刷新</button></header>
      <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="caps?.execution.quarantined" role="alert">执行槽已隔离。请核查原 worker 后受控恢复，不重复启动 worker。</p>
      <p v-if="caps" class="muted">真实输入：{{ caps.source.source_start }} → {{ caps.source.source_cutoff }}。只读已核验数据库，不在训练时采集新数据。输入日期可能落后于今天；16:00前不使用当日行情。</p>
      <p v-if="draft.operation === 'backtest' && caps" class="muted">本机回测上限 {{ caps.max_backtest_price_rows }} 个价格观测（固定股票数 × 区间交易日数，含执行尾部）。全市场请选择较短区间；超限会在入队前拒绝，不缩股票池。更长回测需后续扩容或流式账本。</p>
      <form class="form" @submit.prevent="submit">
        <label class="wide" v-if="draft.operation!=='import'">锁定特征版本（留空使用当前版本）<input v-model="draft.feature_build_id" maxlength="32" placeholder="启用任务完成后带入；版本变化会拒绝入队" /></label>
        <label>操作<select v-model="draft.operation"><option v-for="(name,key) in operationLabels" :key="key" :value="key">{{ name }}</option></select></label>
        <label>算法<select aria-label="算法" v-model="draft.algorithm"><option value="xgboost">XGBoost · 单股方向</option><option value="lightgbm">LightGBM · 股票池排名</option><option value="transformer">Transformer · 小范围</option></select></label>
        <template v-if="draft.operation !== 'import'">
          <label>信号 / 训练起始<input v-model="draft.start_date" type="date" required :max="draft.end_date" /></label><label>信号 / 训练截止<input v-model="draft.end_date" type="date" required :min="draft.start_date" :max="caps?.source.source_cutoff" /></label>
          <label v-if="draft.operation !== 'train'">已保存模型<select v-model="draft.model_id" required><option value="">请选择固定版本</option><option v-for="m in matchingModels" :key="m.id" :value="m.id">{{ m.title }} · {{ m.id.slice(0,8) }}</option></select></label>
          <template v-if="draft.operation === 'train'">
            <WorkflowPoolSelector class="wide" v-model="draft.pool_version" :algorithm="draft.algorithm" @ready="poolReady=$event" @members="usePoolMembers" />
            <label v-if="draft.algorithm === 'xgboost'">股票代码<input v-model="draft.ts_code" required maxlength="9" placeholder="000001.SZ" /></label>
            <template v-if="draft.algorithm !== 'transformer'"><label>树数量<input v-model.number="draft.n_estimators" type="number" min="1" :max="draft.algorithm === 'xgboost' ? 100 : 300" required /></label><label v-if="draft.algorithm === 'xgboost'">树深度<input v-model.number="draft.max_depth" type="number" min="1" max="6" required /></label></template>
            <template v-if="draft.algorithm === 'lightgbm'"><label>目标量纲<select v-model="draft.label"><option value="raw">原始收益</option><option value="daily_rank">日截面排名</option></select></label><label>早停指标<select v-model="draft.early_metric"><option value="rmse">RMSE</option><option value="rank_ic">RankIC</option></select></label><label>早停耐心<input v-model.number="draft.patience" type="number" min="1" max="50" required /></label></template>
            <template v-if="draft.algorithm === 'transformer'"><label class="wide">固定股票池 · 10–20只<textarea v-model="draft.stock_codes" required rows="3" placeholder="沪深代码，以逗号或换行分隔；不会按训练效果挑选" /></label><label>训练轮数<input v-model.number="draft.num_epochs" type="number" min="1" max="5" required /></label><label>批大小<input v-model.number="draft.batch_size" type="number" min="1" max="2" required /></label><p>保留训练集探针筛选；默认1 epoch、batch 1；GPU可用时使用 CUDA，显存与进程资源仍受限。此入口不支持全市场 Transformer。</p></template>
          </template>
          <template v-if="draft.operation === 'backtest'"><label>Top N<input v-model.number="draft.top_n" type="number" min="1" max="100" required /></label><label>初始资金<input v-model.number="draft.initial_capital" type="number" min="1" max="10000000000" required /></label><label>费用（基点）<input v-model.number="draft.fee_bps" type="number" min="0" max="100" step="0.1" required /></label><label>滑点（基点）<input v-model.number="draft.slippage_bps" type="number" min="0" max="100" step="0.1" required /></label></template>
        </template>
        <template v-else><label>原工程任务编号<input v-model="draft.import_task_id" maxlength="32" placeholder="与下方实验来源二选一" /></label><label>验证实验编号<input v-model="draft.experiment_id" maxlength="32" /></label><label>实验分支<select v-model="draft.trial"><option v-for="t in trials" :key="t">{{ t }}</option></select></label><p>只接收已有成功任务 / 封存实验编号，不接收文件路径或上传模型。实验分支仅支持 LightGBM。</p></template>
        <button class="btn btn-primary" :disabled="busy || !caps || caps.execution.quarantined || draft.operation==='train' && draft.pool_version && !poolReady">{{ busy ? '处理中…' : '提交后台任务' }}</button>
      </form>
      <p class="muted">所有模型共用单执行槽；默认上限：进程树6 GiB、60分钟。全市场构建要求空闲内存≥8 GiB、临时磁盘≥16 GiB。关闭网页不影响任务。模型与旧封存包不会覆盖；发布必须另行确认。</p>
    </section>
    <DailyPredictionPanel :models="models.items" />
    <ModelReleasePanel :models="models.items" />
    <div class="columns">
      <section class="card box"><h2>任务队列</h2><p v-if="jobs.error" role="alert">{{ jobs.error }}</p><p v-if="jobs.loading">正在读取…</p><p v-if="!jobs.items.length && !jobs.loading">暂无任务</p>
        <div class="list"><button v-for="j in jobs.items" :key="j.id" class="item" @click="taskControl.select(j.id)"><strong>{{ operationLabels[j.request.operation] || '旧工程训练' }} · {{ j.request.algorithm || j.request.kind }}</strong><span>{{ j.state === 'SUCCEEDED' ? '已完成并保存' : stateLabels[j.state] || j.state }} · {{ j.id.slice(0,12) }}</span></button></div>
        <div class="actions"><button v-if="jobs.next_cursor" class="btn btn-sm" @click="jobControl.refresh(jobs.next_cursor)">更早任务</button><button v-if="jobs.before" class="btn btn-sm" @click="jobControl.refresh(null)">最新任务</button></div>
        <template v-if="detail.job"><h3>所选任务</h3><code>{{ detail.job.id }}</code><p>{{ detail.job.state === 'SUCCEEDED' ? '完成（不代表已发布或盈利）' : stateLabels[detail.job.state] }} · {{ stageLabels[detail.job.stage] || detail.job.stage }}</p><p>{{ formatTaskProgress(detail.job.progress) }}</p><p v-if="detail.job.failure_code" role="alert">{{ failureLabels[detail.job.failure_code] || detail.job.failure_code }}</p><button v-if="detail.job.state==='QUEUED'||detail.job.state==='RUNNING'&&detail.job.request.kind!=='feature_activate'" class="btn" :disabled="busy" @click="cancel">取消任务</button><button class="btn" @click="taskControl.refresh">刷新详情</button><ol class="events"><li v-for="e in detail.events" :key="e.id">{{ stageLabels[e.event] || e.event }} {{ formatTaskProgress(e.detail) }}</li></ol></template><p v-if="detail.error" role="alert">{{ detail.error }}</p>
      </section>
      <section class="card box"><h2>已保存模型</h2><p v-if="models.error" role="alert">{{ models.error }}</p><p v-if="!models.items.length">尚无可选版本；可先训练或导入旧候选。</p>
        <div v-for="m in models.items" :key="m.id" class="model"><strong>{{ m.algorithm }} · {{ m.title }}</strong><code>{{ m.id }}</code><p>{{ m.metadata.train_start }} → {{ m.metadata.train_end }} · {{ m.metadata.pool_count }} 只 · {{ m.metadata.feature_count }} 特征</p><p>{{ scoreLabel(m.metadata.score_units) }}</p><details><summary>参数 / 实际轮数 / 数据边界</summary><pre>{{ JSON.stringify(m.metadata,null,2) }}</pre></details><div class="actions"><button class="btn btn-sm" @click="choose(m,'predict')">用此模型预测</button><button class="btn btn-sm" @click="choose(m,'backtest')">用此模型回测</button></div></div>
        <div class="actions"><button v-if="models.next_cursor" class="btn btn-sm" @click="modelControl.refresh(models.next_cursor)">更早模型</button><button v-if="models.before" class="btn btn-sm" @click="modelControl.refresh(null)">最新模型</button></div>
      </section>
    </div>
    <section class="card box"><h2>预测与回测结果 · 审核发布</h2><p>负收益、低相关或相关系数未定义不阻止发布。发布前请阅读结果日期、费用与执行假设；未就绪结果不能发布。</p><p v-if="results.error || previewError" role="alert">{{ results.error || previewError }}</p>
      <div class="list"><button v-for="r in results.items" :key="r.id" class="item" @click="selectResult(r.id)"><strong>{{ r.title }} · {{ r.algorithm }}</strong><span>{{ r.published ? '已发布' : '未发布' }} · {{ r.metadata.status }} · {{ r.id.slice(0,12) }}</span></button></div>
      <div class="actions"><button v-if="results.next_cursor" class="btn btn-sm" @click="resultControl.refresh(results.next_cursor)">更早结果</button><button v-if="results.before" class="btn btn-sm" @click="resultControl.refresh(null)">最新结果</button></div>
      <template v-if="selectedResult"><div class="actions"><label>分区<select v-model="section" @change="loadPreview()"><option v-for="s in sections" :key="s[0]" :value="s[0]">{{ s[1] }}</option></select></label><button class="btn" :disabled="busy || !preview || preview.report.status !== 'COMPLETE' || preview.published" @click="publication(true)">确认发布给用户</button><button class="btn" :disabled="busy || !preview?.published" @click="publication(false)">撤回发布</button></div><p v-if="previewLoading">正在校验结果…</p><WorkflowReport :value="preview" /><div class="actions"><button v-if="preview?.next_cursor != null" class="btn" @click="loadPreview(preview.next_cursor)">下一页记录</button><button v-if="previewAfter" class="btn" @click="loadPreview()">返回首屏</button></div></template>
    </section>
  </div>
</template>
<script setup>
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { authState } from '../../stores/auth.js'
import { useRoute } from 'vue-router'
import { workflowApi } from '../../api/workflows.js'
import { buildWorkflowRequest, operationLabels, scoreLabel, createWorkflowController } from '../../utils/workflows.js'
import { createTaskController, stateLabels, stageLabels, formatTaskProgress, failureLabels } from '../../utils/trainingTasks.js'
import WorkflowReport from '../../components/WorkflowReport.vue'
import WorkflowPoolSelector from '../../components/WorkflowPoolSelector.vue'
import DailyPredictionPanel from '../../components/DailyPredictionPanel.vue'
import ModelReleasePanel from '../../components/ModelReleasePanel.vue'
const poolReady=ref(true)
const route=useRoute()
function usePoolMembers(codes){if(draft.algorithm==='transformer'&&draft.pool_version)draft.stock_codes=codes.join(', ')}
const caps=ref(null),error=ref(''),notice=ref(''),busy=ref(false),jobs=reactive({}),models=reactive({}),results=reactive({}),detail=reactive({})
const draft=reactive({operation:'train',algorithm:'xgboost',start_date:'',end_date:'',ts_code:'000001.SZ',stock_codes:'',pool_version:'',model_id:'',n_estimators:20,max_depth:3,label:'raw',early_metric:'rmse',patience:30,num_epochs:1,batch_size:1,top_n:10,initial_capital:1000000,fee_bps:3,slippage_bps:10,import_task_id:'',experiment_id:'',trial:'raw_rmse20'})
const trials=['raw_rmse20','raw_rmse300','raw_rankic300','csrank_rankic300'],sections=[['scores','最新信号排行'],['equity','权益曲线'],['trades','成交'],['orders','未成交'],['writeoffs','退市处理'],['requirements','缺失证据']]
draft.feature_build_id=/^[0-9a-f]{32}$/.test(route.query.build||'')?route.query.build:''
const selectedResult=ref(''),preview=ref(null),previewError=ref(''),previewLoading=ref(false),section=ref('scores'),previewAfter=ref(0)
const enabled=()=>authState.mode==='protected' && authState.user?.role==='admin' && !authState.user.must_change_password
const visible=()=>!document.hidden
const jobControl=createWorkflowController(workflowApi,jobs,{enabled,visible,poll:true})
const modelControl=createWorkflowController({list:workflowApi.models},models,{enabled,visible})
const resultControl=createWorkflowController({list:workflowApi.results},results,{enabled,visible})
const taskControl=createTaskController(workflowApi,detail,{enabled,visible})
const matchingModels=computed(()=>models.items.filter(m=>m.algorithm===draft.algorithm))
let active=true,generation=0,previewGeneration=0,previewRequest,capRequest,submitBody='',submitKey=''
function current(run){return active && run===generation && enabled()}
function clearPreview(){previewGeneration++;previewRequest?.abort();preview.value=null;previewLoading.value=false;previewError.value='';previewAfter.value=0}
async function loadPreview(after=0){
  clearPreview();if(!active||!enabled()||!selectedResult.value)return
  const run=previewGeneration;previewRequest=new AbortController();previewLoading.value=true;previewAfter.value=after
  try{const data=await workflowApi.result(selectedResult.value,{section:section.value,after,limit:100,signal:previewRequest.signal});if(active&&enabled()&&run===previewGeneration)preview.value=data}
  catch(e){if(active&&enabled()&&run===previewGeneration&&e.name!=='AbortError')previewError.value=e.message}
  finally{if(active&&run===previewGeneration)previewLoading.value=false}
}
function selectResult(id){selectedResult.value=id;section.value='scores';loadPreview()}
function choose(m,operation){draft.algorithm=m.algorithm;draft.model_id=m.id;draft.operation=operation;window.scrollTo({top:0,behavior:'smooth'})}
async function refresh(){
  if(!active||!enabled())return
  const run=++generation;capRequest?.abort();capRequest=new AbortController();error.value=''
  const lists=Promise.all([jobControl.refresh(null),modelControl.refresh(null),resultControl.refresh(null)])
  try{const data=await workflowApi.capabilities({signal:capRequest.signal});if(!current(run))return;caps.value=data;draft.start_date||=data.source.source_start;draft.end_date||=data.source.source_cutoff;draft.stock_codes||=data.default_transformer_stocks.join(', ')}
  catch(e){if(current(run)&&e.name!=='AbortError'){caps.value=null;error.value=e.message}}
  await lists
}
async function submit(){
  if(busy.value||!enabled())return
  busy.value=true;error.value='';notice.value='';const run=generation
  try{const request=buildWorkflowRequest(draft),body=JSON.stringify(request);if(body!==submitBody||!submitKey){submitBody=body;submitKey=crypto.randomUUID().replaceAll('-','')}
    const job=await workflowApi.create(request,submitKey);if(!current(run))return;submitKey='';notice.value='已入队。关闭页面不会停止任务；请在完成后刷新模型与结果。';await Promise.all([jobControl.refresh(null),taskControl.select(job.id)])}
  catch(e){if(current(run))error.value=e.message}finally{if(active)busy.value=false}
}
async function cancel(){if(busy.value||!window.confirm('请求取消？执行进程完全退出前不会释放槽位。'))return;busy.value=true;const run=generation;try{await taskControl.cancel();if(current(run))notice.value='已请求取消，请以服务器最终状态为准。'}catch(e){if(current(run))error.value=e.message}finally{if(active)busy.value=false}}
async function publication(published){
  if(busy.value||!preview.value||!window.confirm(published?'确认将此真实结果发布给所有已登录用户？这不表示收益保证。':'确认撤回？后续用户请求将无法读取此结果。'))return
  const id=selectedResult.value,run=generation;busy.value=true;error.value=''
  try{await(published?workflowApi.publish(id):workflowApi.withdraw(id));if(!current(run))return;notice.value=published?'已发布；用户端刷新后可见。':'已撤回。';await resultControl.refresh();if(selectedResult.value===id)await loadPreview()}
  catch(e){if(current(run))error.value=e.message}finally{if(active)busy.value=false}
}
function reset(){generation++;capRequest?.abort();clearPreview();selectedResult.value='';caps.value=null;notice.value='';error.value='';submitKey='';jobControl.reset();modelControl.reset();resultControl.reset();taskControl.pause();Object.assign(detail,{selectedId:null,job:null,events:[]})}
watch(()=>[authState.user?.id,authState.user?.role,authState.mode,authState.user?.must_change_password],()=>{draft.pool_version='';reset()},{flush:'sync'})
watch(()=>draft.algorithm,()=>{draft.model_id='';if(draft.algorithm==='xgboost'&&draft.n_estimators>100)draft.n_estimators=100},{flush:'sync'})
function visibility(){if(!visible()){generation++;capRequest?.abort();jobControl.pause();modelControl.pause();resultControl.pause();taskControl.pause();clearPreview()}else{refresh();taskControl.refresh();loadPreview()}}
onMounted(()=>{refresh();document.addEventListener('visibilitychange',visibility)})
onBeforeUnmount(()=>{active=false;reset();jobControl.dispose();modelControl.dispose();resultControl.dispose();taskControl.dispose();document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.workflow{display:grid;gap:20px;min-width:0}.box{padding:24px;display:grid;gap:16px;min-width:0}header,.actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between}h1,h2,h3{margin:0}.muted{color:var(--text-3);line-height:1.7}.form{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:end}.form label{display:grid;gap:8px;min-width:0}.form input,.form select,.form textarea{width:100%;min-height:40px;min-width:0}.wide{grid-column:1/-1}.columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.list{display:grid;gap:6px;max-height:420px;overflow:auto}.item{border:1px solid var(--border);background:transparent;color:var(--text);text-align:left;padding:12px;cursor:pointer;display:grid;gap:6px}.item:hover,.item:focus-visible{border-color:var(--accent)}.item span{font-size:12px;color:var(--text-3)}.model{border-top:1px solid var(--border);padding-top:14px;display:grid;gap:8px}code,pre{font-size:12px;overflow-wrap:anywhere;white-space:pre-wrap}pre{max-height:280px;overflow:auto}details summary{cursor:pointer}.events{max-height:200px;overflow:auto;padding-left:24px;font-size:12px}[role=alert]{color:var(--serious)}@media(max-width:1000px){.columns{grid-template-columns:1fr}}@media(max-width:640px){.form{grid-template-columns:1fr}.box{padding:16px}}
</style>
