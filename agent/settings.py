from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULTS = {
    "author_thinking": False,
    "implementer_thinking": True,
    "repair_thinking": False,
    "repair_diagnosis": True,
    "spec_review": True,
    "architect_thinking": True,
    "vision_model": "",
    "audit_enabled": True,
    "audit_llm": "always",
    "shared_sweep": True,
    "audit_thinking": True,

    "description_pass": True,
    "description_pass_scope": "boilerplate",
    "description_pass_max_sec": 10800,
    "description_pass_token_share": 0.5,
    "foundation_retry": True,

    "hub_min_dependents": 5,
    "hub_retry": True,
    "partial_green_guard": True,
    "description_pass_hubs": True,
    "seed_lock": True,
    "seed_absence_check": False,
    "seed_conflict_quarantine": True,
    "raw_wait_to_author": True,
    "round_bonus": True,
    "best_batch_state": True,
    "closing_regression_guard": True,
    "audit_spec_to_author": True,

    "progress_rounds": True,
    "start_failure_free": True,
    "regression_repair": True,

    "platform_time_limit_sec": 86400,
    "platform_time_margin_sec": 5400,

    "platform_watchdog_sec": 83700,
    "llm_call_limit_sec": 1800,

    "timesave_foundation_filter": False,
    "timesave_down_no_foundation": True,
    "timesave_chase_stop": True,
    "timesave_api_mute": True,

    "regression_net_rollback": True,
    "closing_debt_first": True,
    "timesave_unnamed_inconclusive": True,

    "break_evidence": True,
    "attempt_note": True,
    "in_batch_restart": True,
    "retry_temperature": 0.3,

    "audit_repair_bound": True,

    "foundation_salvage": True,

    "llm_quota_wait_by_time": True,
    "llm_thinking_stream": True,
    "llm_stream_idle_sec": 120,

    "det_code_checks": True,
    "det_seed_restart": True,
    "det_seed_restart_sec": 60,
    "det_nested_names": True,
    "det_role_ladder": True,
    "det_session_probe": True,
    "det_session_probe_sec": 60,
    "det_stated_messages": True,
    "spec_follow_when": True,
    "stage_handoff": True,
    "det_outsider_walk": True,
    "det_outsider_walk_sec": 90,
    "det_entry_link_rewrite": False,
    "det_stage_controls": True,
    "det_router_links": True,
    "det_duplicate_names": True,

    "stage_continuation": True,
    "continuation_baseline_sec": 3600,
    "stage_run_target_sec": 0,
    "stage_final_number": 3,
    "stage_watchdog_slack_sec": 1800,
    "stage_closing_reserve_sec": 2700,
    "continuation_per_batch_sec": 1800,
    "continuation_description_pass_sec": 3600,

    "evolution_preserve": True,
    "evolution_mode": True,
    "evolution_effective_rules": True,
    "evolution_inherited_context": True,
    "evolution_test_baseline": True,
    "evolution_evo_spec_names": True,
    "evolution_deferred_baseline": True,
    "evolution_freeze_inherited_specs": True,
    "evolution_data_lifecycle": True,
    "evolution_data_lifecycle_sec": 150,

    "audit_route_stated": True,
    "outsider_review_retry": True,
    "slot_protected_recheck": True,
    "partial_repair_evidence": True,
    "audit_partial_keep": True,
    "regression_partial_keep": True,
    "audit_carry_still": True,
    "retry_partial_keep": True,
    "seed_pending_carry": True,
    "seed_quarantine_per_test": True,
    "audit_rotate_unresolved": True,
    "detaudit_rotate_cap": True,
    "lifecycle_report_rename_undo": True,
    "seed_lock_partial_restore": True,
    "seed_identity_findings": True,
    "description_pass_partial_keep": True,
    "seed_slot_keep_leftovers": True,
    "audit_settle_rechecked": True,

    "shared_sweep_handle": True,
    "shared_sweep_handle_sec": 2400,
    "shared_sweep_handle_max_specs": 6,

    "edit_blocks": True,

    "spec_plan_roles": True,
    "later_controls_plan": True,
    "spec_plan_block": False,
    "spec_defect_outcome": True,
    "det_plan_walk": True,
    "det_plan_walk_sec": 300,

    "evolution_health_rules": True,
    "evolution_single_source": True,
    "evolution_health_check": True,

    "evolution_health_build": True,
    "evolution_health_probes": True,
    "evolution_health_cap_sec": 900,
    "evolution_startup_debt": True,
    "evolution_entry_debt_slot": True,
    "evolution_entry_debt_sec": 900,

    "contest_deadline_utc": "2026-10-08T12:00:00Z",
    "contest_deadline_margin_sec": 900,
    "evolution_run_target_sec": 57600,
    "contest_min_run_sec": 7200,

    "evolution_full_batch_window": True,
    "evolution_seed_foundation": True,
    "evolution_seed_foundation_sec": 1500,

    "seed_foundation_staged": True,
    "seed_foundation_staged_sec": 2400,
    "regression_window": True,

    "reasoning_cap": False,
    "reasoning_cap_chars": 40000,
    "reasoning_cap_gen": False,
    "cutoff_reask": False,
    "cutoff_reask_chars": 6000,

    "outsider_enabled": True,
    "v162b_crossdep": True,
    "v162b_dependency_probe": True,
    "stated_region_scope": True,
    "signin_page_by_form": True,
    "session_revoke_after_batch": False,

    "strip_trailing_marker": True,
    "fixture_visible_name_prompt": True,
    "conventions_v166": True,

    "fixture_idempotent": True,
    "conventions_v167": True,
    "choice_select_hybrid": True,
    "nav_entry_data_driven": True,
    "item_links_advisory": True,
    "state_disables_everywhere": True,
    "repair_prefix_shared": True,
    "spec_helper_arg_check": True,
    "spec_chained_call_defect": True,
    "conventions_v168x": True,

    "findings_owner_batch_v2": False,

    "spec_strict_targets": True,
    "spec_choice_popup": True,

    "dialog_close_check": True,
    "dialog_entries_buttons_check": True,
    "cell_glyph_check": True,
    "stated_select_check": True,
    "header_button_check": True,
    "count_text_check": True,
    "stated_textbox_check": True,
    "menu_role_check": True,
    "status_word_check": True,
    "spec_choice_both_ways": False,
}

def retry_temperature() -> float:
    try:
        value = float(SETTINGS.get("retry_temperature", 0.3))
    except (TypeError, ValueError):
        return 0.3
    if value != value:
        return 0.3
    return max(0.0, min(2.0, value))

def setting_seconds(key: str, default: float) -> float:
    try:
        return float(SETTINGS.get(key, default))
    except (TypeError, ValueError):
        return float(default)

def hub_min_dependents() -> int:
    try:
        return max(1, int(float(SETTINGS.get("hub_min_dependents", 5))))
    except (TypeError, ValueError):
        return 5

def setting_on(key: str, default: bool = True) -> bool:
    value = SETTINGS.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)

def _load() -> dict:
    data = dict(_DEFAULTS)
    path = _ROOT / "settings.json"
    if path.is_file():
        try:
            data.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    for key in data:
        env = os.environ.get("AGENT_" + key.upper())
        if env is not None:
            data[key] = env.strip().lower() in ("1", "true", "yes", "on") if isinstance(data[key], bool) else env.strip()
    return data

SETTINGS = _load()
AUTHOR_THINKING: bool = bool(SETTINGS["author_thinking"])
IMPLEMENTER_THINKING: bool = bool(SETTINGS["implementer_thinking"])
