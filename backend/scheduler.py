"""시작 판정(스펙 "스케줄러"). 상태 없는 순수 함수이고, 예약과 실행은 `jobs.Scheduler` 가 합니다."""

from typing import NamedTuple


class Entry(NamedTuple):
    """미완료 job 하나. `tools` 는 원본과 대상 전부, `n` 은 대상 수(= 예약 슬롯 수)."""

    id: int
    tools: frozenset[str]
    n: int
    started: bool


def admit(queue: list[Entry], cap: int) -> tuple[list[int], list[int]]:
    """`queue` 는 미완료 job 을 id 순으로. (지금 시작할 id, 상한 초과로 끝낼 id)를 돌려줍니다.

    시작한 job 은 대상 수만큼 슬롯을 쥐고 있습니다. 대기 job J 는 앞선 미완료 job 과 장비가 겹치지
    않고, 남은 슬롯에 들고, 앞에 슬롯을 기다리는 job 이 없을 때 시작합니다.
    """
    free = cap - sum(e.n for e in queue if e.started)
    busy: set[str] = set()
    budget_wait = False
    start, over = [], []
    for e in queue:
        if not e.started:
            if e.n > cap:  # 실행할 수 없는 job 은 큐 앞을 막지 않습니다.
                over.append(e.id)
                continue
            if not (e.tools & busy) and not budget_wait:
                if e.n <= free:
                    start.append(e.id)
                    free -= e.n
                else:
                    budget_wait = True
        busy |= e.tools
    return start, over

