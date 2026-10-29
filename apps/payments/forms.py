from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.validators import normalize_mobile_phone
from apps.catalog.inputs import normalize_customer_input
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
        self.input_types: dict[str, str] = {}
        if purchase:
            for line in purchase.lines:
                if line.plan.input_type == "none":
                    continue
                name = f"customer_input_{line.plan.pk}"
                self.input_types[name] = line.plan.input_type
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

    def clean_phone(self) -> str:
        return normalize_mobile_phone(self.cleaned_data["phone"])

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for name, kind in self.input_types.items():
            if name not in cleaned:
                continue
            try:
                cleaned[name] = normalize_customer_input(kind, cleaned[name])
            except ValidationError as error:
                self.add_error(name, error)
        return cleaned
