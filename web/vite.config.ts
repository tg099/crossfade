import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			adapter: adapter({ pages: 'build', assets: 'build', fallback: 'index.html', precompress: false }),
			version: { pollInterval: 0 }
		})
	],
	server: {
		port: 5173,
		proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false } }
	}
});
