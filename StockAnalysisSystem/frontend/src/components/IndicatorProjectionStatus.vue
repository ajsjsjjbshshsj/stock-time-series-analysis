<template>
  <section class="card section evidence-card" :class="{ 'has-evidence': status?.indicatorReady, 'needs-repair': status?.state === 'REPAIR_REQUIRED' }"
    :aria-busy="loading" aria-label="服务端指标验收状态">
    <div class="evidence-header">
      <div class="card-title">服务端历史指标验收 <span class="evidence-code">{{ tsCode }}</span></div>
      <button class="evidence-refresh" :disabled="loading || !live" @click="load">刷新验收状态</button>
    </div>
    <p v-if="!live" role="status">演示模式不验证服务端指标。</p>
    <p v-else-if="loading" role="status" aria-live="polite">读取验收状态…</p>
    <p v-else-if="error" class="evidence-warning" role="alert">验收状态暂不可用：{{ error }}。请刷新重试或联系管理员。</p>
    <template v-else-if="status">
      <p role="status" aria-live="polite">{{ summary }}</p>
      <dl v-if="status.verifiedThrough" class="evidence-dates">
        <div><dt>验收至</dt><dd>{{ status.verifiedThrough }}</dd></div>
        <div><dt>{{ status.indicatorReady ? '最近已验收行情' : '当前可用指标' }}</dt><dd>{{ status.latestObservation || '暂不可用' }}</dd></div>
      </dl>
      <p v-if="status.isWarmup" class="evidence-note">仍处于指标预热阶段，部分数值为空，不补造行情。</p>
    </template>
    <p class="evidence-note">本页图表仍由浏览器基于日线计算，与服务端验收状态分开展示；指标状态不代表模型或预测已就绪。</p>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getIndicatorReadiness } from '../api/analysis.js'
import { dataSource, requestScope } from '../api/client.js'
import { createGenerationGuard, settleDetailRequest } from '../utils/prediction.js'

const props = defineProps({ tsCode: { type: String, required: true } })
const live = computed(() => dataSource.value === 'hybrid')
const status = ref(null)
const loading = ref(false)
const error = ref('')
const guard = createGenerationGuard()
const summary = computed(() => {
  if (status.value?.indicatorReady) return '服务端指标已按所示截止日核对，不代表此后行情或模型预测已更新。'
  if (status.value?.state === 'REPAIR_REQUIRED') return '该股服务端指标待修复，当前不作为已就绪结果展示。'
  if (status.value?.state === 'SOURCE_PENDING') return '该股历史行情有更新，指标尚未重新验收；暂不展示旧的已就绪状态。'
  if (status.value?.state === 'NO_SEED') return '该股尚未完成服务端历史指标引导。'
  if (status.value?.state === 'SEED_COMPLETE') return '该股暂无可用的已验收指标。'
  return '尚无完整的服务端指标验收证明；现有图表不视为服务端验收通过。'
})

async function load() {
  const run = guard.next()
  status.value = null
  error.value = ''
  if (!live.value) { loading.value = false; return }
  loading.value = true
  await settleDetailRequest(getIndicatorReadiness(props.tsCode, { signal: run.signal }), run, {
    success: value => { status.value = value },
    error: reason => { error.value = reason.message },
    settled: () => { loading.value = false },
  })
}

watch([() => props.tsCode, () => requestScope.value], load, { immediate: true })
onBeforeUnmount(() => guard.cancel())
</script>

<style scoped>
.evidence-card { border-inline-start: 3px solid var(--border-strong); }
.evidence-card.has-evidence { border-inline-start-color: var(--accent); }
.evidence-card.needs-repair { border-inline-start-color: var(--warning); }
.evidence-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
.evidence-header .card-title { margin: 0; }
.evidence-code { color: var(--text-2); font-size: 12px; font-weight: 400; margin-inline-start: 8px; }
.evidence-dates { display: flex; flex-wrap: wrap; gap: 12px 28px; margin: 12px 0; }
.evidence-dates dt, .evidence-note { color: var(--text-2); font-size: 12px; }
.evidence-dates dd { color: var(--text-1); font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.evidence-note { margin-top: 8px; }
.evidence-warning { color: var(--warning); }
.evidence-refresh { border: 1px solid var(--border-strong); border-radius: 6px; background: var(--surface-2); color: var(--text-1); padding: 6px 10px; cursor: pointer; }
.evidence-refresh:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.evidence-refresh:disabled { opacity: .6; cursor: default; }
</style>
