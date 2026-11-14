"""Forms are the trust boundary: everything arriving from a browser lands here."""

from __future__ import annotations

from django import forms

from apps.accounts import otp
from apps.accounts.models import User
from apps.accounts.validators import normalize_mobile_phone


class ChannelForm(forms.Form):
    channel = forms.ChoiceField(
        label="روش دریافت کد", choices=[("sms", "پیامک"), ("telegram", "تلگرام")],
        initial="sms", required=False, widget=forms.RadioSelect,
    )

    def clean_channel(self) -> str:
        return str(self.cleaned_data["channel"] or "sms")


class PhoneForm(ChannelForm):
    """An Iranian mobile number only, in the eleven-digit
    09 form: Persian or Arabic digits, +98 or 0098, spaces, dashes and brackets are
    accepted on the way in and never kept."""

    phone = forms.CharField(
        label="شماره‌ی موبایل",
        # Room for what normalize_mobile_phone strips.
        max_length=32,
        error_messages={"required": "شماره‌ی موبایل را وارد کنید."},
        widget=forms.TextInput(
            attrs={
                "class": "field__input",
                "type": "tel",
                "inputmode": "tel",
                "autocomplete": "tel",
                "dir": "ltr",
            }
        ),
    )

    def clean_phone(self) -> str:
        phone = normalize_mobile_phone(self.cleaned_data["phone"])
        if not phone:
            raise forms.ValidationError("شماره‌ی موبایل را وارد کنید.")
        return phone


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

        A customer copying a code exactly as a page showed it, in Persian digits,
        would otherwise be told, correctly and uselessly, that it is wrong.
        Normalising in the FORM rather than the view means every entry point gets
        it — the sign-in page, checkout's inline sign-in, the Telegram bot.
        """
        value = otp.normalize_digits(self.cleaned_data["code"])
        if not (value.isdigit() and len(value) == otp.CODE_LENGTH):
            raise forms.ValidationError("کد باید ۶ رقم باشد.")
        return value


class ProfileForm(forms.ModelForm):
    """The part of the account a customer edits: the email order mail goes to.

    Optional, and only a mail address: the phone is the way in, changed through
    support, so nothing here can move the account to someone else.
    """

    email = forms.EmailField(
        label="ایمیل",
        required=False,
        max_length=254,
        help_text="رسید و خبرهای سفارش را به این ایمیل می‌فرستیم.",
        error_messages={"invalid": "این ایمیل درست نیست."},
        widget=forms.EmailInput(
            attrs={
                "class": "field__input",
                "autocomplete": "email",
                "inputmode": "email",
                "dir": "ltr",
            }
        ),
    )

    class Meta:
        model = User
        fields = ["email"]

    def clean_email(self) -> str:
        return otp.normalize_email(self.cleaned_data.get("email") or "")
