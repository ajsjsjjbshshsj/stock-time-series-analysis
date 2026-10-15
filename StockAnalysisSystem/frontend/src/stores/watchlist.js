import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { STOCK_CODE } from '../utils/market.js'
import { authState } from './auth.js'

const STORAGE_KEY = 'sas-watchlist'
const MAX_ITEMS = 20

function storageKey() {
  return authState.mode === 'protected' ? (authState.user?.id ? `${STORAGE_KEY}:${authState.user.id}` : null) : STORAGE_KEY
}

function load() {
  try {
    if (typeof localStorage === 'undefined' || !storageKey()) return []
    const arr = JSON.parse(localStorage.getItem(storageKey()) ?? '[]')
    if (!Array.isArray(arr)) return []
    return [...new Set(arr.filter((c) => typeof c === 'string' && STOCK_CODE.test(c)))].slice(0, MAX_ITEMS)
  } catch (e) {
    return [] // 隐私模式/脏数据：从空自选开始
  }
}

/**
 * 自选股收藏（纯本地）：代码列表存 localStorage，不上传后端。
 * 上限 MAX_ITEMS 只，超限淘汰最早收藏。
 */
export const useWatchlistStore = defineStore('watchlist', () => {
  const codes = ref(load())
  watch(storageKey, () => { codes.value = load() }, { flush: 'sync' })

  function persist() {
    try {
      if (storageKey()) localStorage.setItem(storageKey(), JSON.stringify(codes.value))
    } catch (e) {
      /* 忽略隐私模式下的存储失败，收藏仅当前会话生效 */
    }
  }

  function has(code) {
    return codes.value.includes(code)
  }

  function add(code) {
    if (typeof code !== 'string' || !STOCK_CODE.test(code) || has(code)) return
    codes.value.push(code)
    if (codes.value.length > MAX_ITEMS) codes.value.shift()
    persist()
  }

  function remove(code) {
    const i = codes.value.indexOf(code)
    if (i >= 0) {
      codes.value.splice(i, 1)
      persist()
    }
  }

  function toggle(code) {
    if (has(code)) remove(code)
    else add(code)
  }

  return { codes, has, add, remove, toggle }
})
