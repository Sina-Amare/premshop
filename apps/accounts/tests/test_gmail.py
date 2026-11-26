import pytest
from django.core.exceptions import ValidationError

from apps.accounts import otp


@pytest.mark.parametrize(
    ("address", "expected"),
    [(" Name+Tag@GmAiL.CoM ", "name+tag@gmail.com"), ("", "")],
)
def test_gm_accepts_gmail_and_optional_blank(address, expected):
    assert otp.normalize_gmail(address) == expected


@pytest.mark.parametrize(
    "address",
    [
        "missing-at",
        "@gmail.com",
        "name@googlemail.com",
        "name@gmail.co",
        "name@gmail.com.evil.com",
        "name@xn--gmal-51d.com",
        "name@gmaіl.com",
        "name@gmail.com.",
        "a" * 245 + "@gmail.com",
    ],
)
def test_gm_rejects_invalid_or_non_gmail_addresses(address):
    with pytest.raises(ValidationError) as invalid:
        otp.normalize_gmail(address)
    assert invalid.value.messages == ["ایمیل Gmail را کامل و به شکل name@gmail.com وارد کنید."]


def test_gm_rejects_blank_when_address_is_required():
    with pytest.raises(ValidationError):
        otp.normalize_gmail("", required=True)
