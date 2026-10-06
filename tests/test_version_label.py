"""Подпись версии в отчётах (r7.versions.version_label): у 2026.3 в
DisplayName нет номера сборки, и все отчёты подписывались одинаково."""
import pytest

from r7.versions import version_label

NAME = "Р7-Офис. Профессиональный (десктопная версия)"


@pytest.mark.parametrize("info, label", [
    ({"name": NAME, "version": "2026.3.2.3229"}, f"{NAME} 2026.3.2.3229"),
    ({"name": f"{NAME} 2026.2.2.2923 (x64)", "version": "2026.2.2.2923"},
     f"{NAME} 2026.2.2.2923 (x64)"),                      # старые сборки — как было
    ({"name": NAME, "version": ""}, NAME),
    ({"name": "", "version": "2026.3.2.3229"}, "2026.3.2.3229"),
    ({"name": None, "version": None}, None),
    (None, None),
])
def test_version_label(info, label):
    assert version_label(info) == label
