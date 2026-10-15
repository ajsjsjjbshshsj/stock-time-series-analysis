import { defineStore } from 'pinia'
import { ref } from 'vue'
import { STOCK_CODE } from '../utils/market.js'

const STORAGE_KEY = 'sas-watchlist'
const MAX_ITEMS = 20

function load() {
  try {
    if (typeof localStorage === 'undefined') return []
    const arr = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
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

  function persist() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(codes.value))
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
