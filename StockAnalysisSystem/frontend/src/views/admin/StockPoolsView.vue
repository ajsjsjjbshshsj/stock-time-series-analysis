<template>
  <div class="pool-manager">
    <header class="heading"><div><h1>股票池管理</h1><p class="muted">从已入库数据选择固定成员。预览与查询不采集；补缺需显式提交，不训练或发布。</p></div><button class="btn" :disabled="busy || loading" @click="refresh">刷新目录</button></header>
    <p v-if="!enabled" role="alert">此页面仅向受保护模式下的管理员开放。</p>
    <template v-else>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
      <section class="card box">
        <h2>从本地数据预览成员</h2>
        <p class="muted">快照固定成员用于整个历史区间，不是各历史交易日的动态成分，存在幸存者偏差。本地行业来自 stock_basic 分类，不冒充历史行业快照。</p>
        <form class="form" @submit.prevent="loadCandidate">
          <label>股票池类型<select aria-label="股票池类型" v-model="kind" :disabled="busy || !catalog"><option value="market">沪深A股全市场</option><option value="index">指数</option><option value="industry">行业</option></select></label>
          <label v-if="kind==='index'">指数<select aria-label="指数" v-model="code" :disabled="busy"><option value="000300.SH">沪深300</option><option value="000852.SH">中证1000</option></select></label>
          <label v-if="kind==='industry'">本地行业<select aria-label="本地行业" v-model="code" :disabled="busy || !catalog"><option v-for="row in catalog?.industries || []" :key="row.code" :value="row.code">{{ row.code }}（{{ row.count }}只）</option></select></label>
          <label v-if="kind==='index'">成分快照截至<input v-model="asOf" type="date" :max="catalog?.today" required :disabled="busy" /></label>
          <button class="btn" :disabled="busy || candidateLoading || !catalog || !code">{{ candidateLoading?'读取中…':'预览本地成员' }}</button>
        </form>
        <p v-if="candidateError" class="error" role="alert">{{ candidateError }}</p>
        <div v-if="candidate" class="candidate">
          <h3>{{ candidate.pool.display_name }} · {{ candidate.total }}只 · 尚未保存本次预览</h3>
          <p class="muted">来源 {{ candidate.pool.provider }} · 来源成分日期 {{ candidate.pool.provider_snapshot_date || '本地当前分类，无历史成分日期' }}</p>
          <code>{{ candidate.pool.pool_version }}</code>
          <p class="muted">成员 SHA256：{{ candidate.pool.member_sha256 }}</p>
          <DataTable :columns="memberColumns" :rows="candidate.preview" :page-size="10" />
          <button class="btn btn-primary" :disabled="busy" @click="save">{{ busy?'保存中…':'保存固定版本' }}</button><span class="muted"> 仅保存成员元数据，不改变训练参数或原默认版本。预览只显示前20只。</span>
        </div>
        <p v-else-if="!candidateLoading && !candidateError" class="muted">选择股票池后预览。没有已入库成分的指数会显示未就绪，不替换成全市场。</p>
      </section>
      <section class="card box">
        <h2>已保存的固定版本</h2>
        <p v-if="versionsError" role="alert" class="error">{{ versionsError }}</p>
        <label>固定版本<select aria-label="固定版本" v-model="selected" @change="changeVersion" :disabled="busy"><option value="">请选择</option><option v-for="row in versions.items" :key="row.pool_version" :value="row.pool_version">{{ row.display_name }} · {{ row.stock_count }}只 · {{ row.provider_snapshot_date || row.requested_as_of }} · {{ row.pool_version.slice(-8) }}</option></select></label>
        <div class="pager"><button class="btn btn-sm" :disabled="loading || versionOffset===0" @click="moveVersions(-20)">上一页版本</button><span>共 {{ versions.total }} 个版本</span><button class="btn btn-sm" :disabled="loading || versionOffset+20>=versions.total" @click="moveVersions(20)">下一页版本</button></div>
        <p v-if="membersError" role="alert" class="error">{{ membersError }}</p><p v-if="memberLoading" role="status">读取固定成员…</p>
        <template v-if="members">
          <p>{{ members.pool.display_name }} · 固定 {{ members.total }}只 · 来源 {{ members.pool.provider }} · 成分日期 {{ members.pool.provider_snapshot_date || '无历史成分日期' }}</p>
          <code>{{ members.pool.pool_version }}</code><p class="muted">成员 SHA256：{{ members.pool.member_sha256 }}</p>
          <DataTable :columns="memberColumns" :rows="members.items" />
          <div class="pager"><button class="btn btn-sm" :disabled="memberLoading || memberOffset===0" @click="moveMembers(-20)">上一页成员</button><span>{{ memberOffset+1 }} / {{ members.total }}</span><button class="btn btn-sm" :disabled="memberLoading || memberOffset+20>=members.total" @click="moveMembers(20)">下一页成员</button></div>
        </template>
      </section>
      <section class="card box">
        <h2>数据库覆盖查询</h2>
        <form class="form" @submit.prevent="queryCoverage(0)">
          <label>开始日期<input v-model="start" type="date" min="2023-01-01" :max="end" required /></label><label>结束日期<input v-model="end" type="date" :min="start" :max="catalog?.completed_date" required /></label>
          <button class="btn" :disabled="!selected || coverageLoading || busy">{{ coverageLoading?'核对本页…':'查询固定版本覆盖' }}</button>
        </form>
        <p class="muted">按页核对最多20只，不在网页载入全市场历史。缺行情可能是停牌或待核对，不补造价格；行覆盖不代表价格已校验，也不代表模型输入已就绪。</p>
        <p v-if="coverageError" role="alert" class="error">{{ coverageError }}</p>
        <template v-if="coverage">
          <p>本页统计：{{ coverage.items.length }}只 / 固定 {{ coverage.total }}只 · {{ coverage.start }} → {{ coverage.end }}</p>
          <p class="muted">已知适用交易日 {{ coverage.summary.eligible_sessions }} · 已有行情 {{ coverage.summary.daily_rows }} · 已验证无报价 {{ coverage.summary.verified_no_quote }} · 待核对无行情 {{ coverage.summary.unresolved_no_quote }} · 已有报价缺基本指标 {{ coverage.summary.missing_basic_for_quotes }}</p>
          <p v-if="coverage.unavailable_stocks" role="alert">本页 {{ coverage.unavailable_stocks }}只缺少可信上市资料，统计不包含这些股票；没有将其按零缺口处理。</p>
          <p v-if="coverage.reference_older_than_range" role="status">上市资料采集于 {{ coverage.reference_acquired_at }}，早于查询截止。之后的上市／退市变化仍需核对。</p>
          <DataTable :columns="coverageColumns" :rows="coverage.items" row-key="ts_code"><template #cell-status="{ row }">{{ statusLabel(row.status) }}</template></DataTable>
          <div class="pager"><button class="btn btn-sm" :disabled="coverageLoading || coverageOffset===0" @click="queryCoverage(coverageOffset-20)">上一页覆盖</button><span>{{ coverageOffset+1 }} / {{ coverage.total }}</span><button class="btn btn-sm" :disabled="coverageLoading || coverageOffset+20>=coverage.total" @click="queryCoverage(coverageOffset+20)">下一页覆盖</button></div>
        </template>
      </section>
      <PoolPreparationPanel :pool-version="selected" :start="start" :end="end" />
    </template>
  </div>
