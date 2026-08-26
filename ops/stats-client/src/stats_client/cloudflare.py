"""Cloudflare D1 REST Query API 的只读客户端。

只用 stdlib（``urllib``），不额外依赖 ``requests``/``httpx``——这个客户端
只做"发一条参数化 SQL、拿回行"这一件事，用不上一整个 HTTP 库的功能面。

文档：https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/query/
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

_API_BASE = "https://api.cloudflare.com/client/v4"
_TIMEOUT_S = 30


class D1Error(RuntimeError):
    """CF API 返回 HTTP 错误，或 ``success: false``。"""


class D1ConnectionError(D1Error):
    """网络不可达/超时——与"凭据错/SQL 错"区分开，调用方可能想区别处理
    （例如：网络问题时仍展示本地缓存，凭据错才提示重新配置）。"""


@dataclass
class D1Client:
    account_id: str
    api_token: str
    database_id: str

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        """执行一条只读 SQL，返回结果行（``list[dict]``）。

        D1 Query API 一次请求可能对应多条以 ``;`` 分隔的语句，返回值是
        每条语句一个结果块；本客户端只发单条语句，取第一块的 ``results``。
        """
        url = (f"{_API_BASE}/accounts/{self.account_id}"
              f"/d1/database/{self.database_id}/query")
        body = json.dumps({"sql": sql, "params": params or []}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            try:
                errors = json.loads(detail).get("errors")
            except json.JSONDecodeError:
                errors = detail
            raise D1Error(f"D1 查询失败（HTTP {e.code}）：{errors}") from e
        except urllib.error.URLError as e:
            raise D1ConnectionError(f"连不上 Cloudflare：{e.reason}") from e

        if not payload.get("success"):
            raise D1Error(f"D1 查询失败：{payload.get('errors')}")
        result = payload.get("result") or []
        if not result:
            return []
        return result[0].get("results") or []

    def verify(self) -> None:
        """轻量连通性/权限检查：配置页保存时调用，失败抛 D1Error/D1ConnectionError。"""
        self.query("SELECT 1 AS ok")
