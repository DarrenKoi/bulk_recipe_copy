// Copied from skewnono_v3_nuxt frontend/nuxt.config.ts, trimmed.
const apiTarget = import.meta.env.NUXT_API_TARGET || 'http://localhost:5060'
const portFromEnv = Number.parseInt(import.meta.env.NUXT_PORT || '', 10)

export default defineNuxtConfig({
  modules: ['@nuxt/ui'],

  // Flask serves the generated SPA; there is no Node server.
  ssr: false,

  app: {
    head: {
      title: '레시피 횡전개',
      htmlAttrs: { lang: 'ko' },
      // SKEWNONO inlines the SVG as a data: URI because its URL changes per
      // click; this app has no routing, so a plain file link is enough.
      link: [
        { rel: 'icon', type: 'image/svg+xml', href: '/favicon/favicon.svg' },
        { rel: 'icon', href: '/favicon/favicon.ico', sizes: 'any' },
        { rel: 'apple-touch-icon', sizes: '180x180', href: '/favicon/apple-touch-icon.png' },
        { rel: 'manifest', href: '/favicon/site.webmanifest' }
      ],
      meta: [{ name: 'theme-color', content: '#f0eee9' }]
    }
  },

  css: ['~/assets/css/main.css'],

  // Fonts are self-hosted under public/fonts; never contact a font CDN.
  ui: { fonts: false },

  // SKEWNONO uses 3000 (and Flask 5050); keep both apps runnable side by side.
  devServer: {
    port: Number.isFinite(portFromEnv) ? portFromEnv : 3060
  },

  compatibilityDate: '2025-01-15',

  nitro: {
    devProxy: {
      // h3 strips the '/api' mount prefix, so it must live inside the target.
      '/api': { target: `${apiTarget.replace(/\/$/, '')}/api`, changeOrigin: true }
    }
  },

  // The office network blocks the Iconify API, so icons must be bundled.
  icon: {
    fallbackToApi: false,
    clientBundle: {
      scan: {
        globInclude: ['app/**/*.{vue,ts,js}', 'node_modules/@nuxt/ui/dist/**/*.{vue,js,mjs}'],
        globExclude: ['node_modules/**/node_modules/**']
      },
      sizeLimitKb: 2048
    }
  }
})
