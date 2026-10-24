<template>
  <section class="card model-releases"><header><h2>用户可用模型 · 上线审核</h2><button class="btn" :disabled="busy" @click="refresh">刷新上线状态</button></header><p>审核上线后，用户可用此固定模型预测并直接查看结果；不能训练。上线不会立即运行预测，也不改变原有结果发布流程。请核查日期、股票池与适用范围。</p><p v-if="error" role="alert">{{error}}</p>
    <div v-for="m in models" :key="m.id" class="release-row"><div><strong>{{m.algorithm}} · {{m.title}}</strong><code>{{m.id}}</code></div><span>{{ready?(enabled(m.id)?'已上线供用户预测':'未上线'):'状态未确认'}}</span><button class="btn btn-sm" :disabled="busy||!ready" @click="toggle(m)">{{enabled(m.id)?'撤下用户预测模型':'审核上线供用户预测'}}</button></div>
  </section>
</template>
<script setup>
import {ref,onMounted,onBeforeUnmount,watch} from 'vue'
import {authState} from '../stores/auth.js'
import {userPredictionApi as api} from '../api/userPredictions.js'
defineProps({models:{type:Array,default:()=>[]}})
const items=ref([]),busy=ref(false),ready=ref(false),error=ref('')
let live=true,generation=0,request
const allowed=()=>live&&authState.user?.role==='admin'&&authState.mode==='protected'&&!authState.user.must_change_password
const enabled=id=>items.value.some(r=>r.model_id===id&&r.enabled)
function reset(){generation++;request?.abort();items.value=[];ready.value=false;busy.value=false;error.value=''}
async function refresh(){if(!allowed())return;const run=++generation;request?.abort();request=new AbortController();ready.value=false
  try{const value=await api.releases({signal:request.signal});if(allowed()&&run===generation){items.value=value.items;ready.value=true;error.value=''}}catch(e){if(allowed()&&run===generation&&e.name!=='AbortError')error.value=e.message}}
async function toggle(model){if(busy.value||!allowed()||!ready.value)return;const next=!enabled(model.id)
  if(!window.confirm(next?'确认审核此固定版本供用户预测？用户可计算并直接查看结果，但不能训练。':'确认撤下？新请求和用户读取均会拒绝，已有计算可能仍在完成或退出。'))return
  busy.value=true;const run=generation
  try{await api.release(model.id,next);if(allowed()&&run===generation)await refresh()}catch(e){if(allowed()&&run===generation)error.value=e.message}finally{if(live)busy.value=false}}
watch(()=>[authState.mode,authState.user?.id,authState.user?.role,authState.user?.must_change_password],()=>{reset();refresh()},{flush:'sync'})
onMounted(refresh);onBeforeUnmount(()=>{live=false;reset()})
</script>
<style scoped>
.model-releases{padding:24px;margin-block:20px;display:grid;gap:16px}.model-releases header,.release-row{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.model-releases h2,.model-releases p{margin:0}.model-releases p{line-height:1.7;color:var(--text-3)}.release-row{border-top:1px solid var(--border);padding-top:14px}.release-row>div{display:grid;gap:6px;min-width:0}.release-row code{font-size:12px;overflow-wrap:anywhere}[role=alert]{color:var(--serious)!important}
</style>
