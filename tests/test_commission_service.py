from decimal import Decimal

import pytest

from backend.app.commission_service import _commission_for, _money


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100", Decimal("100.00")),
        ("10.005", Decimal("10.01")),
        ("10.004", Decimal("10.00")),
        (Decimal("0.01"), Decimal("0.01")),
    ],
)
def test_money_normalizes_to_two_decimal_places(raw, expected):
    assert _money(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "NaN", "Infinity", "-Infinity", "invalid"])
def test_money_rejects_non_positive_or_invalid_amounts(raw):
    with pytest.raises((ValueError, ArithmeticError)):
        _money(raw)


@pytest.mark.parametrize(
    ("basis", "rate_bps", "expected"),
    [
        ("1000.00", 500, Decimal("50.00")),
        ("1234.56", 500, Decimal("61.73")),
        ("100.00", 10000, Decimal("100.00")),
        ("999.99", 0, Decimal("0.00")),
        ("0.05", 1000, Decimal("0.01")),
    ],
)
def test_commission_calculation_uses_basis_points_and_half_up_rounding(
    basis, rate_bps, expected
):
    assert _commission_for(Decimal(basis), rate_bps) == expected
