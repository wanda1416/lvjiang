"""反馈提交的 HTTP 通道：一次提交 + 逐张附件上传。

两段式是刻意的：10 张截图各 700KB，一次性 base64 会到 11MB，弱网必然超时，
而且失败一次就要重传全部。这里先提交正文与清单（几百字节），再逐张上传；
某张失败只记一条 failure，"代码里重试那张"就够了。

**服务端返回的正文永远不改变本地状态**：本模块只回结果对象或抛
:class:`FeedbackError`，不做任何自动清理存储的动作（与统计通道同理）。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib import error as urlerror
from urllib import request as urlrequest

from loguru import logger

from ..account.transport import RETRY_DELAY_S, network_hint
from .limits import MAX_ATTACHMENT_BYTES

#: 与账号服务同一个域，同一个 Worker。
SERVICE_BASE = "https://lvjiang.wyxj.net"
#: 自定义 UA：urllib 默认 UA 会被 Cloudflare Bot 防护在边缘 403。
USER_AGENT = "lvjiang-feedback-client"
#: 正文提交超时（用户在等结果，宁可早点失败给可行动的提示）。
SUBMIT_TIMEOUT_S = 15
#: 单个附件超时（上传体积大，弱网下放宽）。
ATTACH_TIMEOUT_S = 45

_ERROR_MESSAGES = {
    "invalid_email": "邮箱格式不正确，填错了就没法回复你",
    "invalid_request": "提交内容不符合要求（标题最多 120 字，描述最多 6000 字）",
    "too_many_attempts": "提交过于频繁了，请稍后再试",
    "too_many_attachments": "附件数量超过上限（截图最多 10 张）",
    "attachment_too_large": "有附件超过大小上限，请减少截图张数或裁剪后重试",
    "not_found": "反馈编号不存在，可能已被删除",
    "unauthorized": "附件上传凭据已失效，请关闭对话框后重新提交",
    "service_unavailable": "反馈服务暂时不可用，请稍后再试",
}
#: 服务端明确说「这个凭据不行」以外的错误都不该被当成"内容有问题"。
_NETWORK_CODES = frozenset({"network", "service_unavailable", "rejected"})


class FeedbackError(Exception):
    """提交失败；``code`` 是服务端错误码或本地分类（network/rejected）。"""

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OutgoingAttachment:
    """一个待上传的附件（``data`` 是**未压缩原文**）。"""

    name: str
    kind: str
    mime: str
    data: bytes

    @property
    def gzipped(self) -> bytes:
        return gzip.compress(self.data, compresslevel=6)


@dataclass(frozen=True)
class AttachmentFailure:
    """某个附件没上传成功；``seq`` 对应它在上传顺序里的位置（从 1 起）。"""

    seq: int
    name: str
    message: str


@dataclass(frozen=True)
class SubmitResult:
    """提交结果：正文一定已入库，附件可能部分失败。"""

    id: str
    ticket: str
    uploaded: int
    declared: int
    failures: tuple[AttachmentFailure, ...] = field(default=())

    @property
    def complete(self) -> bool:
        return not self.failures


def access_token() -> str:
    """当前登录态的 access 令牌；未登录返回空串（匿名提交用）。

    走 ``AccountSession.refresh_token_for``：它复用进程内那把刷新锁——反馈提交在
    工作线程里跑，若和同步通道各自 refresh，会把对方的令牌顶掉（2026-11-11 事故）。
    这里**不**抛错也不清本地登录态：拿不到令牌只说明「这次以匿名身份提交」。
    """
    try:
        from ..account import service
        return service.get_session().refresh_token_for("") or ""
    except Exception as error:  # noqa: BLE001 — 登录态只是附加信息
        logger.debug("反馈提交：取登录态失败，按匿名提交（{}）", error)
        return ""


def _post(route: str, payload: dict, *, token: str, timeout: float,
          retry: bool) -> dict:
    """发一次 JSON 请求；网络类失败按 ``retry`` 决定是否再试一次。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    attempts = 2 if retry else 1
    last: Exception | None = None
    for attempt in range(attempts):
        prepared = urlrequest.Request(SERVICE_BASE + route, data=body,
                                      headers=headers, method="POST")
        try:
            with urlrequest.build_opener().open(prepared, timeout=timeout) as response:
                return json.loads(response.read(1_000_001).decode("utf-8"))
        except urlerror.HTTPError as exc:
            raise _http_error(exc) from None
        except (urlerror.URLError, TimeoutError, OSError, HTTPException, ValueError) as exc:
            last = exc
            if attempt + 1 < attempts:
                logger.warning("反馈通道连接失败（重试一次）：{}", exc)
                time.sleep(RETRY_DELAY_S)
    logger.warning("反馈通道连接失败（重试后仍失败）：{}", last)
    hint = network_hint(last) if isinstance(last, Exception) else "连不上反馈服务"
    raise FeedbackError(f"{hint}，请检查网络后重试", code="network") from None


