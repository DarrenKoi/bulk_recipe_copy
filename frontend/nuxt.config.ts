// Copied from skewnono_v3_nuxt frontend/nuxt.config.ts, trimmed.
const apiTarget = import.meta.env.NUXT_API_TARGET || 'http://localhost:5050'

export default defineNuxtConfig({
  modules: ['@nuxt/ui'],

  // Flask serves the generated SPA; there is no Node server.
  ssr: false,

  app: {
    head: {
      title: '레시피 횡전개',
      htmlAttrs: { lang: 'ko' }
    }
  },

  css: ['~/assets/css/main.css'],

  // Fonts are self-hosted under public/fonts; never contact a font CDN.
  ui: { fonts: false },

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
