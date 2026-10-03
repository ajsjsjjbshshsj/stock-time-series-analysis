import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.CONSUMER_API_TARGET || 'http://127.0.0.1:8080'
  return {
  plugins: [vue()],
  server: {
    port: 5173,
    open: false,
    proxy: {
      '/api/consumer': { target, changeOrigin: true },
      '/actuator/health': { target, changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
  }
})