def _http_error(exc: urlerror.HTTPError) -> FeedbackError:
    """把 HTTP 错误翻成可行动的中文；只有服务端给的错误码才引用其含义。"""
    code = ""
    try:
        details = json.loads(exc.read(65_536).decode("utf-8"))
        if isinstance(details, dict) and isinstance(details.get("error"), str):
            code = details["error"]
    except (ValueError, UnicodeDecodeError, OSError):
        pass
    if exc.code >= 500:
        return FeedbackError(_ERROR_MESSAGES["service_unavailable"],
                             code=code or "service_unavailable")
    if code:
        return FeedbackError(_ERROR_MESSAGES.get(code, f"反馈提交失败（{code}）"), code=code)
    return FeedbackError(f"反馈服务拒绝了请求（HTTP {exc.code}），请稍后重试",
                         code="rejected")


def _manifest(attachments: Sequence[OutgoingAttachment]) -> list[dict]:
    """只发元数据：服务端据此知道「应该收到几张」，缺口在控制台直接可见。"""
    return [{
        "seq": index,
        "name": item.name,
        "kind": item.kind,
        "mime": item.mime,
        "size": len(item.data),
        "sha256": hashlib.sha256(item.data).hexdigest(),
    } for index, item in enumerate(attachments, start=1)]


def upload_attachments(
    feedback_id: str,
    ticket: str,
    attachments: Sequence[OutgoingAttachment],
    *,
    token: str = "",
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[int, tuple[AttachmentFailure, ...]]:
    """逐张上传附件，返回 ``(成功张数, 失败清单)``。

    可重复调用：``seq`` 由附件在序列里的位置决定，服务端按
    ``(feedback_id, seq)`` UPSERT，所以「重试失败的那几张」用同一份序列再跑一遍
    就是幂等的，不会堆出重复行。
    """
    failures: list[AttachmentFailure] = []
    uploaded = 0
    total = len(attachments)
    for seq, item in enumerate(attachments, start=1):
        if on_progress is not None:
            on_progress(uploaded, total, item.name)
        blob = item.gzipped
        if len(blob) > MAX_ATTACHMENT_BYTES:
            failures.append(AttachmentFailure(
                seq=seq, name=item.name,
                message=f"{item.name} 压缩后仍有 {len(blob) // 1024} KB，超过单件上限"))
            continue
        try:
            _post("/api/feedback/attach", {
                "id": feedback_id,
                "ticket": ticket,
                "seq": seq,
                "name": item.name,
                "kind": item.kind,
                "mime": item.mime,
                "size": len(item.data),
                "sha256": hashlib.sha256(item.data).hexdigest(),
                "gzipped": 1,
                "payload_base64": base64.b64encode(blob).decode("ascii"),
            }, token=token, timeout=ATTACH_TIMEOUT_S, retry=False)
            uploaded += 1
        except FeedbackError as error:
            logger.info("反馈附件上传失败（{}）：{}", item.name, error)
            failures.append(AttachmentFailure(seq=seq, name=item.name, message=str(error)))
    if on_progress is not None:
        on_progress(uploaded, total, "")
    return uploaded, tuple(failures)


def submit_feedback(
    *,
    kind: str,
    title: str,
    body: str,
    email: str,
    fields: dict | None = None,
    env: dict | None = None,
    attachments: Iterable[OutgoingAttachment] = (),
    on_progress: Callable[[int, int, str], None] | None = None,
    token: str | None = None,
) -> SubmitResult:
    """提交一条反馈：正文先落库，附件随后逐张上传。

    Args:
        kind: 分类 key（见 ``kinds.FEEDBACK_KINDS``）。
        title: 标题（≤120 字）。
        body: 描述正文（≤6000 字）。
        email: **必填**，回复走它。
        fields: 类型专属字段（复现步骤等），全部可选。
        env: 环境快照，见 :func:`payload.collect_environment`。
        attachments: 附件序列，顺序即 seq。
        on_progress: ``(已上传, 总数, 当前名称)`` 回调，供 UI 更新进度。
        token: 指定 access 令牌；缺省自动取当前登录态（取不到即匿名）。

    Raises:
        FeedbackError: 正文没能入库（附件失败不算，见 ``result.failures``）。
    """
    items = list(attachments)
    payload = {
        "kind": kind,
        "title": title,
        "body": body,
        "email": email,
        "fields": fields or {},
        "env": env or {},
        "attachments": _manifest(items),
    }
    auth = token if token is not None else access_token()
    created = _post("/api/feedback/submit", payload, token=auth,
                    timeout=SUBMIT_TIMEOUT_S, retry=True)
    feedback_id = str(created.get("id") or "")
    ticket = str(created.get("ticket") or "")
    if not feedback_id or not ticket:
        raise FeedbackError("反馈服务响应不完整，请稍后重试", code="service_unavailable")
    uploaded, failures = upload_attachments(feedback_id, ticket, items,
                                            token=auth, on_progress=on_progress)
    return SubmitResult(id=feedback_id, ticket=ticket, uploaded=uploaded,
                        declared=len(items), failures=failures)
