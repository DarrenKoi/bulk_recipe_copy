<script setup lang="ts">
import type { Choice, Preflight } from './ConfirmDialog.vue'
import type { Retry } from './JobList.vue'

type Tool = { eqp_id: string, fab_name: string, model: string }

const props = defineProps<{ eqp: string, cls: string, picked: string[], retry?: Retry | null }>()
const emit = defineEmits<{ 'created': [id: number], 'clear-retry': [] }>()

const targets = ref<string[]>([])
// 공유 파일을 모르고 덮지 않도록 기본은 끕니다.
const includeIdw = ref(false)
const only = computed(() => props.retry?.pairs.map(p => `${p.eqp}/${p.idw}/${p.idp}`))
// 재시도를 고르면 그 job 에 남았던 대상과 `.idw` 포함 여부를 미리 고릅니다. 사용자는 빼거나 더할 수 있습니다.
watch(() => props.retry, (r) => {
  if (!r) return
  targets.value = [...new Set(r.pairs.map(p => p.eqp))]
  includeIdw.value = r.include_idw
}, { immediate: true })
const query = ref('')
const sending = ref(false)
const failure = ref<string | null>(null)

const { data, error } = await useFetch<{ tools: Tool[] }>('/api/tools')

// 원본은 대상이 될 수 없습니다. 다른 fab 장비도 고를 수 있습니다.
const shown = computed(() => {
  const q = query.value.trim().toUpperCase()
  return (data.value?.tools ?? []).filter(t => t.eqp_id !== props.eqp && t.eqp_id.toUpperCase().includes(q))
})

const recipes = computed(() => props.picked.map((rid) => {
  const [idw, idp] = rid.split('/')
  return { idw: idw!, idp: idp! }
}))
type Selection = { source_eqp: string, class_name: string, recipes: { idw: string, idp: string }[], targets: string[], include_idw: boolean }
// 점검을 시작할 때 선택을 복사해 고정합니다. 점검·팝업·job 생성이 모두 이 사본을 씁니다.
const frozen = shallowRef<Selection | null>(null) // shallow: 아래 `!== sel` 비교가 프록시가 아닌 원본끼리 되도록
const check = ref<Preflight | null>(null)
const confirming = ref(false)
const reason = (e: any) => e?.data?.error?.message ?? e?.message

// 점검 중이거나 팝업이 열린 동안 선택이 바뀌면 그 점검 결과는 버리고 다시 점검하게 합니다.
// 문자열로 비교합니다. 부모가 같은 내용의 새 배열을 내려줘도 바뀐 것으로 보지 않게.
watch(() => JSON.stringify([props.eqp, props.cls, props.picked, targets.value, includeIdw.value, props.retry?.job]), () => {
  if (!frozen.value || !(sending.value || confirming.value)) return
  frozen.value = null
  check.value = null
  confirming.value = false
  failure.value = '선택이 바뀌어 사전 점검을 다시 해야 합니다.'
})

// 실행은 사전 점검 → 확인 팝업 → job 생성입니다. 팝업의 선택을 그대로 보냅니다.
async function run() {
  const sel: Selection = { source_eqp: props.eqp, class_name: props.cls, recipes: recipes.value.map(r => ({ ...r })), targets: [...targets.value], include_idw: includeIdw.value }
  frozen.value = sel
  check.value = null
  sending.value = true
  failure.value = null
  try {
    const result = await $fetch<Preflight>('/api/preflight', { method: 'POST', body: sel })
    if (frozen.value !== sel) return // 대기 중 선택이 바뀜
    check.value = result
    confirming.value = true
  } catch (e) {
    if (frozen.value !== sel) return
    frozen.value = null
    failure.value = `사전 점검을 하지 못했습니다. ${reason(e)}`
  } finally {
    sending.value = false
  }
}

async function create(choice: Choice) {
  const sel = frozen.value
  if (!sel) return
  sending.value = true
  try {
    const job = await $fetch<{ id: number }>('/api/jobs', { method: 'POST', body: { ...sel, ...choice } })
    frozen.value = null
    failure.value = null
    confirming.value = false
    targets.value = []
    emit('created', job.id)
  } catch (e) {
    frozen.value = null
    confirming.value = false
    failure.value = `job을 만들지 못했습니다. ${reason(e)}`
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <section class="dashboard-surface flex flex-col gap-4 rounded-[var(--sk-r-card)] p-6">
    <div class="flex flex-wrap items-center gap-3">
      <h2 class="sk-heading">
        대상 장비
      </h2>
      <span class="sk-meta">레시피 {{ picked.length }}개 · 대상 {{ targets.length }}대</span>
      <UInput
        v-model="query"
        icon="i-lucide-search"
        placeholder="eqp_id 일부"
        class="ml-auto w-64"
      />
    </div>

    <p
      v-if="error"
      class="sk-body text-(--sk-bad)"
    >
      장비 목록을 불러오지 못했습니다. {{ error.data?.error?.message ?? error.message }}
    </p>
    <table
      v-else
      class="w-full text-sm"
    >
      <thead>
        <tr class="sk-label border-b border-(--sk-border) text-left">
          <th class="py-2">
            eqp_id
          </th>
          <th>fab</th>
          <th>모델</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="t in shown"
          :key="t.eqp_id"
          class="border-b border-(--sk-border-soft) hover:bg-(--sk-muted-surface)"
        >
          <td class="py-2 font-mono tabular-nums text-(--sk-ink)">
            <label class="flex cursor-pointer items-center gap-2">
              <input
                v-model="targets"
                type="checkbox"
                :value="t.eqp_id"
              >
              {{ t.eqp_id }}
            </label>
          </td>
          <td class="text-(--sk-ink)">
            {{ t.fab_name }}
          </td>
          <td class="font-mono text-(--sk-ink)">
            {{ t.model }}
          </td>
        </tr>
      </tbody>
    </table>

    <label class="sk-body flex items-center gap-2 text-(--sk-ink)">
      <input
        v-model="includeIdw"
        type="checkbox"
      >
      .idw 포함
      <span
        v-if="includeIdw"
        class="text-(--sk-warn)"
      >같은 idw를 쓰는 다른 레시피도 영향받음</span>
    </label>

    <p
      v-if="retry"
      class="sk-body flex flex-wrap items-center gap-2 text-(--sk-ink)"
    >
      job {{ retry.job }} 재시도: 미리 고른 대상에는 그 job에 남았던 레시피만 보냅니다.
      <UButton
        size="xs"
        color="neutral"
        variant="outline"
        @click="emit('clear-retry')"
      >
        재시도 해제
      </UButton>
    </p>
    <div class="flex items-center gap-3">
      <UButton
        :disabled="!picked.length || !targets.length"
        :loading="sending"
        icon="i-lucide-play"
        @click="run"
      >
        실행
      </UButton>
      <span
        v-if="failure"
        class="sk-body text-(--sk-bad)"
      >{{ failure }}</span>
    </div>
    <ConfirmDialog
      v-if="frozen && check && confirming"
      v-model:open="confirming"
      :eqp="frozen.source_eqp"
      :cls="frozen.class_name"
      :recipes="frozen.recipes"
      :include-idw="frozen.include_idw"
      :check="check"
      :only="only"
      :sending="sending"
      @confirm="create"
    />
  </section>
</template>
