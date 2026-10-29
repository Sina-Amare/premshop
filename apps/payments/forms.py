from typing import Any

from django import forms

from apps.orders.services import CONSENT_TEXT, Purchase


class CheckoutForm(forms.Form):
    quote = forms.CharField(max_length=20000, widget=forms.HiddenInput)
    checkout_id = forms.UUIDField(widget=forms.HiddenInput)
    discount_code = forms.CharField(
        label="کد تخفیف",
        max_length=32,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "field__input", "dir": "ltr", "autocomplete": "off"}
        ),
    )
    phone = forms.CharField(
        label="شماره‌ی موبایل (اختیاری)",
        max_length=32,
        required=False,
        help_text="این شماره فقط در صورتی ذخیره می‌شود که شماره‌ای در حساب شما ثبت نشده باشد.",
        widget=forms.TextInput(
            attrs={"class": "field__input", "dir": "ltr", "type": "tel", "autocomplete": "tel"}
        ),
    )
    activation_consent = forms.BooleanField(
        label=CONSENT_TEXT,
        required=True,
        error_messages={"required": "شروع فعال‌سازی پس از تأیید پرداخت را تأیید کنید."},
    )

    def __init__(self, *args: Any, purchase: Purchase | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.input_fields = []
        if purchase:
            for line in purchase.lines:
                if line.plan.input_type == "none":
                    continue
                name = f"customer_input_{line.plan.pk}"
                self.fields[name] = forms.CharField(
                    label=line.plan.activation_input_label,
                    max_length=254 if line.plan.input_type == "email" else 151,
                    widget=forms.TextInput(
                        attrs={
                            "class": "field__input",
                            "dir": "ltr",
                            "autocomplete": "off",
                            "type": "email" if line.plan.input_type == "email" else "text",
                            "aria-describedby": f"{name}_product",
                        }
                    ),
                )
                self.input_fields.append((line, self[name]))
