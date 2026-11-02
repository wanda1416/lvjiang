"""配置页：只编辑本机配置；Cloudflare Token 只读自 secrets_dir。

Token 不再从页面写入（也没有环境变量/密钥环回退）：它固定在
``<secrets_dir>/cloudflare_api_token.txt``，想换就改那个文件再刷新页面。
页面只负责展示"读了没有、能不能用"，并提供一次显式校验。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from .. import config as config_mod
from ..cloudflare import D1Client, D1ConnectionError, D1Error
from ..deps import get_cfg
from ..templates_env import templates

router = APIRouter()


def _render(request: Request, cfg: config_mod.Config, *,
            error: str = "", notice: str = "", status_code: int = 200):
    return templates.TemplateResponse(request, "setup.html", {
        "cfg": cfg,
        "token": config_mod.token_status(),
        "error": error,
        "notice": notice,
    }, status_code=status_code)


@router.get("/setup")
def setup_form(request: Request, cfg: config_mod.Config = Depends(get_cfg)):
    return _render(request, cfg)


@router.post("/setup")
def setup_save(
    request: Request,
    account_id: str = Form(""),
    database_id: str = Form(""),
    database_name: str = Form(""),
    auto_sync: bool = Form(False),
    cfg: config_mod.Config = Depends(get_cfg),
):
    # 留空的字段沿用已保存的值：折叠或未提交的输入框不该被当成"没填"。
    account_id = account_id.strip() or cfg.account_id
    database_id = database_id.strip() or cfg.database_id
    database_name = database_name.strip() or cfg.database_name

    token = config_mod.get_api_token()
    error = ""
    if not account_id:
        error = "Account ID 不能为空"
    elif not token:
        status = config_mod.token_status()
        error = (f"读不到 Cloudflare API Token：请把只读 token 写到 "
                 f"{status['path'] or '<secrets_dir>/cloudflare_api_token.txt'}"
                 f"（{status['error']}）")
    else:
        try:
            D1Client(account_id=account_id, api_token=token,
                     database_id=database_id).verify()
        except (D1Error, D1ConnectionError) as exc:
            error = f"连接校验失败，请检查 Account/Database ID 与 token：{exc}"

    if error:
        new_cfg = config_mod.Config(
            account_id=account_id, database_id=database_id,
            database_name=database_name, data_dir=cfg.data_dir,
            auto_sync=auto_sync)
        return _render(request, new_cfg, error=error, status_code=400)

    config_mod.save_config(config_mod.Config(
        account_id=account_id, database_id=database_id,
        database_name=database_name, data_dir=cfg.data_dir,
        auto_sync=auto_sync))
    return RedirectResponse("/", status_code=303)


@router.post("/setup/verify")
def setup_verify(request: Request, cfg: config_mod.Config = Depends(get_cfg)):
    """不改配置，只用当前 token 跑一遍 ``SELECT 1`` 校验，把结果说清楚。"""
    token = config_mod.get_api_token()
    if not token:
        status = config_mod.token_status()
        return _render(request, cfg, status_code=400,
                       error=f"读不到 Cloudflare API Token：{status['error']}")
    try:
        D1Client(account_id=cfg.account_id, api_token=token,
                 database_id=cfg.database_id).verify()
    except (D1Error, D1ConnectionError) as exc:
        return _render(request, cfg, status_code=400,
                       error=f"凭据校验失败：{exc}")
    return _render(request, cfg, notice="凭据可用：D1 查询成功返回。")
