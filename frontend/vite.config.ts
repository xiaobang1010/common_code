import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
// Tailwind v4 官方 Vite 插件：编译 index.css 里的 tailwind 模块化导入与 @source 扫描
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // 后端端口由 Electron 动态分配，浏览器开发时用 API_PORT 指定
      '/api': `http://localhost:${process.env.API_PORT || '8000'}`
    }
  }
})
