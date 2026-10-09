"""One callable catalog shared by the server and packaged schema generation."""
from __future__ import annotations


def tool_catalog(service, documents) -> dict:
    names = (
        "get_capabilities", "list_targets", "list_plans", "query_equipment", "get_plan_context",
        "get_game_config", "list_graduation_schemes", "get_tuning_config", "analyze_cultivation",
        "update_tuning_config",
        "search_best_combo", "get_analysis_job", "preview_generated_tuning", "create_generated_tuning",
        "list_generated_tuning", "validate_auto_tuning", "start_auto_tuning",
        "start_scan_all_loadouts", "get_task_status", "query_tuning_history", "get_tuning_results",
    )
    tools = {name: getattr(service, name) for name in names}
    tools.update({name: getattr(documents, name) for name in ("list_docs", "read_doc", "search_docs")})
    return tools
