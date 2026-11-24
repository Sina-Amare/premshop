"""Where each editable catalog value appears or changes behavior."""

from django.utils.translation import gettext as _


def field_placements(model: str) -> dict[str, tuple[str, str]]:
    groups = {
        "product": [
            ("category", "card", _("دسته‌بندی روی کارت محصول و مسیر بالای صفحه")),
            ("brand_colour", "card", _("این رنگ در پس‌زمینه‌ی کارت محصول دیده می‌شود.")),
            ("name,image", "header", _("بالای صفحه‌ی محصول و کارت محصول")),
            ("slug", "", _("با تغییر نامک، لینک قبلی محصول از کار می‌افتد.")),
            (
                "status",
                "",
                _("فعال روی سایت دیده می‌شود؛ پیش‌نویس و ناموجود از فهرست‌ها پنهان می‌مانند."),
            ),
            (
                "delivery_type,warranty,renewable,delivery_hours",
                "facts",
                _("خلاصه‌ی شرایط و توضیح تحویل در صفحه‌ی محصول"),
            ),
            ("delivery_note", "questions", _("خلاصه‌ی شرایط و توضیح تحویل در صفحه‌ی محصول")),
            (
                "short_description",
                "introduction",
                _("معرفی کوتاه زیر نام محصول؛ توضیح پیش‌فرض نتایج جستجو هم هست."),
            ),
            (
                "usage_notes,refund_note",
                "before-buying",
                _("بخش پیش از خرید؛ نکات خالی یا «موضوعیت ندارد» نمایش داده نمی‌شوند."),
            ),
            ("time_sensitive", "", _("هنگام انتشار اثر دارد؛ روی سایت چیزی نشان نمی‌دهد.")),
            (
                "facts_checked_on",
                "",
                _("فقط در پنل دیده می‌شود؛ هنگام انتشار محصول به کار می‌آید."),
            ),
            ("aliases", "", _("برای پیدا شدن محصول با نام‌های دیگر؛ روی صفحه نمایش داده نمی‌شود.")),
            (
                "seo_title,seo_description",
                "seo",
                _("پیش‌نمایش نتیجه‌ی جستجو. متن نهایی را موتور جستجو انتخاب می‌کند."),
            ),
        ],
        "plan": [
            ("product", "header", _("این پلن زیر همین محصول می‌آید.")),
            (
                "title,sale_price,promo_price,promo_starts_at,promo_ends_at,is_available,stock_quantity",
                "plans",
                _("انتخاب پلن و قیمت محصول؛ تخفیف فقط در بازه‌ی خودش دیده می‌شود."),
            ),
            (
                "max_quantity",
                "quantity",
                _("تعداد مجاز و اطلاعات لازم برای پلن انتخاب‌شده"),
            ),
            (
                "input_type,telegram_required,customer_input_label",
                "customer-input",
                _("تعداد مجاز و اطلاعات لازم برای پلن انتخاب‌شده"),
            ),
            (
                "duration_days",
                "",
                _("مدت واقعی اشتراک در محاسبه‌ی قیمت ماهانه و ثبت سفارش استفاده می‌شود."),
            ),
            (
                "price_confirmed_at",
                "",
                _("فقط در پنل دیده می‌شود؛ زمان آخرین تأیید قیمت را نشان می‌دهد."),
            ),
            ("sort_order", "plans", _("ترتیب پلن‌ها در صفحه‌ی محصول")),
        ],
        "specs": [
            ("title,value", "specs", _("جدول مشخصات محصول")),
            ("is_key", "header", _("اگر روشن باشد، این مشخصه کنار نام محصول هم دیده می‌شود.")),
            ("sort_order", "specs", _("ترتیب ردیف‌های جدول مشخصات")),
        ],
        "sections": [
            (
                "kind,title,body,image,image_alt,sort_order",
                "sections",
                _(
                    "همین بخش در توضیحات محصول؛ پیش از خرید جای ثابت دارد، بقیه با ترتیب شما نمایش داده می‌شوند."
                ),
            ),
        ],
        "questions": [
            (
                "question,answer,sort_order",
                "questions",
                _("همین سؤال و جواب در سؤال‌های پرتکرار محصول"),
            ),
        ],
        "category": [
            (
                "name,description,intro",
                "category",
                _("عنوان و معرفی صفحه‌ی دسته‌بندی؛ نام در منوی سایت هم دیده می‌شود."),
            ),
            (
                "slug",
                "",
                _("با تغییر نامک، لینک قبلی دسته‌بندی از کار می‌افتد."),
            ),
            (
                "sort_order",
                "navigation",
                _("دسته فقط وقتی در منو می‌آید که محصول فعال داشته باشد."),
            ),
            (
                "seo_title,seo_description",
                "seo",
                _("پیش‌نمایش نتیجه‌ی جستجو. متن نهایی را موتور جستجو انتخاب می‌کند."),
            ),
        ],
    }
    result = {}
    for group_name in (
        model,
        *(("plan", "specs", "sections", "questions") if model == "product" else ()),
    ):
        prefix = "plans" if group_name == "plan" and model == "product" else group_name
        for names, target, description in groups[group_name]:
            for name in names.split(","):
                result[f"{prefix}.{name}"] = (target, description)
    return result
