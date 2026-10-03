import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.CONSUMER_API_TARGET || 'http://127.0.0.1:8080'
  const marketTarget = env.MARKET_API_TARGET || 'http://127.0.0.1:8083'
  return {
  plugins: [vue()],
  server: {
    port: 5173,
    open: false,
    proxy: {
      '/api/consumer': { target, changeOrigin: true },
      '/actuator/health': { target, changeOrigin: true },
      '^/api/analysis/stocks(?:\\?|$)': { target: marketTarget, changeOrigin: true },
      '^/api/analysis/(?:kline|indicators)/[0-9]{6}\\.(?:SZ|SH|BJ)(?:\\?|$)': { target: marketTarget, changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
  }
})
