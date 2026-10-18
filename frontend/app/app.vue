<script setup lang="ts">
import type { Retry } from '~/components/JobList.vue'

type Me = { user: string, allowed: boolean }

const { data: me, error } = await useFetch<Me>('/api/me')
const source = ref<string | null>(null)
const cls = ref<string | undefined>()
const picked = ref<string[]>([])
const openedJob = ref<number | null>(null)
const retry = ref<Retry | null>(null)

watch(source, () => {
  cls.value = undefined
  picked.value = []
})
watch([source, cls], () => {
  if (retry.value && (source.value !== retry.value.source_eqp || cls.value !== retry.value.class_name)) retry.value = null
})

// 재시도: 원본·class·레시피를 복원합니다. 위 watch 와 RecipeBrowser 의 watch 가 하위 선택을
// 비우므로 한 단계씩 tick 을 넘기며 채웁니다.
async function startRetry(r: Retry) {
  retry.value = null
  source.value = r.source_eqp
  await nextTick()
  cls.value = r.class_name
  await nextTick()
  picked.value = [...new Set(r.pairs.map(p => `${p.idw}/${p.idp}`))]
  retry.value = r
}

// 401: LASTUSER 쿠키 없음, 그 외 오류: 서버에 닿지 않음, allowed=false: 허용 목록 밖.
const blocked = computed(() => {
  if (error.value?.statusCode === 401) {
    return { title: '사용자를 확인할 수 없습니다', body: '사내 신원 쿠키(LASTUSER)가 없습니다. 사내 포털에 로그인한 브라우저에서 다시 열어 주세요.' }
  }
  if (error.value) {
    return { title: '서버에 연결하지 못했습니다', body: error.value.message }
  }
  if (me.value && !me.value.allowed) {
    return { title: '접근 권한이 없습니다', body: `${me.value.user} 님은 허용 목록에 없습니다. 앱 운영자에게 허용 목록 추가를 요청해 주세요.` }
  }
  return null
})
</script>

<template>
  <UApp>
    <div class="dashboard-bg-layer" />
    <main class="mx-auto flex max-w-[1280px] flex-col gap-6 px-6 py-8">
      <header class="flex items-baseline gap-3">
        <span class="sk-eyebrow">HITACHI CD-SEM</span>
        <h1 class="sk-page-title">
          레시피 횡전개
        </h1>
        <span
          v-if="me"
          class="sk-meta ml-auto"
        >{{ me.user }}</span>
      </header>

      <section
        v-if="blocked"
        class="dashboard-surface mx-auto mt-16 w-full max-w-md rounded-[var(--sk-r-card)] p-8 text-center"
      >
        <UIcon
          name="i-lucide-shield-x"
          class="size-8 text-(--sk-bad)"
        />
        <h2 class="sk-heading mt-3">
          {{ blocked.title }}
        </h2>
        <p class="sk-body mt-2 text-(--sk-ink-muted)">
          {{ blocked.body }}
        </p>
      </section>

      <template v-else-if="me">
        <ToolPicker v-model="source" />
        <!-- key: 장비를 바꾸면 class·필터·열린 행을 모두 새로 시작합니다. -->
        <RecipeBrowser
          v-if="source"
          :key="source"
          v-model:cls="cls"
          v-model:picked="picked"
          :eqp="source"
        />
        <JobLauncher
          v-if="source && cls && picked.length"
          :eqp="source"
          :cls="cls"
          :picked="picked"
          :retry="retry"
          @created="openedJob = $event; retry = null"
          @clear-retry="retry = null"
        />
        <JobList
          v-model="openedJob"
          :user="me.user"
          @retry="startRetry"
        />
      </template>
    </main>
  </UApp>
</template>
