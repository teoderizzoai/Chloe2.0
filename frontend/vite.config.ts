import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  build: { outDir: '../static/ui', emptyOutDir: true },
  server: {
    proxy: {
      '/v1': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/metrics': 'http://localhost:8000',
    },
  },
})
