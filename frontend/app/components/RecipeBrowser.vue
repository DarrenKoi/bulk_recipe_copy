<script setup lang="ts">
type Recipe = { idw: string, idp: string, modified: string | null }
type Summary = { file_count: number, total_bytes: number }

const props = defineProps<{ eqp: string }>()

const cls = ref<string | undefined>()
const query = ref('')
const newestFirst = ref(true)
const opened = ref<string | null>(null)
const summaries = ref<Record<string, Summary | { error: string } | 'loading'>>({})

const api = (...parts: string[]) => `/api/tools/${[props.eqp, ...parts].map(encodeURIComponent).join('/')}`
const reason = (e: any) => e?.data?.error?.message ?? e?.message

const { data: classData, error: classError, status: classStatus } = await useFetch<{ classes: string[] }>(() => api('classes'))

const { data: recipeData, error: recipeError, status: recipeStatus } = await useAsyncData(
  () => cls.value ? $fetch<{ recipes: Recipe[] }>(api('classes', cls.value, 'recipes')) : Promise.resolve({ recipes: [] }),
  { watch: [cls] }
)

const shown = computed(() => {
  const q = query.value.trim().toUpperCase()
  const rows = (recipeData.value?.recipes ?? []).filter(r => `${r.idw}/${r.idp}`.toUpperCase().includes(q))
  // 같은 오프셋(KST)의 ISO 문자열이라 문자열 비교가 시각 비교입니다. 시각 없는 행은 맨 뒤.
  return rows.sort((a, b) => {
    if (!a.modified || !b.modified) return a.modified ? -1 : b.modified ? 1 : 0
    const c = a.modified.localeCompare(b.modified)
    return newestFirst.value ? -c : c
  })
})

watch(cls, () => {
  opened.value = null
  summaries.value = {}
})

const rid = (r: Recipe) => `${r.idw}/${r.idp}`

function detail(r: Recipe) {
  const s = summaries.value[rid(r)]
  if (!s || s === 'loading') return { tone: 'text-(--sk-ink-muted)', text: 'raw 폴더를 읽는 중입니다.' }
  if ('error' in s) return { tone: 'text-(--sk-bad)', text: `raw 폴더를 읽지 못했습니다. ${s.error}` }
  return { tone: 'text-(--sk-ink)', text: `raw 폴더 파일 ${s.file_count}개, 총 ${size(s.total_bytes)}` }
}

async function toggle(r: Recipe) {
  const id = rid(r)
  opened.value = opened.value === id ? null : id
  const cache = summaries.value
  if (opened.value === null || cache[id]) return
  // class를 바꾸면 summaries 가 새 객체가 됩니다. 늦게 온 결과는 요청 때의 객체에만 적습니다.
  cache[id] = 'loading'
  try {
    cache[id] = await $fetch<Summary>(api('classes', cls.value!, 'recipes', r.idw, r.idp))
  } catch (e) {
    cache[id] = { error: reason(e) }
  }
}

function size(n: number) {
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i++
  }
  return `${i ? n.toFixed(1) : n} ${units[i]}`
}
</script>

<template>
  <section class="dashboard-surface flex flex-col gap-4 rounded-[var(--sk-r-card)] p-6">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="sk-heading">
        레시피
      </h2>
      <USelect
        v-model="cls"
        :items="classData?.classes ?? []"
        :loading="classStatus === 'pending'"
        placeholder="class 선택"
        aria-label="class"
        class="w-64"
      />
      <UInput
        v-if="cls"
        v-model="query"
        icon="i-lucide-search"
        placeholder="idw / idp 일부"
        class="ml-auto w-64"
      />
    </div>

    <p
      v-if="classError"
      class="sk-body text-(--sk-bad)"
    >
      {{ eqp }}의 class 목록을 읽지 못했습니다. {{ reason(classError) }}
    </p>
    <p
      v-else-if="classData && classData.classes.length === 0"
      class="sk-body text-(--sk-ink-muted)"
    >
      이 장비에는 class 폴더가 없습니다.
    </p>
    <template v-else-if="cls">
      <p
        v-if="recipeStatus === 'pending'"
        class="sk-body text-(--sk-ink-muted)"
      >
        레시피 목록을 불러오는 중입니다.
      </p>
      <p
        v-else-if="recipeError"
        class="sk-body text-(--sk-bad)"
      >
        레시피 목록을 읽지 못했습니다. {{ reason(recipeError) }}
      </p>
      <p
        v-else-if="shown.length === 0"
        class="sk-body text-(--sk-ink-muted)"
      >
        {{ query ? '맞는 레시피가 없습니다.' : '이 class에는 레시피가 없습니다.' }}
      </p>
      <table
        v-else
        class="w-full text-sm"
      >
        <thead>
          <tr class="sk-label border-b border-(--sk-border) text-left">
            <th class="py-2">
              idp
            </th>
            <th>idw</th>
            <th :aria-sort="newestFirst ? 'descending' : 'ascending'">
              <button
                type="button"
                class="flex cursor-pointer items-center gap-1"
                @click="newestFirst = !newestFirst"
              >
                수정 시각
                <UIcon :name="newestFirst ? 'i-lucide-arrow-down' : 'i-lucide-arrow-up'" />
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <template
            v-for="r in shown"
            :key="rid(r)"
          >
            <tr
              class="cursor-pointer border-b border-(--sk-border-soft) hover:bg-(--sk-muted-surface)"
              :class="{ 'bg-(--sk-muted-surface)': opened === rid(r) }"
              @click="toggle(r)"
            >
              <td class="py-2 font-mono text-(--sk-ink)">
                <button
                  type="button"
                  class="flex cursor-pointer items-center gap-2"
                  :aria-expanded="opened === rid(r)"
                  @click.stop="toggle(r)"
                >
                  <UIcon :name="opened === rid(r) ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'" />
                  {{ r.idp }}
                </button>
              </td>
              <td class="font-mono text-(--sk-ink)">
                {{ r.idw }}
              </td>
              <td class="font-mono tabular-nums text-(--sk-ink)">
                {{ r.modified ? r.modified.slice(0, 16).replace('T', ' ') : '-' }}
              </td>
            </tr>
            <tr
              v-if="opened === rid(r)"
              class="border-b border-(--sk-border-soft)"
            >
              <td
                colspan="3"
                class="sk-body py-2 pl-8"
              >
                <span :class="detail(r).tone">{{ detail(r).text }}</span>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
  </section>
</template>
