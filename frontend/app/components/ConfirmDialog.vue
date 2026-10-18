<script setup lang="ts">
type Recipe = { idw: string, idp: string }
type Pair = Recipe & { eqp: string }
type Checked = { eqp: string, fab_name: string, class_missing: boolean, class_exists_other_fab: boolean, recipe_exists: Recipe[] }
type Failed = { eqp: string, fab_name: string, error: { code: string, message: string } }
export type Preflight = { source_fab: string, targets: (Checked | Failed)[] }
export type Choice = { pairs: Pair[], overwrite: Pair[], create_class: string[] }

const props = defineProps<{ eqp: string, cls: string, recipes: Recipe[], check: Preflight, sending: boolean }>()
const emit = defineEmits<{ confirm: [choice: Choice] }>()
const open = defineModel<boolean>('open', { required: true })

// 기본은 모두 "아니오"입니다. 폴더 생성과 덮어쓰기는 실행자가 직접 켜야 합니다.
const createClass = ref<string[]>([])
const overwrite = ref<string[]>([])
const key = (eqp: string, r: Recipe) => `${eqp}/${r.idw}/${r.idp}`
const exists = (t: Checked, r: Recipe) => t.recipe_exists.some(e => e.idw === r.idw && e.idp === r.idp)

const readable = computed(() => props.check.targets.filter((t): t is Checked => !('error' in t)))
const unreadable = computed(() => props.check.targets.filter((t): t is Failed => 'error' in t))

// 읽지 못한 대상, 폴더 생성을 거절한 대상, 덮어쓰기를 허락하지 않은 쌍은 job 에서 빠집니다.
const pairs = computed<Pair[]>(() => readable.value
  .filter(t => !t.class_missing || createClass.value.includes(t.eqp))
  .flatMap(t => props.recipes
    .filter(r => !exists(t, r) || overwrite.value.includes(key(t.eqp, r)))
    .map(r => ({ eqp: t.eqp, ...r }))))

// 파일 수·크기는 수집 전이라 서버가 모릅니다. 고른 레시피의 raw 폴더 요약 API 로 셉니다.
type Summary = { file_count: number, total_bytes: number }
const summaries = ref<Record<string, Summary | null>>({})
const api = (...parts: string[]) => `/api/tools/${[props.eqp, ...parts].map(encodeURIComponent).join('/')}`
onMounted(() => {
  for (const r of props.recipes) {
    $fetch<Summary>(api('classes', props.cls, 'recipes', r.idw, r.idp))
      .then((s) => { summaries.value[`${r.idw}/${r.idp}`] = s })
      .catch(() => { summaries.value[`${r.idw}/${r.idp}`] = null })
  }
})

const scale = computed(() => {
  const all = pairs.value.map(p => summaries.value[`${p.idw}/${p.idp}`])
  const known = all.every(s => s)
  return {
    tools: new Set(pairs.value.map(p => p.eqp)).size,
    recipes: new Set(pairs.value.map(p => `${p.idw}/${p.idp}`)).size,
    // raw 폴더 파일 + .idp 1개. 크기는 raw 폴더 기준(.idp 크기는 목록에 없음).
    files: known ? all.reduce((n, s) => n + s!.file_count + 1, 0) : null,
    bytes: known ? all.reduce((n, s) => n + s!.total_bytes, 0) : null
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
              v-if="!t.class_missing && !t.recipe_exists.length"
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
            v-for="r in recipes.filter(r => exists(t, r))"
            :key="key(t.eqp, r)"
            class="sk-body flex items-center gap-2 text-(--sk-ink)"
          >
            <input
              v-model="overwrite"
              type="checkbox"
              :value="key(t.eqp, r)"
            >
            <span class="font-mono">{{ cls }}/{{ r.idw }}/{{ r.idp }}</span> 이(가) 이미 있습니다. 덮어쓰기
            (허락하지 않으면 이 레시피는 이 장비에서 빠집니다)
          </label>
        </div>
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
          파일 {{ scale.files ?? '?' }}개 · 총 {{ scale.bytes === null ? '?' : size(scale.bytes) }}(raw 폴더 기준)
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
          :disabled="!pairs.length"
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
