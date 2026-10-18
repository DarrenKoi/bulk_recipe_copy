<script setup lang="ts">
type Recipe = { idw: string, idp: string }
type Pair = Recipe & { eqp: string }
type Checked = { eqp: string, fab_name: string, class_missing: boolean, class_exists_other_fab: boolean, recipe_exists: Recipe[], idw_exists?: string[] }
type Failed = { eqp: string, fab_name: string, error: { code: string, message: string } }
export type Preflight = { source_fab: string, targets: (Checked | Failed)[] }
export type Choice = { pairs: Pair[], overwrite: Pair[], create_class: string[] }

const props = defineProps<{ eqp: string, cls: string, recipes: Recipe[], includeIdw: boolean, check: Preflight, sending: boolean, only?: string[] }>()
const emit = defineEmits<{ confirm: [choice: Choice] }>()
const open = defineModel<boolean>('open', { required: true })

// 기본은 모두 "아니오"입니다. 폴더 생성과 덮어쓰기는 실행자가 직접 켜야 합니다.
const createClass = ref<string[]>([])
const overwrite = ref<string[]>([])
const key = (eqp: string, r: Recipe) => `${eqp}/${r.idw}/${r.idp}`
const recipeExists = (t: Checked, r: Recipe) => t.recipe_exists.some(e => e.idw === r.idw && e.idp === r.idp)
const idwExists = (t: Checked, r: Recipe) => (t.idw_exists ?? []).includes(r.idw)
// 대상에 `.idw` 가 이미 있으면 그것을 쓰는 레시피도 덮어쓰기 허락이 필요합니다(스펙 "사전 점검과 확인").
const exists = (t: Checked, r: Recipe) => recipeExists(t, r) || idwExists(t, r)
// 허락 하나가 그 쌍의 `.idp`·raw 와 공유 `.idw` 를 함께 덮으므로, `.idw` 가 있으면 늘 이름을 밝힙니다.
const idwNote = (t: Checked, r: Recipe) => !idwExists(t, r) ? '' : `${recipeExists(t, r) ? '와 그것이' : '가'} 쓰는 ${r.idw}.idw`
// 재시도(`only`, 키 eqp/idw/idp): 그 job 에 남았던 쌍만 보냅니다. `only` 에 없는 장비는 평소처럼 모든 레시피.
const wanted = (eqp: string, r: Recipe) => {
  const mine = props.only?.filter(k => k.startsWith(`${eqp}/`))
  return !mine?.length || mine.includes(key(eqp, r))
}

const readable = computed(() => props.check.targets.filter((t): t is Checked => !('error' in t)))
const unreadable = computed(() => props.check.targets.filter((t): t is Failed => 'error' in t))

// 읽지 못한 대상, 폴더 생성을 거절한 대상, 덮어쓰기를 허락하지 않은 쌍은 job 에서 빠집니다.
const pairs = computed<Pair[]>(() => readable.value
  .filter(t => !t.class_missing || createClass.value.includes(t.eqp))
  .flatMap(t => props.recipes
    .filter(r => wanted(t.eqp, r) && (!exists(t, r) || overwrite.value.includes(key(t.eqp, r))))
    .map(r => ({ eqp: t.eqp, ...r }))))

// 파일 수·크기는 수집 전이라 서버가 모릅니다. 고른 레시피마다 원본의 요약 API(raw 폴더 + .idp 크기)로 셉니다.
type Summary = { file_count: number, total_bytes: number, idp_bytes: number | null }
// 레시피 키 -> 요약, 실패 사유(string), 또는 아직 없음(undefined)
const summaries = ref<Record<string, Summary | string>>({})
const rkey = (r: Recipe) => `${r.idw}/${r.idp}`
const api = (...parts: string[]) => `/api/tools/${[props.eqp, ...parts].map(encodeURIComponent).join('/')}`
function load(r: Recipe) {
  delete summaries.value[rkey(r)]
  $fetch<Summary>(api('classes', props.cls, 'recipes', r.idw, r.idp))
    .then((s) => {
      summaries.value[rkey(r)] = s.idp_bytes === null ? '원본에서 .idp 를 찾지 못했습니다.' : s
    })
    .catch((e) => { summaries.value[rkey(r)] = e?.data?.error?.message ?? e?.message ?? '조회 실패' })
}
onMounted(() => props.recipes.forEach(load))

const needed = computed(() => props.recipes.filter(r => pairs.value.some(p => rkey(p) === rkey(r))))
const failed = computed(() => needed.value.filter(r => typeof summaries.value[rkey(r)] === 'string'))
// 남은 쌍의 요약이 모두 와야 실행할 수 있습니다.
const ready = computed(() => needed.value.every(r => typeof summaries.value[rkey(r)] === 'object'))