</template>

<script setup>
import {computed,reactive,ref,watch,onMounted,onBeforeUnmount} from 'vue'
import DataTable from '../../components/DataTable.vue'
import PoolPreparationPanel from '../../components/PoolPreparationPanel.vue'
import {poolApi as api} from '../../api/pools.js'
import {authState} from '../../stores/auth.js'
const enabled=computed(()=>authState.mode==='protected'&&authState.user?.role==='admin'&&!authState.user.must_change_password)
const catalog=ref(null),kind=ref('market'),code=ref('SH_SZ_A'),asOf=ref(''),candidate=ref(null),candidateError=ref(''),candidateLoading=ref(false)
const versions=reactive({items:[],total:0}),versionOffset=ref(0),versionsError=ref(''),selected=ref(''),members=ref(null),memberOffset=ref(0),membersError=ref(''),memberLoading=ref(false)
const start=ref('2023-01-01'),end=ref(''),coverage=ref(null),coverageOffset=ref(0),coverageError=ref(''),coverageLoading=ref(false),busy=ref(false),loading=ref(false),error=ref(''),notice=ref('')
const memberColumns=[{key:'ts_code',label:'股票代码'},{key:'weight',label:'成分权重（来源值）'}]
const coverageColumns=[{key:'ts_code',label:'股票代码'},{key:'status',label:'覆盖状态'},{key:'eligible_sessions',label:'适用交易日'},{key:'daily_rows',label:'已有行情'},{key:'daily_basic_rows',label:'已有基本指标'},{key:'verified_no_quote',label:'已验证无报价'},{key:'unresolved_no_quote',label:'待核对无行情'},{key:'missing_basic_for_quotes',label:'报价缺基本指标'},{key:'stored_first',label:'区间首日'},{key:'stored_last',label:'区间末日'}]
const statusLabel=s=>({ROW_COVERED:'行覆盖（非模型就绪）',NEEDS_CHECK:'需核对',NO_APPLICABLE_SESSIONS:'无适用交易日',REFERENCE_UNAVAILABLE:'上市资料未就绪'}[s]||s)
let active=true,epoch=0,loadRun=0,candidateRun=0,memberRun=0,coverageRun=0,candidateSelector=null
let loadAbort,candidateAbort,memberAbort,coverageAbort
const current=e=>active&&enabled.value&&e===epoch
const selector=()=>({kind:kind.value,code:code.value,as_of:kind.value==='index'?asOf.value:catalog.value.today})
function clearCandidate(){candidateRun++;candidateAbort?.abort();candidate.value=null;candidateSelector=null;candidateError.value='';candidateLoading.value=false}
function clearCoverage(){coverageRun++;coverageAbort?.abort();coverage.value=null;coverageError.value='';coverageLoading.value=false;coverageOffset.value=0}
async function refresh(){
  if(!active||!enabled.value)return
  const e=epoch,r=++loadRun;loadAbort?.abort();loadAbort=new AbortController();loading.value=true;error.value='';versionsError.value=''
  try{const [c,v]=await Promise.all([api.catalog({signal:loadAbort.signal}),api.versions({offset:versionOffset.value,signal:loadAbort.signal})]);if(!current(e)||r!==loadRun)return;catalog.value=c;Object.assign(versions,v);asOf.value||=c.today;end.value||=c.completed_date}
  catch(err){if(current(e)&&r===loadRun&&err.name!=='AbortError')error.value=err.message}
  finally{if(current(e)&&r===loadRun)loading.value=false}
}
async function loadCandidate(){
  clearCandidate();if(!enabled.value||!catalog.value)return
  const e=epoch,r=candidateRun,s=selector();candidateAbort=new AbortController();candidateLoading.value=true;notice.value=''
  try{const v=await api.candidate(s,{signal:candidateAbort.signal});if(current(e)&&r===candidateRun){candidate.value=v;candidateSelector=s}}
  catch(err){if(current(e)&&r===candidateRun&&err.name!=='AbortError')candidateError.value=err.message}
  finally{if(current(e)&&r===candidateRun)candidateLoading.value=false}
}
async function save(){
  if(busy.value||!candidate.value||!candidateSelector||!enabled.value)return
  const e=epoch,s={...candidateSelector,expected_version:candidate.value.pool.pool_version};busy.value=true;notice.value='';candidateError.value=''
  try{const v=await api.save(s);if(!current(e))return;selected.value=v.pool.pool_version;versionOffset.value=0;clearCandidate();notice.value='固定成员已保存；没有采集、训练或修改默认版本。';await refresh();await changeVersion()}
  catch(err){if(current(e))candidateError.value=err.message}
  finally{if(current(e))busy.value=false}
}
async function loadMembers(){
  const e=epoch,r=++memberRun;memberAbort?.abort();members.value=null;membersError.value='';if(!selected.value||!enabled.value)return
  memberAbort=new AbortController();memberLoading.value=true
  try{const v=await api.members(selected.value,{offset:memberOffset.value,signal:memberAbort.signal});if(current(e)&&r===memberRun)members.value=v}
  catch(err){if(current(e)&&r===memberRun&&err.name!=='AbortError')membersError.value=err.message}
  finally{if(current(e)&&r===memberRun)memberLoading.value=false}
}
function changeVersion(){clearCoverage();memberOffset.value=0;return loadMembers()}
function moveMembers(n){memberOffset.value+=n;loadMembers()}
function moveVersions(n){versionOffset.value+=n;refresh()}
async function queryCoverage(offset=0){
  clearCoverage();if(!selected.value||!enabled.value)return
  const e=epoch,r=coverageRun;coverageOffset.value=offset;coverageAbort=new AbortController();coverageLoading.value=true
  try{const v=await api.coverage(selected.value,{start:start.value,end:end.value,offset,signal:coverageAbort.signal});if(current(e)&&r===coverageRun)coverage.value=v}
  catch(err){if(current(e)&&r===coverageRun&&err.name!=='AbortError')coverageError.value=err.message}
  finally{if(current(e)&&r===coverageRun)coverageLoading.value=false}
}
watch(kind,()=>{code.value=kind.value==='market'?'SH_SZ_A':kind.value==='index'?'000300.SH':catalog.value?.industries[0]?.code||''})
watch([kind,code,asOf],clearCandidate,{flush:'sync'});watch([start,end],clearCoverage,{flush:'sync'})
watch(()=>[authState.user?.id,authState.user?.role,authState.mode,authState.user?.must_change_password],()=>{reset();refresh()},{flush:'sync'})
function reset(){epoch++;loadRun++;loadAbort?.abort();memberRun++;memberAbort?.abort();clearCandidate();clearCoverage();catalog.value=null;versions.items=[];versions.total=0;selected.value='';members.value=null;loading.value=false;memberLoading.value=false;busy.value=false;error.value='';notice.value='';membersError.value='';versionOffset.value=0}
onMounted(refresh);onBeforeUnmount(()=>{active=false;reset()})
</script>

<style scoped>
.heading{display:flex;justify-content:space-between;align-items:center;gap:16px}.box{margin-top:20px}.box h2{margin-bottom:12px}.form{display:flex;align-items:end;gap:16px;flex-wrap:wrap;margin:16px 0}label{display:grid;gap:7px;min-width:180px}select{max-width:100%}.candidate{border-left:3px solid var(--accent);padding:16px;margin-top:16px}.candidate code,code{overflow-wrap:anywhere}.pager{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:12px;margin-top:12px}.error{color:var(--critical)}p{margin:10px 0;line-height:1.6}@media(max-width:700px){.heading{align-items:start}.candidate{padding:10px}label{max-width:100%}}
</style>
