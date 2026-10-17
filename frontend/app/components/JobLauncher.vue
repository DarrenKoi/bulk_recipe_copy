<script setup lang="ts">
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

async function run() {
  sending.value = true
  failure.value = null
  try {
    const job = await $fetch<{ id: number }>('/api/jobs', {
      method: 'POST',
      body: {
        source_eqp: props.eqp,
        class_name: props.cls,
        recipes: props.picked.map((rid) => {
          const [idw, idp] = rid.split('/')
          return { idw, idp }
        }),
        targets: targets.value
      }
    })
    targets.value = []
    emit('created', job.id)
  } catch (e: any) {
    failure.value = e?.data?.error?.message ?? e?.message
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
      >job을 만들지 못했습니다. {{ failure }}</span>
    </div>
  </section>
</template>
