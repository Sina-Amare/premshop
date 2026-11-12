import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

import jdatetime
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.otp import normalize_email
from apps.catalog.inputs import normalize_customer_input
from apps.core.formatting import format_jalali, format_toman, to_latin_digits, to_persian_digits
from apps.orders.clock import TEHRAN
from apps.orders.selectors import item_numbers
from apps.orders.services import Purchase
from apps.payments.models import Refund


class GroupedTomanField(forms.DecimalField):
    def to_python(self, value: Any) -> Any:
        # The page prints totals grouped (۳۹۹,۹۰۰); the service still demands an exact match.
        if isinstance(value, str):
            value = re.sub(r"[,،٬\s]", "", value)
        return super().to_python(value)


class ManualPaymentForm(forms.Form):
    amount = GroupedTomanField(
        label=_("مبلغ دریافتی (تومان)"),
        max_digits=12,
        decimal_places=0,
        min_value=Decimal(1),
        widget=forms.TextInput(attrs={"inputmode": "numeric", "dir": "ltr"}),
    )
    bank_ref = forms.CharField(
        label=_("شماره‌ی پیگیری بانک"),
        max_length=64,
        widget=forms.TextInput(attrs={"dir": "ltr", "autocomplete": "off"}),
    )


class JalaliDateField(forms.Field):
    def to_python(self, value: Any) -> date | None:
        if value in self.empty_values:
            return None
        text = to_latin_digits(str(value).strip())
        found = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", text)
        try:
            if found is None:
                raise ValueError
            return jdatetime.date(*map(int, found.groups())).togregorian()
        except ValueError, OverflowError:
            raise ValidationError(_("تاریخ را مثل ۱۴۰۵/۰۹/۱۵ بنویسید.")) from None


class ReconciliationForm(forms.Form):
    day = JalaliDateField(label=_("روز (شمسی)"), widget=forms.TextInput(attrs={"dir": "ltr"}))


class RefundTransferForm(ManualPaymentForm):
    refund_ids = forms.TypedMultipleChoiceField(
        label=_("بازپرداخت‌های این واریز"), coerce=int, widget=forms.CheckboxSelectMultiple
    )
    destination_kind = forms.ChoiceField(label=_("نوع مقصد"), choices=Refund.Destination.choices)
    destination_last4 = forms.RegexField(
        label=_("چهار رقم آخر مقصد"),
        regex=r"^\d{4}$",
        max_length=4,
        widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "numeric", "autocomplete": "off"}),
    )
    bank_date = JalaliDateField(
        label=_("تاریخ واریز (شمسی)"), widget=forms.TextInput(attrs={"dir": "ltr"})
    )
    bank_time = forms.TimeField(
        label=_("ساعت واریز (تهران، با ثانیه)"),
        input_formats=["%H:%M:%S"],
        widget=forms.TextInput(attrs={"dir": "ltr"}),
    )
    field_order = [
        "refund_ids",
        "amount",
        "destination_kind",
        "destination_last4",
        "bank_ref",
        "bank_date",
        "bank_time",
    ]

    def __init__(self, *args: Any, refunds: list[Refund], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["amount"].label = _("مبلغ واریزشده (تومان)")
        # Each item by its place in its order, never its database id.
        places = item_numbers({row.order_item.order_id for row in refunds})
        cast(forms.TypedMultipleChoiceField, self.fields["refund_ids"]).choices = [
            (
                row.pk,
                _("آیتم %(item)s · %(amount)s · %(status)s")
                % {
                    "item": f"\u2068{row.order_item.product_snapshot.get('name', '')}\u2069؛ \u2068{row.order_item.product_snapshot.get('plan_title', '')}\u2069 ({to_persian_digits(places[row.order_item_id])})",
                    "amount": format_toman(row.amount),
                    "status": row.get_status_display(),
                },
            )
            for row in refunds
            if self.is_bound or row.status == Refund.Status.OWED
        ]
        now = timezone.localtime(timezone.now(), TEHRAN)
        self.initial.update(
            bank_date=format_jalali(now, with_month_name=False), bank_time=now.strftime("%H:%M:%S")
        )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if not self.errors:
            data["executed_at"] = datetime.combine(data["bank_date"], data["bank_time"], TEHRAN)
        return data


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
    email = forms.EmailField(
        label="ایمیل (اختیاری)",
        max_length=254,
        required=False,
        help_text="رسید و خبرهای سفارش را به این ایمیل می‌فرستیم.",
        error_messages={"invalid": "این ایمیل درست نیست."},
        widget=forms.EmailInput(
            attrs={"class": "field__input", "dir": "ltr", "autocomplete": "email"}
        ),
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
                kind = "ایمیل" if line.plan.input_type == "email" else "نام کاربری"
                self.fields[name] = forms.CharField(
                    label=line.plan.activation_input_label,
                    help_text=(
                        f"امکان تغییر {kind} پس از ثبت سفارش وجود ندارد. "
                        f"پیش از ثبت نهایی از درستی {kind} وارد شده اطمینان حاصل کنید."
                    ),
                    max_length=254 if line.plan.input_type == "email" else 151,
                    widget=forms.TextInput(
                        attrs={
                            "class": "field__input",
                            "dir": "ltr",
                            "autocomplete": "off",
                            "type": "email" if line.plan.input_type == "email" else "text",
                            # Set here, so Django will not add the hint's id by itself.
                            "aria-describedby": f"{name}_product id_{name}_helptext",
                        }
                    ),
                )
                self.input_fields.append((line, self[name]))

    def clean_email(self) -> str:
        return normalize_email(self.cleaned_data["email"])

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
