<script setup lang="ts">
type Counts = Record<'pending' | 'in_progress' | 'verified' | 'failed' | 'uncertain', number>
type Job = {
  id: number
  executor: string
  created_at: string
  source_eqp: string
  class_name: string
  collection: { state: 'waiting' | 'collecting' | 'done' | 'blocked', reason: string | null }
  manifest: { file_count: number, total_bytes: number }
  target_count: number
  recipe_count: number
  file_counts: Counts
}
type Detail = Job & { targets: { eqp: string }[], recipes: { idw: string, idp: string }[] }

const opened = defineModel<number | null>({ required: true })

const COLLECTION = { waiting: '대기', collecting: '수집 중', done: '완료', blocked: '차단' } as const
const POLL_MS = 3000

const { data: list, error: listError, refresh: refreshList } = await useFetch<{ jobs: Job[] }>('/api/jobs')
const { data: detail, error: detailError, refresh: refreshDetail } = await useAsyncData(
  () => opened.value === null ? Promise.resolve(null) : $fetch<Detail>(`/api/jobs/${opened.value}`),
  { watch: [opened] }
)

// 끝난 job 은 갱신하지 않습니다. 지금은 수집이 차단된 job 만 끝난 것입니다(전송은 티켓 05).
const finished = (j: Job) => j.collection.state === 'blocked'
  || (j.collection.state === 'done' && j.file_counts.pending + j.file_counts.in_progress === 0)

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => {
    refreshList()
    if (detail.value && !finished(detail.value)) refreshDetail()
  }, POLL_MS)
})
onUnmounted(() => clearInterval(timer))

const collectionText = (j: Job) => COLLECTION[j.collection.state] + (j.collection.reason ? ` (${j.collection.reason})` : '')
</script>

<template>
  <section class="dashboard-surface flex flex-col gap-4 rounded-[var(--sk-r-card)] p-6">
    <h2 class="sk-heading">
      작업 목록
    </h2>
    <p
      v-if="listError"
      class="sk-body text-(--sk-bad)"
    >
      작업 목록을 불러오지 못했습니다. {{ listError.data?.error?.message ?? listError.message }}
    </p>
    <p
      v-else-if="list && list.jobs.length === 0"
      class="sk-body text-(--sk-ink-muted)"
    >
      아직 job이 없습니다.
    </p>
    <table
      v-else-if="list"
      class="w-full text-sm"
    >
      <thead>
        <tr class="sk-label border-b border-(--sk-border) text-left">
          <th class="py-2">
            #
          </th>
          <th>실행자</th>
          <th>원본</th>
          <th>class</th>
          <th>레시피</th>
          <th>대상</th>
          <th>수집</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="j in list.jobs"
          :key="j.id"
          class="cursor-pointer border-b border-(--sk-border-soft) hover:bg-(--sk-muted-surface)"
          :class="{ 'bg-(--sk-muted-surface) font-semibold': j.id === opened }"
          @click="opened = j.id"
        >
          <td class="py-2 font-mono tabular-nums text-(--sk-ink)">
            <button
              type="button"
              class="cursor-pointer"
              @click.stop="opened = j.id"
            >
              {{ j.id }}
            </button>
          </td>
          <td class="text-(--sk-ink)">
            {{ j.executor }}
          </td>
          <td class="font-mono text-(--sk-ink)">
            {{ j.source_eqp }}
          </td>
          <td class="font-mono text-(--sk-ink)">
            {{ j.class_name }}
          </td>
          <td class="tabular-nums text-(--sk-ink)">
            {{ j.recipe_count }}
          </td>
          <td class="tabular-nums text-(--sk-ink)">
            {{ j.target_count }}
          </td>
          <td :class="j.collection.state === 'blocked' ? 'text-(--sk-bad)' : 'text-(--sk-ink)'">
            {{ collectionText(j) }}
          </td>
        </tr>
      </tbody>
    </table>

    <div
      v-if="opened !== null"
      class="flex flex-col gap-2 border-t border-(--sk-border) pt-4"
    >
      <p
        v-if="detailError"
        class="sk-body text-(--sk-bad)"
      >
        job {{ opened }}을 불러오지 못했습니다. {{ detailError.data?.error?.message ?? detailError.message }}
      </p>
      <template v-else-if="detail">
        <h3 class="sk-heading">
          job {{ detail.id }}
        </h3>
        <p class="sk-body text-(--sk-ink)">
          {{ detail.executor }} · {{ detail.created_at.slice(0, 16).replace('T', ' ') }} ·
          {{ detail.source_eqp }} / {{ detail.class_name }}
        </p>
        <p
          class="sk-body"
          :class="detail.collection.state === 'blocked' ? 'text-(--sk-bad)' : 'text-(--sk-ink)'"
        >
          수집: {{ collectionText(detail) }}
          <template v-if="detail.collection.state === 'done'">
            · 파일 {{ detail.manifest.file_count }}개, {{ detail.manifest.total_bytes }} B
          </template>
        </p>
        <p class="sk-body text-(--sk-ink)">
          대상: <span class="font-mono">{{ detail.targets.map(t => t.eqp).join(', ') }}</span>
        </p>
        <p class="sk-body text-(--sk-ink)">
          레시피: <span class="font-mono">{{ detail.recipes.map(r => `${r.idw}/${r.idp}`).join(', ') }}</span>
        </p>
        <p class="sk-meta">
          file 대기 {{ detail.file_counts.pending }} · 진행 {{ detail.file_counts.in_progress }} ·
          검증 {{ detail.file_counts.verified }} · 실패 {{ detail.file_counts.failed }} ·
          불명확 {{ detail.file_counts.uncertain }}
        </p>
      </template>
    </div>
  </section>
</template>
