"""Пороги замера живут у примесей, которые их читают (plan-to-10, шаг 4).

Значения сняты с R7Testovarka до переноса (main 7bbd4fa) — перенос и любые
перестановки примесей не должны их менять. Новое значение порога — это
смена методики: правится здесь осознанно, вместе с обоснованием у константы.
"""
import r7_Testovarka as r7mod
from r7.bold_button import BoldButtonMixin
from r7.cdp import CdpMixin
from r7.dialogs import DialogsMixin
from r7.doc_run import DocumentRunMixin
from r7.export import ExportMixin
from r7.measure import MeasureMixin
from r7.op_end import OpEndMixin
from r7.readiness import ReadinessMixin
from r7.ui_fallback import UiFallbackMixin
from r7.x2t_files import X2tFilesMixin

EXPECTED = {
    "BOLD_BUTTON_CDP_CONNECT_TIMEOUT_SEC": 0.5,
    "BOLD_BUTTON_CLASSES": ("Button", "ToolbarButton"),
    "BOLD_BUTTON_LABELS": ("b", "ж", "жирный", "bold"),
    "BOLD_BUTTON_POLL_SEC": 0.1,
    "BOLD_BUTTON_TIMEOUT_SEC": 3.0,
    "BOLD_PROBE_TIMEOUT_SEC": 0.3,
    "BOLD_STABLE_SEC": 0.5,
    "CDP_CONNECT_TIMEOUT_SEC": 2.0,
    "CDP_ITEM_POSITION_TOLERANCE_PX": 30,
    "CDP_LONG_OP_TIMEOUT_SEC": 180.0,
    "CDP_OPS_ENABLED": True,
    "CDP_OP_TIMEOUT_SEC": 10.0,
    "CDP_WHOLE_SHEET_MIN_ROWS": 10000,
    "CLOSE_CDP_RETRY_SEC": 1.0,
    "EXPORT_LOCK_WAIT_SEC": 5.0,
    "OP_BUSY_CORE_PCT": 25.0,
    "OP_BUSY_STRONG_CORE_PCT": 60.0,
    "OP_CDP_PANEL_PACE_SEC": 0.4,
    "OP_CDP_TAIL_GRACE_SEC": 0.45,
    "OP_CONTEXT_MENU_WAIT_SEC": 30.0,
    "OP_CPU_WINDOW_SEC": 0.2,
    "OP_DIALOG_ATTEMPTS": 3,
    "OP_DIALOG_PACE": 0.6,
    "OP_EXPORT_FILE_POLL_SEC": 0.05,
    "OP_EXPORT_FILE_STABLE_CHECKS": 8,
    "OP_EXPORT_FILE_TIMEOUT_SEC": 120.0,
    "OP_IDLE_SAMPLES": 6,
    "OP_KEY_PACE": 0.08,
    "RUNS_TOPUP_MAX_FACTOR": 2,
    "OP_MAX_WAIT_SEC": 180,
    "OP_MENU_PACE": 0.12,
    "OP_PDF_GRACE_SEC": 6.0,
    "OP_PING_FAST_SEC": 0.01,
    "OP_PING_GAP_SEC": 0.05,
    "OP_PING_QUIET_SEC": 0.3,
    "OP_POLL_SEC": 0.05,
    "OP_PROC_REFRESH_SEC": 0.5,
    "OP_RESPONSIVE_MS": 40,
    "OP_SELECT_ALL_MAX_SEC": 20,
    "OP_START_GRACE_SEC": 1.0,
    "READY_ESC_WITHOUT_CDP": True,
    "READY_IDLE_CORE_PCT": 25.0,
    "READY_IDLE_SAMPLES": 20,
    "READY_MIN_BUSY_SEC": 0.5,
    "READY_POLL_SEC": 0.15,
    "READY_PROC_REFRESH_SEC": 1.0,
    "READY_RESPONSIVE_MS": 300,
    "DOC_LAYOUT_POLL_SEC": 0.05,
    "DOC_LAYOUT_STABLE_POLLS": 2,
    "DOC_READY_LAYOUT_TIMEOUT_SEC": 60.0,
    "DOC_UNDO_MAX_STEPS": 400,
}

# Где порог объявлен: у примеси, которая его читает (или у главного читателя).
OWNER = {
    ReadinessMixin: "READY_",
    BoldButtonMixin: "BOLD_",
    CdpMixin: ("CDP_OPS_ENABLED", "CDP_OP_TIMEOUT_SEC", "CDP_LONG_OP_TIMEOUT_SEC",
               "CDP_WHOLE_SHEET_MIN_ROWS", "CDP_CONNECT_TIMEOUT_SEC"),
    DialogsMixin: ("CLOSE_CDP_RETRY_SEC", "CDP_ITEM_POSITION_TOLERANCE_PX"),
    ExportMixin: ("OP_PDF_GRACE_SEC", "OP_MENU_PACE", "OP_CDP_PANEL_PACE_SEC"),
    X2tFilesMixin: ("EXPORT_LOCK_WAIT_SEC",),
    MeasureMixin: ("OP_KEY_PACE", "RUNS_TOPUP_MAX_FACTOR"),
    UiFallbackMixin: ("OP_CONTEXT_MENU_WAIT_SEC",),
    DocumentRunMixin: "DOC_",
}


def _owner_of(name):
    for cls, spec in OWNER.items():
        if (name.startswith(spec) if isinstance(spec, str) else name in spec):
            return cls
    return OpEndMixin                       # остальные OP_* — пороги конца операции


def test_threshold_values_unchanged():
    app = r7mod.R7Testovarka
    assert {k: getattr(app, k) for k in EXPECTED} == EXPECTED


def test_thresholds_declared_on_their_mixin_not_on_app():
    app = r7mod.R7Testovarka
    for name in EXPECTED:
        assert name not in vars(app), f"{name} снова объявлен в R7Testovarka"
        owner = _owner_of(name)
        assert name in vars(owner), f"{name} ожидался у {owner.__name__}"


def test_no_threshold_declared_twice():
    """Один порог — одно объявление: иначе порядок примесей решал бы, какое
    значение читает self."""
    mixins = [c for c in r7mod.R7Testovarka.__mro__[1:] if c is not object]
    for name in EXPECTED:
        holders = [c.__name__ for c in mixins if name in vars(c)]
        assert len(holders) == 1, (name, holders)


def test_cdp_long_timeout_follows_op_guard():
    """Длинная CDP-операция ждёт ответа столько же, сколько её ждёт детектор."""
    assert CdpMixin.CDP_LONG_OP_TIMEOUT_SEC == float(OpEndMixin.OP_MAX_WAIT_SEC)
