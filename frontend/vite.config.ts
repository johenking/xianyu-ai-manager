import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { staticRetentionPlugin } from './build/staticRetention';
import packageJson from './package.json';

export default defineConfig({
  base: '/static/',
  define: {
    __APP_VERSION__: JSON.stringify(packageJson.version),
  },
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      // 代理API请求到后端
      '/api': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      // 代理其他后端请求
      '/cookies': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/qr-login': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/password-login': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/keywords': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/keywords-with-item-id': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/default-reply': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/items': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/cards': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/delivery-rules': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/notification-channels': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/message-notifications': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/ai-reply-settings': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/system-settings': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/user-settings': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/admin': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/analytics': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/backup': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/logs': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/login': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/verify': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/logout': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/register': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/generate-captcha': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/verify-captcha': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/geetest': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/send-verification-code': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/change-password': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8091',
        changeOrigin: true,
      },
    },
  },
  plugins: [react(), staticRetentionPlugin()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  build: {
    outDir: '../static',
    sourcemap: process.env.VITE_BUILD_SOURCEMAP === 'true',
    rollupOptions: {
      output: {
        manualChunks: undefined,
      },
    },
    emptyOutDir: false,
  },
});
