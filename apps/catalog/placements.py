"""Where each editable catalog value appears or changes behavior."""

from django.utils.translation import gettext as _


def field_placements(model: str) -> dict[str, tuple[str, str]]:
    groups = {
        "product": [
            ("category", "card", _("دسته‌بندی روی کارت محصول و مسیر بالای صفحه")),
            ("brand_colour", "card", _("رنگ زمینه‌ی کارت محصول؛ رنگ خود لوگو تغییر نمی‌کنه.")),
            ("name,image", "header", _("بالای صفحه‌ی محصول و کارت محصول")),
            ("slug", "", _("بخش آخر لینک محصول؛ اگه عوضش کنید، لینک قبلی دیگه باز نمی‌شه.")),
            (
                "status",
                "",
                _("فعال روی سایت دیده می‌شه؛ پیش‌نویس و ناموجود از فهرست‌ها پنهان می‌مونن."),
            ),
            (
                "delivery_type,warranty,renewable,delivery_hours",
                "facts",
                _("خلاصه‌ی شرایط و توضیح تحویل در صفحه‌ی محصول"),
            ),
            ("delivery_note", "questions", _("خلاصه‌ی شرایط و توضیح تحویل در صفحه‌ی محصول")),
            (
                "delivery_template",
                "",
                _("فقط برای آماده‌کردن پیام تحویل؛ مشتری توی صفحه‌ی محصول نمی‌بینه."),
            ),
            (
                "short_description",
                "introduction",
                _("معرفی کوتاه زیر نام محصول؛ توضیح پیش‌فرض نتایج جستجو هم هست."),
            ),
            (
                "usage_notes,refund_note",
                "before-buying",
                _("بخش پیش از خرید؛ نکات خالی یا «موضوعیت ندارد» نمایش داده نمی‌شن."),
            ),
            ("time_sensitive", "", _("موقع انتشار اثر داره؛ روی سایت چیزی نشون نمی‌ده.")),
            ("facts_checked_on", "", _("فقط توی پنل؛ زمان بررسی اطلاعات برای انتشار محصول.")),
            ("sources", "", _("منابع بررسی فقط توی پنل می‌مونن.")),
            ("aliases", "", _("برای پیدا شدن محصول با نام‌های دیگه؛ روی صفحه نمایش داده نمی‌شه.")),
            (
                "seo_title,seo_description",
                "seo",
                _("نمای تقریبی نتیجه در موتور جستجو؛ متن نهایی رو موتور جستجو انتخاب می‌کنه."),
            ),
        ],
        "plan": [
            ("product", "header", _("این پلن زیر همین محصول میاد.")),
            (
                "title,sale_price,promo_price,promo_starts_at,promo_ends_at,is_available,stock_quantity",
                "plans",
                _("انتخاب پلن و قیمت محصول؛ تخفیف فقط توی بازه‌ی خودش دیده می‌شه."),
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
                "cost_price,supplier_url",
                "",
                _("فقط توی پنل، برای خرید و حساب سود؛ مشتری نمی‌بینه."),
            ),
            (
                "duration_days",
                "",
                _("مدت واقعی اشتراک، برای قیمت ماهانه و سفارش؛ عنوانی که مشتری می‌بینه جداست."),
            ),
            (
                "price_confirmed_at",
                "",
                _("فقط توی پنل؛ زمان تأیید قیمت برای پیگیری بررسی قیمت‌ها."),
            ),
            ("sort_order", "plans", _("ترتیب پلن‌ها در صفحه‌ی محصول")),
        ],
        "specs": [
            ("title,value", "specs", _("جدول مشخصات محصول")),
            ("is_key", "header", _("اگه روشن باشه، این مشخصه کنار نام محصول هم دیده می‌شه.")),
            ("sort_order", "specs", _("ترتیب ردیف‌های جدول مشخصات")),
        ],
        "sections": [
            (
                "kind,title,body,image,image_alt,sort_order",
                "sections",
                _(
                    "همین بخش در توضیحات محصول؛ پیش از خرید جای ثابت داره، بقیه با ترتیب شما نمایش داده می‌شن."
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
                _("عنوان و معرفی صفحه‌ی دسته‌بندی؛ نام در منوی سایت هم دیده می‌شه."),
            ),
            ("slug", "", _("بخش آخر لینک دسته‌بندی؛ اگه عوضش کنید، لینک قبلی دیگه باز نمی‌شه.")),
            (
                "sort_order",
                "navigation",
                _("ترتیب دسته‌ها در منوی سایت؛ دسته‌ای که محصول فعال نداره وارد منو نمی‌شه."),
            ),
            (
                "seo_title,seo_description",
                "seo",
                _("نمای تقریبی نتیجه در موتور جستجو؛ متن نهایی رو موتور جستجو انتخاب می‌کنه."),
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
