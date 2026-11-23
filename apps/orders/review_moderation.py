from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.orders.models import Review


class HideReason(models.TextChoices):
    ABUSE_SPAM = "abuse_spam", _("توهین یا هرزنامه")
    PRIVATE_INFORMATION = "private_information", _("اطلاعات شخصی یا رمز ورود")


# The panel translates these fixed messages when it renders the history.
_HIDE_LOG_MESSAGE = {
    HideReason.ABUSE_SPAM: "Review hidden for abuse or spam.",
    HideReason.PRIVATE_INFORMATION: "Review hidden for personal information or a sign-in secret.",
}


@transaction.atomic
def moderate_review(
    *, review_id: int, actor: User, expected_status: str, decision: str, reason: str
) -> None:
    if decision == "publish" and not reason:
        sources = {Review.Status.PENDING}
        target = Review.Status.PUBLISHED
        message = "Review published."
    elif decision == "hide" and reason in HideReason.values:
        sources = {Review.Status.PENDING, Review.Status.PUBLISHED}
        target = Review.Status.HIDDEN
        message = _HIDE_LOG_MESSAGE[HideReason(reason)]
    else:
        raise ValidationError(
            _(
                "این تصمیم درباره‌ی نظر ثبت نشد، چون فرم کامل نبود یا برای پنهان کردن نظر"
                " دلیلی انتخاب نشده بود."
            )
        )
    if expected_status not in sources:
        raise ValidationError(
            _(
                "این نظر در وضعیتی نیست که این تصمیم برایش ثبت شود و برای دیدن وضعیت فعلی آن،"
                " صفحه را دوباره باز کنید."
            )
        )

    review = Review.objects.select_for_update().only("status").get(pk=review_id)
    if review.status != expected_status:
        raise ValidationError(
            _(
                "وضعیت این نظر پس از باز شدن این صفحه تغییر کرده است و برای دیدن وضعیت فعلی آن، صفحه را دوباره باز کنید."
            )
        )
    review.status = target
    review.save(update_fields=["status"])
    LogEntry.objects.log_actions(
        user_id=actor.pk,
        queryset=[review],
        action_flag=CHANGE,
        change_message=message,
    )
