<template>
  <div class="pool-input">
    <label>训练股票池<select aria-label="训练股票池" :value="modelValue" :disabled="loading" @change="$emit('update:modelValue',$event.target.value)">
      <option value="">沿用算法默认范围（LightGBM为全市场）</option>
      <option v-if="modelValue && !items.some(row=>row.pool_version===modelValue)" :value="modelValue">当前固定版本 · {{ modelValue }}</option>
      <option v-for="row in items" :key="row.pool_version" :value="row.pool_version">{{ row.display_name }} · {{ row.stock_count }}只 · {{ row.pool_version.slice(-8) }}</option>
    </select></label>
    <div class="pool-pages"><button type="button" class="btn btn-sm" :disabled="loading || offset===0" @click="loadVersions(offset-20)">上一页股票池</button><span>共{{ total }}个固定版本</span><button type="button" class="btn btn-sm" :disabled="loading || offset+20>=total" @click="loadVersions(offset+20)">下一页股票池</button></div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="memberLoading" role="status">读取固定成员…</p>
    <template v-if="selected">
      <p>已绑定固定版本（尚未提交任务）：{{ selected.display_name }} · {{ selected.stock_count }}只 · {{ selected.provider }} · {{ selected.provider_snapshot_date || selected.requested_as_of }}</p>
      <code>{{ selected.pool_version }}</code>
      <p v-if="!compatible" role="alert">当前Transformer入口只支持完整的10–20只固定池，不会截取成员。请选择小池或等待大池资源配置。</p>
      <p v-else>选择不会补采或训练，也不代表特征已就绪。XGBoost仍只训练输入的单只股票，且必须属于所选池；预测和回测沿用已保存模型的池。</p>
    </template>
    <p v-else-if="!modelValue">可先在“股票池管理”保存指数、行业或全市场版本；不选择则保留原算法范围。</p>
  </div>
</template>
<script setup>
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {authState} from '../stores/auth.js'
import {poolApi} from '../api/pools.js'
const props=defineProps({modelValue:{type:String,default:''},algorithm:{type:String,required:true}})
const emit=defineEmits(['update:modelValue','members','ready'])
const items=ref([]),total=ref(0),offset=ref(0),selected=ref(null),error=ref(''),loading=ref(false),memberLoading=ref(false)
const enabled=()=>authState.mode==='protected'&&authState.user?.role==='admin'&&!authState.user.must_change_password
const compatible=computed(()=>!selected.value||props.algorithm!=='transformer'||selected.value.stock_count>=10&&selected.value.stock_count<=20)
let active=true,versionRun=0,memberRun=0,versionAbort,memberAbort
async function loadVersions(next=0){
  const run=++versionRun;versionAbort?.abort();versionAbort=new AbortController()
  if(!active||!enabled())return
  loading.value=true;error.value=''
  try{const data=await poolApi.versions({offset:next,signal:versionAbort.signal});if(!active||!enabled()||run!==versionRun)return;items.value=data.items;total.value=data.total;offset.value=next}
  catch(e){if(active&&enabled()&&run===versionRun&&e.name!=='AbortError')error.value=e.message}
  finally{if(active&&run===versionRun)loading.value=false}
}
async function loadMembers(){
  const run=++memberRun;memberAbort?.abort();memberAbort=new AbortController();selected.value=null;error.value='';memberLoading.value=false
  emit('ready',!props.modelValue)
  if(!active||!enabled()||!props.modelValue)return
  memberLoading.value=true
  try{const data=await poolApi.members(props.modelValue,{limit:20,signal:memberAbort.signal});if(!active||!enabled()||run!==memberRun)return
    selected.value=data.pool;emit('members',data.total<=20?data.items.map(row=>row.ts_code):[]);emit('ready',compatible.value)}
  catch(e){if(active&&enabled()&&run===memberRun&&e.name!=='AbortError')error.value=e.message}
  finally{if(active&&run===memberRun)memberLoading.value=false}
}
watch(()=>[props.modelValue,props.algorithm],loadMembers,{flush:'sync'})
watch(()=>[authState.user?.id,authState.user?.role,authState.mode],()=>{versionRun++;memberRun++;versionAbort?.abort();memberAbort?.abort();items.value=[];selected.value=null;emit('ready',false)},{flush:'sync'})
onMounted(()=>{loadVersions();loadMembers()})
onBeforeUnmount(()=>{active=false;versionRun++;memberRun++;versionAbort?.abort();memberAbort?.abort()})
</script>
<style scoped>
.pool-input{display:grid;gap:8px;min-width:0;padding:16px;border:1px solid var(--border);border-left:3px solid var(--accent)}label{display:grid;gap:8px}select{width:100%;min-height:40px}.pool-pages{display:flex;flex-wrap:wrap;gap:12px;align-items:center}p{margin:0;color:var(--text-3);line-height:1.6}[role=alert]{color:var(--serious)}code{font-size:12px;overflow-wrap:anywhere}
</style>
