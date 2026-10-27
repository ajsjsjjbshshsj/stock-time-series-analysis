<template>
  <div>
    <PublishedResults :ts-code="tsCode" algorithm="transformer" />
    <details class="historical" @toggle="opened = $event.target.open"><summary>另行查看历史冻结试点信号（独立封存协议，非当前预测）</summary><AnalysisTransformerSignals v-if="opened" :ts-code="tsCode" /></details>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import PublishedResults from './PublishedResults.vue'
import AnalysisTransformerSignals from './AnalysisTransformerSignals.vue'
defineProps({ tsCode: { type: String, required: true } })
const opened = ref(false)
// Native details stays keyboard-accessible; no archive request before expansion.
</script>
<style scoped>
.historical { margin-top:20px; }.historical summary { cursor:pointer; padding:16px; color:var(--text-3); }
</style>
