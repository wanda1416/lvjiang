"""穷举匹配算法（流派无关）

从会意判定器迁出的两个回溯算法，供 GenericSchoolJudge 复用：
- _match_pattern:  完整定级的精确匹配
- _match_partial:  调律潜力判定的部分匹配（万能牌 + 转律牌）
"""


def _match_pattern(tokens: list[str], required: list[set[str]],
                   optional_n: int, optional_pool: set[str]) -> bool:
    """穷举匹配：tokens 必须恰好填满 必选槽 + 可选槽（无序，允许池内重复）"""
    if len(tokens) != len(required) + optional_n:
        return False

    def backtrack(slot_idx: int, remaining: list[str]) -> bool:
        if slot_idx == len(required):
            return all(t in optional_pool for t in remaining)
        for i, t in enumerate(remaining):
            if t in required[slot_idx]:
                if backtrack(slot_idx + 1, remaining[:i] + remaining[i + 1:]):
                    return True
        return False

    return backtrack(0, tokens)


def _match_partial(tokens: list[str], required: list[set[str]],
                   optional_n: int, n_free: int, n_trans: int = 0,
                   pool_symbols: set[str] | None = None,
                   optional_pool: set[str] | None = None) -> bool:
    """部分匹配：tokens + n_free 张万能牌 + n_trans 张转律牌 能否填满槽位

    空词条槽视作万能牌（可变成任意需要的词条）；转律牌来自转律
    模拟，因转律不产生神力词条，只能补足含普通词条候选的槽位
    （纯神力槽位须由万能牌补足，pool_symbols 用于区分普通符号）。
    已有 token 必须全部落入某个必选槽或可选池槽位，剩余槽位由
    两类牌补足。
    """
    pool = optional_pool or set()
    if len(tokens) + n_free + n_trans != len(required) + optional_n:
        return False

    def backtrack(idx: int, slots: list[set[str]], opt_left: int) -> bool:
        if idx == len(tokens):
            # 剩余槽位由万能牌/转律牌补足；纯神力槽位只能用万能牌
            if n_trans and pool_symbols is not None:
                divine_only = sum(1 for s in slots if not (s & pool_symbols))
                return divine_only <= n_free
            return True
        t = tokens[idx]
        if opt_left > 0 and t in pool:
            if backtrack(idx + 1, slots, opt_left - 1):
                return True
        for j, slot in enumerate(slots):
            if t in slot:
                if backtrack(idx + 1, slots[:j] + slots[j + 1:], opt_left):
                    return True
        return False

    return backtrack(0, required, optional_n)
