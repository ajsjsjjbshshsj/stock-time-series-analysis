<template>
  <section class="card continuation">
    <header><h2>基于已有 LightGBM 模型继续训练</h2><button class="btn" type="button" :disabled="loading || busy" @click="refresh">刷新可用底座</button></header>
    <p>保留父模型的原有树和股票池，只追加新树。后台先检查新增数据、历史一致性及独立比较期，合格才训练；候选不会自动上线。</p>
    <p v-if="loading" role="status">正在检查可用底座…</p>
    <p v-else-if="!items.length && !error">没有可续训的已审核 LightGBM 模型。</p>
    <form v-else-if="items.length" @submit.prevent="submit">
      <label>父模型<select v-model="parent" :disabled="busy" required>
        <option value="">请选择</option>
        <option v-for="item in items" :key="item.id" :value="item.id">{{ item.title }} · {{ item.id.slice(0, 12) }}</option>
      </select></label>
      <label>追加轮数<input v-model.number="rounds" :disabled="busy" type="number" min="1" max="20" required /></label>
      <button class="btn" type="submit" :disabled="busy || loading || blocked || !featureBuild || !parent">{{ busy ? '正在提交…' : '创建续训任务' }}</button>
    </form>
    <p v-if="selected">原模型已见数据至 {{ selected.seen_through }} · {{ selected.pool_count }} 只 · {{ selected.feature_count }} 个特征</p>
    <p v-if="blocked">执行槽隔离中，暂不能提交。</p>
    <p v-else-if="!featureBuild">当前特征版本未就绪。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { continuationApi as api } from '../api/lightgbmContinuation.js'

const props = defineProps({ featureBuild: String, blocked: Boolean })
const emit = defineEmits(['queued'])
const items = ref([]), parent = ref(''), rounds = ref(10), loading = ref(false), busy = ref(false)
const error = ref(''), notice = ref('')
const selected = computed(() => items.value.find(item => item.id === parent.value))
let live = true, request, lastBody, key

async function refresh() {
  request?.abort()
  const controller = request = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const data = await api.parents({ signal: controller.signal })
    if (!live || request !== controller) return
    items.value = data.items
    if (!selected.value) parent.value = ''
  } catch (e) {
    if (live && request === controller && e.name !== 'AbortError') {
      items.value = []
      error.value = e.message
    }
  } finally { if (live && request === controller) loading.value = false }
}

async function submit() {
  if (!live || busy.value || loading.value || props.blocked || !props.featureBuild || !selected.value) return
  if (!window.confirm('确认追加训练？父模型保持上线，新候选需另行审核。')) return
  const body = { parent_model_id: parent.value, feature_build_id: props.featureBuild, additional_rounds: rounds.value }
  const serialized = JSON.stringify(body)
  if (lastBody !== serialized || !key) {
    lastBody = serialized
    key = crypto.randomUUID()
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const job = await api.create(body, key)
    if (!live) return
    key = null
    notice.value = '已入队。后台将先核对数据与比较期，请在任务队列查看进度。'
    emit('queued', job.id)
  } catch (e) {
    if (live) error.value = e.message
  } finally { if (live) busy.value = false }
}

onMounted(refresh)
onBeforeUnmount(() => { live = false; request?.abort() })
</script>

<style scoped>
.continuation{padding:24px;display:grid;gap:14px}.continuation header,.continuation form{display:flex;align-items:end;justify-content:space-between;gap:12px;flex-wrap:wrap}.continuation h2,.continuation p{margin:0}.continuation label{display:grid;gap:6px}.continuation select,.continuation input{min-height:40px;max-width:100%}[role=alert]{color:var(--serious)}
</style>
