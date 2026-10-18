<script setup lang="ts">
type FileState = 'pending' | 'in_progress' | 'verified' | 'failed' | 'uncertain'
type ResultFile = { path: string, state: FileState, reason: string | null, temp_path: string | null }
export type TargetResult = {
  eqp: string
  state: 'running' | 'uncertain' | 'failed' | 'verified' | null
  counts: Record<FileState, number>
  recipes: { idw: string, idp: string, target_only: number | null, files: ResultFile[] }[]
}
export type Progress = {
  status: 'waiting' | 'running' | 'done' | 'failed'
  finished: boolean
  success: boolean
  processed: number
  total: number
  rate: number | null
}

defineProps<{ tree: TargetResult[], progress: Progress }>()

const TARGET = {
  running: { text: '진행 중', cls: 'text-(--sk-ink)' },
  uncertain: { text: '불명확 · 장비에서 직접 확인', cls: 'text-(--sk-warn)' },
  failed: { text: '일부 실패', cls: 'text-(--sk-bad)' },
  verified: { text: '전부 검증됨', cls: 'text-(--sk-ok)' }
} as const
const FILE = {
  pending: '대기',
  in_progress: '진행 중',
  verified: '검증됨',
  failed: '실패',
  uncertain: '불명확'
} as const
const FILE_CLS = { failed: 'text-(--sk-bad)', uncertain: 'text-(--sk-warn)' } as Record<string, string>

const percent = (p: Progress) => p.rate === null ? '-' : `${Math.floor(p.rate * 100)}%`
const fileCount = (t: TargetResult) => Object.values(t.counts).reduce((a, b) => a + b, 0)
</script>

<template>
  <div class="flex flex-col gap-3">
    <div
      v-if="progress.finished"
      class="rounded-[var(--sk-r-card)] border p-3"
      :class="progress.success ? 'border-(--sk-ok-border) bg-(--sk-ok-soft)' : 'border-(--sk-bad-border) bg-(--sk-bad-soft)'"
    >
      <p
        class="sk-body font-semibold"
        :class="progress.success ? 'text-(--sk-ok)' : 'text-(--sk-bad)'"
      >
        {{ progress.success ? '전체 성공' : '전체 성공 아님' }} · 처리율 {{ percent(progress) }}
      </p>
      <p class="sk-meta">
        장비 {{ tree.length }}대 ·
        전부 검증됨 {{ tree.filter(t => t.state === 'verified').length }} ·
        일부 실패 {{ tree.filter(t => t.state === 'failed').length }} ·
        불명확 {{ tree.filter(t => t.state === 'uncertain').length }}
      </p>
    </div>
    <p
      v-else
      class="sk-body text-(--sk-ink)"
    >
      처리율 {{ percent(progress) }} ({{ progress.processed }}/{{ progress.total }})
    </p>

    <ul class="flex flex-col gap-2">
      <li
        v-for="t in tree"
        :key="t.eqp"
        class="rounded-[var(--sk-r-card)] border border-(--sk-border-soft) p-3"
      >
        <p class="sk-body flex flex-wrap items-baseline gap-x-3">
          <span class="font-mono font-semibold text-(--sk-ink)">{{ t.eqp }}</span>
          <span
            v-if="t.state"
            :class="TARGET[t.state].cls"
          >{{ TARGET[t.state].text }}</span>
          <span
            v-else
            class="text-(--sk-ink-muted)"
          >수집 전</span>
          <span
            v-if="t.state === 'running'"
            class="sk-meta"
          >실패 {{ t.counts.failed }} · 불명확 {{ t.counts.uncertain }}</span>
          <span
            v-if="t.state === 'verified'"
            class="sk-meta"
          >파일 {{ fileCount(t) }}개</span>
        </p>
        <!-- 전부 검증된 장비는 한 줄로 접습니다. 대상에만 있는 파일만 따로 알립니다. -->
        <template v-if="t.state === 'verified'">
          <p
            v-for="r in t.recipes.filter(r => r.target_only)"
            :key="`${r.idw}/${r.idp}`"
            class="sk-meta text-(--sk-warn)"
          >
            {{ r.idw }}/{{ r.idp }}: 대상에만 있는 파일 {{ r.target_only }}개
          </p>
        </template>
        <ul
          v-else-if="t.state"
          class="mt-2 flex flex-col gap-2 pl-4"
        >
          <li
            v-for="r in t.recipes"
            :key="`${r.idw}/${r.idp}`"
          >
            <p class="sk-body font-mono text-(--sk-ink)">
              {{ r.idw }}/{{ r.idp }}
              <span
                v-if="r.target_only"
                class="sk-meta font-sans text-(--sk-warn)"
              >대상에만 있는 파일 {{ r.target_only }}개</span>
            </p>
            <ul class="pl-4">
              <li
                v-for="f in r.files"
                :key="f.path"
                class="sk-meta flex flex-wrap gap-x-2"
              >
                <span :class="FILE_CLS[f.state] ?? 'text-(--sk-ink-muted)'">{{ FILE[f.state] }}</span>
                <span class="font-mono break-all text-(--sk-ink)">{{ f.path }}</span>
                <span
                  v-if="f.reason"
                  :class="FILE_CLS[f.state]"
                >{{ f.reason }}</span>
                <span
                  v-if="f.temp_path"
                  class="font-mono break-all"
                >임시 파일 {{ f.temp_path }}</span>
              </li>
            </ul>
          </li>
        </ul>
      </li>
    </ul>
  </div>
</template>
