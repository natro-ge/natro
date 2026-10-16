import { svelte } from '@sveltejs/vite-plugin-svelte'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const page = (name: string) => fileURLToPath(new URL(name, import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  // Three pages, each its own window: the dock (index.html), the reply box (reply.html) and the chat (chat.html).
  build: {
    rollupOptions: {
      input: { dock: page('index.html'), reply: page('reply.html'), chat: page('chat.html') },
    },
  },
})
