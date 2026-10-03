<template>
  <AnalysisPredictionLive v-if="dataSource === 'hybrid'" :ts-code="tsCode" />
  <AnalysisPredictionDemo v-else :ts-code="tsCode" />
</template>
<script setup>
import { dataSource } from '../../api/client.js'
import AnalysisPredictionDemo from './AnalysisPredictionDemo.vue'
import AnalysisPredictionLive from './AnalysisPredictionLive.vue'
defineProps({ tsCode: { type: String, required: true } })
</script>
