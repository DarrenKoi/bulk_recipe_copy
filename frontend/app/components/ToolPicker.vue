<script setup lang="ts">
type Tool = { eqp_id: string, fab_name: string, model: string }

const selected = defineModel<string | null>({ required: true })
const query = ref('')

const { data, error, status } = await useFetch<{ tools: Tool[] }>('/api/tools')

const shown = computed(() => {
  const q = query.value.trim().toUpperCase()
  return (data.value?.tools ?? []).filter(t => t.eqp_id.toUpperCase().includes(q))
})
</script>

<template>
  <section class="dashboard-surface flex flex-col gap-4 rounded-[var(--sk-r-card)] p-6">
    <div class="flex items-center gap-3">
      <h2 class="sk-heading">
        원본 장비
      </h2>
      <span
        v-if="selected"
        class="sk-card-id"
      >{{ selected }}</span>
      <UInput
        v-model="query"
        icon="i-lucide-search"
        placeholder="eqp_id 일부"
        class="ml-auto w-64"
      />
    </div>

    <p
      v-if="status === 'pending'"
      class="sk-body text-(--sk-ink-muted)"
    >
      장비 목록을 불러오는 중입니다.
    </p>
    <p
      v-else-if="error"
      class="sk-body text-(--sk-bad)"
    >
      장비 목록을 불러오지 못했습니다. {{ error.data?.error?.message ?? error.message }}
    </p>
    <p
      v-else-if="shown.length === 0"
      class="sk-body text-(--sk-ink-muted)"
    >
      맞는 장비가 없습니다.
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
          class="cursor-pointer border-b border-(--sk-border-soft) hover:bg-(--sk-muted-surface)"
          :class="{ 'bg-(--sk-muted-surface) font-semibold': t.eqp_id === selected }"
          @click="selected = t.eqp_id"
        >
          <td class="py-2 font-mono tabular-nums text-(--sk-ink)">
            <label class="flex cursor-pointer items-center gap-2">
              <input
                v-model="selected"
                type="radio"
                name="source-tool"
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
  </section>
</template>
