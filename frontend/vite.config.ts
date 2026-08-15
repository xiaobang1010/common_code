import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 后端端口由 Electron 动态分配，浏览器开发时用 API_PORT 指定
      '/api': `http://localhost:${process.env.API_PORT || '8000'}`
    }
  }
})