const scale = computed(() => {
  const all = pairs.value.map(p => summaries.value[rkey(p)] as Summary)
  return {
    tools: new Set(pairs.value.map(p => p.eqp)).size,
    recipes: needed.value.length,
    // raw 폴더 파일 + .idp 1개, idw 포함이면 대상마다 idw 하나에 .idw 1개
    files: ready.value
      ? all.reduce((n, s) => n + s.file_count + 1, 0)
        + (props.includeIdw ? new Set(pairs.value.map(p => `${p.eqp}/${p.idw}`)).size : 0)
      : null,
    bytes: ready.value ? all.reduce((n, s) => n + s.total_bytes + s.idp_bytes!, 0) : null
  }
})

function size(n: number) {
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i++
  }
  return `${i ? n.toFixed(1) : n} ${units[i]}`
}

function confirm() {
  emit('confirm', {
    pairs: pairs.value,
    overwrite: pairs.value.filter(p => overwrite.value.includes(key(p.eqp, p))),
    create_class: createClass.value.filter(e => pairs.value.some(p => p.eqp === e))
  })
}
</script>

<template>
  <UModal
    v-model:open="open"
    title="실행 전 확인"
    :description="`${eqp} · ${cls} → 대상 ${check.targets.length}대`"
    :ui="{ content: 'max-w-3xl' }"
  >
    <template #body>
      <div class="flex flex-col gap-4">
        <div
          v-for="t in readable"
          :key="t.eqp"
          class="flex flex-col gap-2 border-b border-(--sk-border-soft) pb-3"
        >
          <div class="flex items-baseline gap-2">
            <span class="font-mono text-(--sk-ink)">{{ t.eqp }}</span>
            <span class="sk-meta">{{ t.fab_name }}</span>
            <span
              v-if="!t.class_missing && !recipes.some(r => wanted(t.eqp, r) && exists(t, r))"
              class="sk-meta ml-auto"
            >그대로 진행</span>
          </div>
          <label
            v-if="t.class_missing"
            class="sk-body flex items-center gap-2 text-(--sk-ink)"
          >
            <input
              v-model="createClass"
              type="checkbox"
              :value="t.eqp"
            >
            class 폴더 {{ cls }} 가 없습니다. 만들까요? 만들지 않으면 이 장비는 이번 job에서 빠집니다.
          </label>
          <p
            v-if="t.class_exists_other_fab"
            class="sk-body text-(--sk-warn)"
          >
            다른 fab({{ t.fab_name }})에 같은 class {{ cls }} 가 있습니다. 이름만 같은 다른 공정이 아닌지 주의하세요.
          </p>
          <label
            v-for="r in recipes.filter(r => wanted(t.eqp, r) && exists(t, r))"
            :key="key(t.eqp, r)"
            class="sk-body flex items-center gap-2 text-(--sk-ink)"
          >
            <input
              v-model="overwrite"
              type="checkbox"
              :value="key(t.eqp, r)"
            >
            <span class="font-mono">{{ cls }}/{{ r.idw }}/{{ r.idp }}</span>
            {{ idwNote(t, r) }} 이(가) 이미 있습니다. 덮어쓰기
            (허락하지 않으면 이 레시피는 이 장비에서 빠집니다)
          </label>
        </div>
        <p
          v-for="r in failed"
          :key="rkey(r)"
          class="sk-body flex items-center gap-2 text-(--sk-bad)"
        >
          <span><span class="font-mono">{{ cls }}/{{ rkey(r) }}</span> 의 파일 수·크기를 읽지 못했습니다. {{ summaries[rkey(r)] }}</span>
          <UButton
            size="xs"
            color="neutral"
            variant="outline"
            icon="i-lucide-rotate-cw"
            @click="load(r)"
          >
            다시 조회
          </UButton>
        </p>
        <p
          v-for="t in unreadable"
          :key="t.eqp"
          class="sk-body text-(--sk-bad)"
        >
          <span class="font-mono">{{ t.eqp }}</span> 을(를) 읽지 못해 이번 job에서 뺍니다. {{ t.error.message }}
        </p>
      </div>
    </template>
    <template #footer>
      <div class="flex w-full flex-wrap items-center gap-3">
        <span class="sk-meta">
          장비 {{ scale.tools }}대 · 레시피 {{ scale.recipes }}개 ·
          파일 {{ scale.files ?? '?' }}개 · 총 {{ scale.bytes === null ? '?' : size(scale.bytes) }}{{ includeIdw ? ' + .idw' : '' }}
        </span>
        <UButton
          class="ml-auto"
          color="neutral"
          variant="ghost"
          @click="open = false"
        >
          취소
        </UButton>
        <UButton
          :disabled="!pairs.length || !ready"
          :loading="sending"
          icon="i-lucide-play"
          @click="confirm"
        >
          실행
        </UButton>
      </div>
    </template>
  </UModal>
</template>
