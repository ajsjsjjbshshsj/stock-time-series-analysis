<template>
  <div ref="rootEl" class="stock-search">
    <div class="input-wrap">
      <Icon name="search" class="search-icon" aria-hidden="true" />
      <input
        :id="id"
        ref="inputEl"
        v-model="query"
        type="text"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="open"
        :aria-controls="open ? listId : undefined"
        :aria-activedescendant="activeId"
        autocomplete="off"
        autocorrect="off"
        spellcheck="false"
        :placeholder="placeholder"
        :disabled="disabled"
        @input="onInput"
        @focus="onFocus"
        @click="reveal"
        @keydown="onKeydown"
        @compositionstart="composing = true"
        @compositionend="onCompositionEnd"
      />
    </div>
    <ul v-if="open && !disabled" :id="listId" ref="listEl" role="listbox" class="result-list" aria-label="股票搜索结果">
      <li
        v-for="(r, i) in results"
        :id="`${id}-opt-${i}`"
        :key="r.stock.ts_code"
        role="option"
        :aria-selected="i === activeIndex"
        class="result-item"
        :class="{ active: i === activeIndex, selected: r.stock.ts_code === modelValue }"
        @mousedown.prevent
        @click="select(r.stock)"
        @mouseenter="activeIndex = i"
      >
        <span class="code num">{{ r.stock.ts_code }}</span>
        <span class="name">{{ r.stock.name }}</span>
        <span class="meta muted">{{ r.stock.industry }}</span>
        <Icon v-if="r.stock.ts_code === modelValue" name="check" class="check" aria-hidden="true" />
      </li>
    </ul>
    <div v-if="open && !results.length" class="no-result muted">未找到匹配「{{ query.trim() }}」的股票</div>
    <span class="visually-hidden" role="status" aria-live="polite">{{ open ? `共 ${results.length} 个结果` : '' }}</span>
  </div>
</template>

<script setup>
/**
 * 股票近似搜索 combobox：文本输入 + 打分排序下拉（算法见 utils/stockSearch.js）。
 * - 输入框静止时刻精确回显当前选中（"代码 · 名称"）；聚焦/点击时清空未修改的回显，展示默认列表
 * - 关闭（Esc/Tab/点击外部/选中）一律还原回显，不提供清空（父组件依赖恒有值）
 * - IME 三重防护：v-model 组合期冻结 + compositionend 刷新 + keydown isComposing/229 早退
 * - 下拉为 absolute 定位；若未来父容器加 overflow:hidden 需改用 Teleport
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { buildSearchIndex, searchStocks, formatStockLabel } from '../utils/stockSearch.js'

const props = defineProps({
  stocks: { type: Array, default: () => [] },
  modelValue: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  limit: { type: Number, default: 50 },
  id: { type: String, default: 'stock-search' },
  placeholder: { type: String, default: '代码 / 名称 / 行业，如 600519、茅台、白酒' },
})
const emit = defineEmits(['update:modelValue'])

const query = ref('')
const open = ref(false)
const activeIndex = ref(-1)
const composing = ref(false)
const rootEl = ref(null)
const inputEl = ref(null)
const listEl = ref(null)

const listId = computed(() => `${props.id}-listbox`)
const index = computed(() => buildSearchIndex(props.stocks))
const results = computed(() => searchStocks(index.value, query.value, { limit: props.limit }))
const selected = computed(() => props.stocks.find((s) => s.ts_code === props.modelValue) || null)
const activeId = computed(() =>
  open.value && activeIndex.value >= 0 && results.value[activeIndex.value]
    ? `${props.id}-opt-${activeIndex.value}` : undefined)

// 外部 modelValue / 池变化时同步回显（默认选中 000001.SZ、loadPool 清空等）；打开态不干扰输入
watch([selected, () => props.stocks], () => {
  if (!open.value) query.value = formatStockLabel(selected.value)
}, { immediate: true })

watch(() => props.disabled, (v) => { if (v) closeList() })

// 聚焦/点击时：若文本仍是未修改的回显标签则清空，展示默认可浏览列表；
// 已输入内容不受影响。@click 覆盖"已聚焦再点击"（focus 事件不会重发）。
function reveal() {
  if (props.disabled) return
  if (query.value === formatStockLabel(selected.value)) query.value = ''
  open.value = true
  activeIndex.value = -1
}

function onFocus() {
  reveal()
  inputEl.value?.setSelectionRange(0, query.value.length)
}

function onInput() {
  if (composing.value) return // IME 拼写中不过滤，避免列表抖动
  open.value = true
  activeIndex.value = -1
}

function onCompositionEnd() {
  composing.value = false
  open.value = true
  activeIndex.value = -1
}

function closeList() {
  open.value = false
  activeIndex.value = -1
  query.value = formatStockLabel(selected.value)
}

function select(stock) {
  emit('update:modelValue', stock.ts_code)
  query.value = formatStockLabel(stock)
  open.value = false
  activeIndex.value = -1
  inputEl.value?.focus()
}

function scrollActive() {
  nextTick(() => listEl.value?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }))
}

function onKeydown(e) {
  if (e.isComposing || e.keyCode === 229) return // IME 确认候选的 Enter 绝不触发选中
  switch (e.key) {
    case 'ArrowDown':
      e.preventDefault()
      if (!open.value) { onFocus(); break }
      activeIndex.value = Math.min(activeIndex.value + 1, results.value.length - 1)
      scrollActive()
      break
    case 'ArrowUp':
      e.preventDefault()
      if (open.value) {
        activeIndex.value = Math.max(activeIndex.value - 1, 0)
        scrollActive()
      }
      break
    case 'Enter':
      if (open.value && activeIndex.value >= 0 && results.value[activeIndex.value]) {
        e.preventDefault()
        select(results.value[activeIndex.value].stock)
      }
      break
    case 'Escape':
      if (open.value) { e.preventDefault(); closeList() }
      break
    case 'Tab':
      closeList()
      break
  }
}

// 点击外部关闭：pointerdown capture（兼顾触摸）；open 变化时挂/卸，卸载兜底
function onDocPointerDown(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) closeList()
}
watch(open, (v) => {
  if (v) document.addEventListener('pointerdown', onDocPointerDown, true)
  else document.removeEventListener('pointerdown', onDocPointerDown, true)
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocPointerDown, true))
</script>

<style scoped>
.stock-search {
  position: relative;
}

.input-wrap {
  position: relative;
}

.input-wrap input {
  width: 100%;
  padding-left: 30px;
  box-sizing: border-box;
}

.search-icon {
  position: absolute;
  left: 9px;
  top: 50%;
  transform: translateY(-50%);
  width: 14px;
  height: 14px;
  color: var(--text-3);
  pointer-events: none;
}

.result-list,
.no-result {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 30;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow);
}

.result-list {
  max-height: 320px;
  overflow-y: auto;
  padding: 4px;
  margin: 0;
  list-style: none;
}

.result-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-1);
}

.result-item.active {
  background: var(--accent-soft);
}

.result-item.selected .name {
  color: var(--accent);
  font-weight: 600;
}

.code {
  color: var(--text-3);
  font-size: 12px;
}

.meta {
  margin-left: auto;
  font-size: 11.5px;
  white-space: nowrap;
}

.check {
  width: 14px;
  height: 14px;
  color: var(--accent);
  flex: none;
  align-self: center;
}

.no-result {
  padding: 10px 12px;
  font-size: 12.5px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
  border: 0;
}
</style>
