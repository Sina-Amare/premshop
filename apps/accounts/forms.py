"""Forms are the trust boundary: everything arriving from a browser lands here."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from django import forms
from django.contrib.auth import forms as auth_forms

from apps.accounts import otp
from apps.accounts.models import User


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
                "class": "input input--ltr",
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
            attrs={"class": "input input--ltr", "autocomplete": "current-password"}
        ),
    )


class CodeForm(forms.Form):
    code = forms.CharField(
        label="کد ورود",
        error_messages={"required": "کد را وارد کنید."},
        widget=forms.TextInput(
            attrs={
                "class": "input input--ltr",
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


# The four validators in AUTH_PASSWORD_VALIDATORS, in one breath. If a validator is
# ever added or removed there, this line is wrong — hence the test that renders it.
PASSWORD_HINT = (
    "حداقل ۸ کاراکتر و نه فقط عدد. نباید رایج یا شبیه ایمیل شما باشد."  # noqa: S105  # nosec B105
)


class RuleErrorsOnTheFirstField(auth_forms.SetPasswordMixin):
    """Django checks the password rules while cleaning the repeat field, so their
    errors landed under «تکرار رمز عبور». They belong under the password they are
    about, beside the hint that states the rules; a mismatch stays on the repeat."""

    # A field name, not a password; bandit reads the parameter's name.
    def validate_password_for_user(
        self, user: User, password_field_name: str = ""  # nosec B107
    ) -> None:
        super().validate_password_for_user(user, "new_password1")


class StyledPasswordChangeForm(RuleErrorsOnTheFirstField, auth_forms.PasswordChangeForm):
    """Django's PasswordChangeForm with this project's field styling and wording.

    Two reasons this subclass has to exist rather than the template patching it:

    - Tailwind's preflight sets `border-width: 0` on every element, so an input
      that does not carry `input` renders with NO BORDER AT ALL. Django's
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
        self.error_messages = {
            **self.error_messages,
            "password_incorrect": "رمز عبور فعلی اشتباه است.",  # nosec B105
            "password_mismatch": "تکرار رمز عبور یکسان نیست.",  # nosec B105
        }
        labels = {
            "old_password": "رمز عبور فعلی",  # nosec B105
            "new_password1": "رمز عبور جدید",
            "new_password2": "تکرار رمز عبور جدید",
        }
        for name, field in self.fields.items():
            field.label = labels.get(name, field.label)
            # Django's default help text is a bulleted list in translated Persian.
            # One line, matching the four validators in settings exactly, on the
            # field where the rule bites; nothing on the confirmation field.
            field.help_text = PASSWORD_HINT if name == "new_password1" else ""
            field.widget.attrs["class"] = "input input--ltr"


class StyledSetPasswordForm(RuleErrorsOnTheFirstField, auth_forms.SetPasswordForm):
    """The same, for an account that has no password yet — an OTP-only customer
    setting one for the first time. Django's SetPasswordForm asks for no current
    password, which is correct: there isn't one to ask for."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.error_messages = {
            **self.error_messages,
            "password_mismatch": "تکرار رمز عبور یکسان نیست.",  # nosec B105
        }
        labels = {"new_password1": "رمز عبور", "new_password2": "تکرار رمز عبور"}
        for name, field in self.fields.items():
            field.label = labels.get(name, field.label)
            field.help_text = PASSWORD_HINT if name == "new_password1" else ""
            field.widget.attrs["class"] = "input input--ltr"


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
                "class": "input input--ltr",
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
        # Direction marks and other invisible format characters come along with
        # a number copied from a contact card or a chat on a right-to-left phone.
        typed = otp.normalize_digits(self.cleaned_data.get("phone") or "")
        value = "".join(ch for ch in typed if unicodedata.category(ch) != "Cf")
        if not value:
            return ""
        # [0-9], not \d or isdigit(): both accept digits from other scripts, and
        # isdigit() accepts «²».
        mobile = re.fullmatch(r"(?:\+98|0098|98)?0?(9[0-9]{9})", re.sub(r"[\s()-]", "", value))
        if not mobile:
            raise forms.ValidationError("شماره‌ی موبایل باید ۱۱ رقم باشد و با ۰۹ شروع شود.")
        return "0" + mobile.group(1)
