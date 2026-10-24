<template>
  <section class="card published">
    <header><div><h2>已发布模型结果</h2><p class="muted">后台保存并审核发布的真实结果；浏览页面不会训练或重新计算。</p></div><button class="btn" :disabled="!enabled() || catalog.loading" @click="refresh">刷新发布列表</button></header>
    <p v-if="!enabled()" role="status">需要受保护模式及登录才能读取已发布结果；当前未查询发布库，不代表没有结果。</p>
    <template v-else>
    <p v-if="catalog.error || error" role="alert">{{ catalog.error || error }}</p>
    <p v-if="catalog.loading || busy" role="status">正在校验并读取封存结果…</p>
    <p v-if="catalogLoaded && !visibleItems.length && !catalog.loading && !catalog.error" role="status">{{ catalog.next_cursor || catalog.before ? '本页暂无匹配的已发布结果，可翻阅其他发布页。' : '暂无匹配的已发布结果，请等待管理员审核发布。' }}</p>
    <p v-if="operation || algorithm" class="muted">筛选：{{ algorithm || '全部模型' }} · {{ operation || '预测与回测' }}。固定池和日期以各封存报告为准，不代表当前全市场。</p>
    <div class="filters" v-if="visibleItems.length">
      <label>结果版本<select v-model="selected" @change="load()"><option value="">请选择</option><option v-for="r in visibleItems" :key="r.id" :value="r.id">{{ r.algorithm }} · {{ r.title }} · {{ r.id.slice(0,8) }}</option></select></label>
      <label>分区<select v-model="section" @change="load()"><option value="scores">最新信号排行</option><option value="equity">回测权益曲线</option><option value="trades">成交记录</option><option value="orders">未成交记录</option><option value="writeoffs">退市处理</option></select></label>
      <label><input v-model="stockOnly" type="checkbox" @change="load()" /> 仅当前股票 {{ tsCode }}</label>
    </div>
    <p v-if="stockOnly && !validStock" role="status">当前股票不属于沪深 A 股，本工作流不覆盖北交所。</p>
    <WorkflowReport :value="result" />
    <div class="actions"><button v-if="result?.next_cursor != null" class="btn" :disabled="busy" @click="load(result.next_cursor)">下一页记录</button><button v-if="after" class="btn" :disabled="busy" @click="load()">返回首屏</button><button v-if="catalog.next_cursor" class="btn" @click="nextCatalog">更早发布</button><button v-if="catalog.before" class="btn" @click="refresh">最新发布</button></div>
    </template>
  </section>
</template>
<script setup>
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { publishedApi } from '../../api/workflows.js'
import { createWorkflowController } from '../../utils/workflows.js'
import { authState } from '../../stores/auth.js'
import WorkflowReport from '../../components/WorkflowReport.vue'
import { filterPublishedResults } from '../../utils/analysisPanels.js'
const props = defineProps({ tsCode: { type: String, default: '' }, operation: { type: String, default: '' }, algorithm: { type: String, default: '' }, initialSection: { type: String, default: 'scores' } })
const initialSection = () => ['scores','equity','trades','orders','writeoffs'].includes(props.initialSection) ? props.initialSection : 'scores'
const selected = ref(''), section = ref(initialSection()), stockOnly = ref(false), result = ref(null), busy = ref(false), error = ref(''), after = ref(0), catalog = reactive({})
const visibleItems = computed(() => filterPublishedResults(catalog.items || [], props))
const catalogLoaded = ref(false)
const enabled = () => authState.mode === 'protected' && Boolean(authState.user) && !authState.user.must_change_password
const control = createWorkflowController(publishedApi, catalog, { enabled })
const validStock = computed(() => /^[0-9]{6}\.(SH|SZ)$/.test(props.tsCode))
let active = true, generation = 0, catalogGeneration = 0, request
function clear() { generation++; request?.abort(); result.value = null; busy.value = false; error.value = ''; after.value = 0 }
async function load(cursor = 0) {
  clear()
  if (!active || !enabled() || !visibleItems.value.some(row => row.id === selected.value) || (stockOnly.value && !validStock.value)) return
  const run = generation; request = new AbortController(); busy.value = true; after.value = cursor
  try { const data = await publishedApi.get(selected.value, { section: section.value, after: cursor, limit:100, ts_code:stockOnly.value ? props.tsCode : undefined, signal:request.signal }); if (active && run === generation && enabled()) result.value = data }
  catch (e) { if (active && run === generation && enabled() && e.name !== 'AbortError') error.value = e.message }
  finally { if (active && run === generation) busy.value = false }
}
async function readCatalog(before) {
  const run = ++catalogGeneration; catalogLoaded.value = false
  await control.refresh(before)
  if (active && run === catalogGeneration && enabled() && !document.hidden && !catalog.error) catalogLoaded.value = true
  return run === catalogGeneration
}
async function refresh() { clear(); selected.value = ''; const current = await readCatalog(null); if (current && active && enabled() && visibleItems.value.length) { selected.value = visibleItems.value[0].id; await load() } }
async function nextCatalog() { clear(); selected.value = ''; await readCatalog(catalog.next_cursor) }
watch(() => props.tsCode, () => { if (stockOnly.value) load() })
watch(() => [props.operation, props.algorithm, props.initialSection], () => { section.value = initialSection(); refresh() })
watch(() => [authState.user?.id, authState.user?.role, authState.user?.must_change_password, authState.mode], () => { catalogGeneration++; catalogLoaded.value = false; clear(); selected.value = ''; control.reset() }, { flush:'sync' })
function visibility() { if (document.hidden) { catalogGeneration++; clear(); control.pause() } else refresh() }
onMounted(() => { refresh(); document.addEventListener('visibilitychange',visibility) })
onBeforeUnmount(() => { active = false; clear(); control.dispose(); document.removeEventListener('visibilitychange',visibility) })
</script>
<style scoped>
.published { padding:24px; display:grid; gap:20px; min-width:0; }header,.actions { display:flex; flex-wrap:wrap; gap:12px; justify-content:space-between; align-items:center; }.filters { display:flex; flex-wrap:wrap; gap:16px; align-items:end; }.filters label { display:grid; gap:8px; max-width:100%; }.filters select{min-height:38px;max-width:100%;}.muted{color:var(--text-3);margin-top:8px;}[role=alert]{color:var(--serious);}
</style>
