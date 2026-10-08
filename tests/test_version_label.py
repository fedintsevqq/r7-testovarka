"""Подпись версии в отчётах (r7.versions.version_label): у 2026.3 в
DisplayName нет номера сборки, и все отчёты подписывались одинаково."""
import pytest

from r7.versions import version_label

NAME = "Р7-Офис. Профессиональный (десктопная версия)"


@pytest.mark.parametrize("info, label", [
    ({"name": NAME, "version": "2026.3.2.3229"}, f"{NAME} 2026.3.2.3229"),
    ({"name": f"{NAME} 2026.2.2.2923 (x64)", "version": "2026.2.2.2923"},
     f"{NAME} 2026.2.2.2923"),                            # Inno: номер в названии, «(x64)» — нет
    ({"name": NAME, "version": ""}, NAME),
    ({"name": "", "version": "2026.3.2.3229"}, "2026.3.2.3229"),
    ({"name": None, "version": None}, None),
    (None, None),
])
def test_version_label(info, label):
    assert version_label(info) == label


@pytest.mark.parametrize("raw, canon", [
    (f"{NAME} 2026.3.2.3229 (x64)", f"{NAME} 2026.3.2.3229"),
    (f"{NAME} 2026.3.2.3229", f"{NAME} 2026.3.2.3229"),
    ("2026.2.1.2269 (X86) ", "2026.2.1.2269"),
    ("свой ярлык (вариант)", "свой ярлык (вариант)"),
    (None, None), ("", ""),
])
def test_canonical_version_drops_arch_suffix(raw, canon):
    """Одна сборка от msi и от Inno — одна версия (живой Batch 08.10.2026)."""
    from r7.versions import canonical_version
    assert canonical_version(raw) == canon
