import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/app/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/chat': 'http://localhost:8000',
      '/chunk': 'http://localhost:8000',
      '/feedback': 'http://localhost:8000',
      '/validate': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
});

