<script setup lang="ts">
import type { Choice, Preflight } from './ConfirmDialog.vue'

type Tool = { eqp_id: string, fab_name: string, model: string }

const props = defineProps<{ eqp: string, cls: string, picked: string[] }>()
const emit = defineEmits<{ created: [id: number] }>()

const targets = ref<string[]>([])
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
const selection = () => ({ source_eqp: props.eqp, class_name: props.cls, recipes: recipes.value, targets: targets.value })
const check = ref<Preflight | null>(null)
const confirming = ref(false)
const reason = (e: any) => e?.data?.error?.message ?? e?.message

// 실행은 사전 점검 → 확인 팝업 → job 생성입니다. 팝업의 선택을 그대로 보냅니다.
async function run() {
  sending.value = true
  failure.value = null
  try {
    check.value = await $fetch<Preflight>('/api/preflight', { method: 'POST', body: selection() })
    confirming.value = true
  } catch (e) {
    failure.value = `사전 점검을 하지 못했습니다. ${reason(e)}`
  } finally {
    sending.value = false
  }
}

async function create(choice: Choice) {
  sending.value = true
  try {
    const job = await $fetch<{ id: number }>('/api/jobs', { method: 'POST', body: { ...selection(), ...choice } })
    confirming.value = false
    targets.value = []
    emit('created', job.id)
  } catch (e) {
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
      v-if="check && confirming"
      v-model:open="confirming"
      :eqp="eqp"
      :cls="cls"
      :recipes="recipes"
      :check="check"
      :sending="sending"
      @confirm="create"
    />
  </section>
</template>
