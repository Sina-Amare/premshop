"""Forms are the trust boundary: everything arriving from a browser lands here."""

from __future__ import annotations

from django import forms

from apps.accounts import otp
from apps.accounts.models import User
from apps.accounts.validators import normalize_mobile_phone


class EmailForm(forms.Form):
    email = forms.EmailField(
        label="ایمیل",
        max_length=254,
        error_messages={
            "required": "ایمیل را وارد کنید.",
            "invalid": "این ایمیل درست نیست.",
        },
        widget=forms.EmailInput(
            attrs={
                "class": "field__input",
                "autocomplete": "email",
                "dir": "ltr",
                "inputmode": "email",
            }
        ),
    )

    def clean_email(self) -> str:
        return otp.normalize_email(self.cleaned_data["email"])


class CodeForm(forms.Form):
    code = forms.CharField(
        label="کد ورود",
        error_messages={"required": "کد را وارد کنید."},
        widget=forms.TextInput(
            attrs={
                "class": "field__input field__input--code",
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "dir": "ltr",
                "maxlength": "6",
            }
        ),
    )

    def clean_code(self) -> str:
        """Persian digits to ASCII before anything compares them.

        The email renders the code as ۴۱۸۳۰۵ because that is what an Iranian
        reader expects to see. A customer copying exactly what they were shown
        would otherwise be told, correctly and uselessly, that the code is wrong.
        Normalising in the FORM rather than the view means every entry point gets
        it — the login page, a future checkout-inline flow, the Telegram bot.
        """
        value = otp.normalize_digits(self.cleaned_data["code"])
        if not (value.isdigit() and len(value) == otp.CODE_LENGTH):
            raise forms.ValidationError("کد باید ۶ رقم باشد.")
        return value


class ProfileForm(forms.ModelForm):
    """The parts of the account a customer may edit themselves. Today: the phone.

    An Iranian mobile number only, normalised to the eleven-digit 09 form — the
    shop is Iran-only, and one canonical spelling means the operator can search
    for it. Persian or Arabic digits, +98 or 0098, spaces, dashes and brackets
    are accepted on the way in and never stored. Anything else is refused, never
    dropped: an empty field is the only way to remove a number.
    """

    phone = forms.CharField(
        label="شماره‌ی موبایل",
        required=False,
        # Room for what clean_phone strips; the column keeps the 11 digits.
        max_length=32,
        help_text="برای پیگیری سفارش‌ها، مثلاً ۰۹۱۲۳۴۵۶۷۸۹",
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "inputmode": "tel",
                "autocomplete": "tel",
                "dir": "ltr",
            }
        ),
    )

    class Meta:
        model = User
        fields = ["phone"]

    def clean_phone(self) -> str:
        return normalize_mobile_phone(self.cleaned_data.get("phone") or "")
