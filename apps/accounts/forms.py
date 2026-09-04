"""Forms are the trust boundary: everything arriving from a browser lands here."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth import forms as auth_forms

from apps.accounts import otp


class EmailForm(forms.Form):
    email = forms.EmailField(
        label="ایمیل",
        max_length=254,
        error_messages={
            "required": "ایمیل را وارد کنید.",
            "invalid": "این ایمیل معتبر نیست.",
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


class PasswordLoginForm(EmailForm):
    password = forms.CharField(
        label="رمز عبور",
        strip=False,
        error_messages={"required": "رمز عبور را وارد کنید."},
        widget=forms.PasswordInput(
            attrs={"class": "field__input", "autocomplete": "current-password"}
        ),
    )


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


class StyledPasswordChangeForm(auth_forms.PasswordChangeForm):
    """Django's PasswordChangeForm with this project's field styling and wording.

    Two reasons this subclass has to exist rather than the template patching it:

    - Tailwind's preflight sets `border-width: 0` on every element, so an input
      that does not carry `field__input` renders with NO BORDER AT ALL. Django's
      own widgets carry no class, so the two new-password boxes were invisible on
      screen while still being focusable and submittable. Nothing errors; the page
      just silently becomes unusable.
    - Django's Persian translations say «گذرواژه» where the rest of this UI says
      «رمز عبور». Two words for one thing on the same page reads as two different
      systems bolted together, which is exactly the impression the shop cannot
      afford.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        labels = {
            "old_password": "رمز عبور فعلی",
            "new_password1": "رمز عبور جدید",
            "new_password2": "تکرار رمز عبور جدید",
        }
        for name, field in self.fields.items():
            field.label = labels.get(name, field.label)
            field.help_text = ""
            field.widget.attrs["class"] = "field__input"


class StyledSetPasswordForm(auth_forms.SetPasswordForm):
    """The same, for an account that has no password yet — an OTP-only customer
    setting one for the first time. Django's SetPasswordForm asks for no current
    password, which is correct: there isn't one to ask for."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        labels = {"new_password1": "رمز عبور", "new_password2": "تکرار رمز عبور"}
        for name, field in self.fields.items():
            field.label = labels.get(name, field.label)
            field.help_text = ""
            field.widget.attrs["class"] = "field__input"
