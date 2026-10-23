<template>
  <section class="card daily-panel">
    <header><h2>每晚预测 · 保存模型自动推理</h2><button class="btn" :disabled="busy" @click="refresh">刷新状态</button></header>
    <p>20:00后等待当日全市场采集完成，再构建并启用特征、使用指定的已保存模型预测。不会自动训练或公开发布；用户选择已上线模型及兼容股票池时，可复用输入完全一致的封存结果，不需要再次计算。</p>
    <p>新计划默认关闭。启用代表允许后续夜间特征切换和预测；关闭阻止新阶段，但不会撤销已经入队的任务。停牌、新股不会补造价格；数据或模型不兼容时显示阻塞。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <form @submit.prevent="create"><label>固定模型版本<select v-model="model" required><option value="">请选择已保存模型</option><option v-for="m in models" :key="m.id" :value="m.id">{{m.algorithm}} · {{m.id}}</option></select></label><button class="btn" :disabled="busy||!model">创建关闭状态的计划</button></form>
    <div v-for="p in items" :key="p.id" class="daily-row"><strong>{{p.proof.algorithm}} · {{p.enabled?'已启用':'已关闭'}}</strong><code>{{p.model_id}}</code><button class="btn" :disabled="busy||p.actor_id!==authState.user?.id" @click="toggle(p)">{{p.enabled?'关闭后续阶段':'确认启用夜间链路'}}</button></div>
    <p v-if="!items.length&&!error">尚无计划。这里只创建配置，不会立即执行历史补跑。</p>
    <table v-if="runs.length"><thead><tr><th>日期</th><th>状态</th><th>原因 / 任务</th></tr></thead><tbody><tr v-for="r in runs" :key="r.id"><td>{{r.day}}</td><td>{{labels[r.state.status]||r.state.status}}</td><td>{{r.state.reason||'—'}} <RouterLink v-if="r.state.prediction_task" :to="{path:'/training',query:{task:r.state.prediction_task}}">查看任务</RouterLink></td></tr></tbody></table>
  </section>
</template>
<script setup>
import {ref,onMounted,onBeforeUnmount,watch} from 'vue'
import {RouterLink} from 'vue-router'
import {dailyPredictionApi as api} from '../api/dailyPredictions.js'
import {authState} from '../stores/auth.js'
defineProps({models:{type:Array,default:()=>[]}})
const model=ref(''),items=ref([]),runs=ref([]),busy=ref(false),error=ref('')
const labels={WAITING_SCHEDULE:'等待20:00或下一交易日',WAITING_DEPENDENCY:'等待数据或依赖',BUILDING:'构建特征',ACTIVATING:'核验并启用特征',PREDICTING:'生成预测',COMPLETE:'结果已封存 · 可按权限复用',BLOCKED:'阻塞 · 需管理员处理'}
let live=true,generation=0,request
const allowed=()=>live&&authState.mode==='protected'&&authState.user?.role==='admin'&&!authState.user.must_change_password
function reset(){generation++;request?.abort();items.value=[];runs.value=[];error.value='';busy.value=false}
async function refresh(){if(!allowed())return;const run=++generation;request?.abort();request=new AbortController();error.value=''
  try{const data=await api.list({signal:request.signal});if(allowed()&&run===generation){items.value=data.items;runs.value=data.runs}}
  catch(e){if(allowed()&&run===generation&&e.name!=='AbortError'){items.value=[];runs.value=[];error.value=e.message}}}
async function action(fn){if(busy.value||!allowed())return;busy.value=true;const run=generation
  try{await fn();if(allowed()&&run===generation)await refresh()}catch(e){if(allowed()&&run===generation)error.value=e.message}finally{if(live)busy.value=false}}
function create(){return action(()=>api.create(model.value))}
function toggle(p){if(!window.confirm(p.enabled?'关闭此计划后续夜间阶段？已入队任务仍需单独管理。':'允许每晚自动构建及启用特征，并使用此模型生成未发布结果？不自动训练、不自动发布。'))return;return action(()=>api.enabled(p.id,!p.enabled))}
watch(()=>[authState.mode,authState.user?.id,authState.user?.role,authState.user?.must_change_password],()=>{reset();refresh()})
onMounted(refresh);onBeforeUnmount(()=>{live=false;reset()})
</script>
<style scoped>
.daily-panel{padding:24px;margin-top:20px}.daily-panel header,.daily-panel form,.daily-row{display:flex;gap:16px;align-items:center;flex-wrap:wrap}.daily-row{border-top:1px solid var(--border-color,#444);padding:12px 0}.daily-panel code{overflow-wrap:anywhere}.daily-panel p{line-height:1.7}.daily-panel table{width:100%;text-align:left}
</style>
