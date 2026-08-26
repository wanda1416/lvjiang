"""首次配置向导：填 Cloudflare 只读 Token / Account ID / Database ID。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from .. import config as config_mod
from ..cloudflare import D1Client, D1Error
from ..deps import get_cfg
from ..templates_env import templates

router = APIRouter()


@router.get("/setup")
def setup_form(request: Request, cfg: config_mod.Config = Depends(get_cfg)):
    has_token = bool(config_mod.get_api_token(cfg))
    return templates.TemplateResponse(request, "setup.html", {
        "cfg": cfg, "has_token": has_token, "error": None, "saved": False,
    })


@router.post("/setup")
def setup_save(
    request: Request,
    account_id: str = Form(""),
    api_token: str = Form(""),
    database_id: str = Form(""),
    database_name: str = Form(""),
    auto_sync: bool = Form(False),
    cfg: config_mod.Config = Depends(get_cfg),
):
    # account_id 来自环境变量时表单里那个 <input disabled>——浏览器不会把
    # disabled 字段提交上来，所以这里必须回退到 cfg.account_id（它已经在
    # get_cfg() 里应用过环境变量覆盖），否则每次保存都会被误判成"没填"。
    account_id = account_id.strip() or cfg.account_id
    database_id = database_id.strip() or cfg.database_id
    database_name = database_name.strip() or cfg.database_name
    token = api_token.strip() or config_mod.get_api_token(cfg)

    error = None
    if not account_id:
        error = "Account ID 不能为空"
    elif not token:
        error = "需要一个只读（D1:Read）权限的 API Token"
    else:
        try:
            D1Client(account_id=account_id, api_token=token,
                     database_id=database_id).verify()
        except D1Error as e:
            error = f"连接校验失败，请检查凭据：{e}"

    if error:
        return templates.TemplateResponse(request, "setup.html", {
            "cfg": config_mod.Config(account_id=account_id, database_id=database_id,
                                     database_name=database_name,
                                     data_dir=cfg.data_dir, auto_sync=auto_sync),
            "has_token": bool(token), "error": error, "saved": False,
        }, status_code=400)

    new_cfg = config_mod.Config(account_id=account_id, database_id=database_id,
                                database_name=database_name,
                                data_dir=cfg.data_dir, auto_sync=auto_sync)
    config_mod.save_config(new_cfg)
    if api_token.strip():
        config_mod.set_api_token(new_cfg, api_token.strip())
    return RedirectResponse("/", status_code=303)
