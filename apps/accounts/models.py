"""The customer account.

This is the one model in the project that must be right the first time. Django
permits swapping AUTH_USER_MODEL only while the database has no tables; once
anything holds a foreign key to it, changing it means rebuilding the schema by
hand on a live system with real orders in it. Hence the ordering rule: this
migration is 0001 of the project's history, and no other app has models yet.

The mobile number is the identity (ADR-0031): a customer signs in with it and a
code sent by SMS. Email is an optional address for order mail.
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.accounts.validators import normalize_mobile_phone


class UserManager(BaseUserManager["User"]):
    """Creates users with a normalised phone and email and no username concept."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """Lowercase the WHOLE address, not just the domain.

        Django's own normalize_email lowercases the domain only, because RFC 5321
        says the local part is case-sensitive. No mail provider a customer of this
        shop actually uses honours that.
        """
        return super().normalize_email(email or "").lower().strip()

    def _create(self, email: str, password: str | None, **extra: object) -> User:
        phone = normalize_mobile_phone(str(extra.pop("phone", None) or "")) or None
        email = self.normalize_email(email)
        if not phone and not email:
            raise ValueError("A phone number or an email address is required.")
        user = self.model(phone=phone, email=email, **extra)
        # set_password hashes; set_unusable_password marks the account code-only,
        # which is every customer's state.
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str = "", password: str | None = None, **extra: object) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(
        self, email: str = "", password: str | None = None, **extra: object
    ) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_verified", True)
        if not (extra["is_staff"] and extra["is_superuser"]):
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    # NULL for an account with no number (one made before the phone was the
    # identity): it cannot sign in. One normal form, enforced in the database,
    # because the panel, a shell and an import reach the table without the manager.
    phone = models.CharField("شماره‌ی موبایل", max_length=20, unique=True, null=True, blank=True)

    # A contact address for order mail, never a way in, so neither unique nor
    # confirmed: two accounts may share one.
    email = models.EmailField("ایمیل", max_length=254, blank=True, default="")

    # Set by any flow that proves the number receives messages: today the sign-in
    # code, which is why it doubles as verification.
    is_verified = models.BooleanField("شماره‌ی موبایل تأیید شده", default=False)

    # Present in 0001 because these are part of the identity shape and belong in
    # the table that must not be redesigned. Set by the linking flow.
    telegram_id = models.BigIntegerField("شناسه‌ی تلگرام", null=True, blank=True, unique=True)
    telegram_username = models.CharField("نام کاربری تلگرام", max_length=64, blank=True, default="")
    telegram_linked_at = models.DateTimeField("زمان اتصال تلگرام", null=True, blank=True)

    is_active = models.BooleanField("فعال", default=True)
    is_staff = models.BooleanField("دسترسی پنل", default=False)
    date_joined = models.DateTimeField("تاریخ عضویت", default=timezone.now)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(phone__isnull=True) | models.Q(phone__regex=r"^09[0-9]{9}$"),
                name="user_phone_normal_form",
            ),
        ]

    def __str__(self) -> str:
        return self.get_username()

    def get_username(self) -> str:
        # Wherever Django or the panel names an account: its number, or the email
        # of an account with none.
        return self.phone or self.email

    def clean(self) -> None:
        # One normal form for the number. Not AbstractBaseUser.clean, which writes
        # get_username() back into the phone, email and all.
        self.phone = normalize_mobile_phone(self.phone or "") or None

    def mark_verified(self) -> None:
        """Idempotent: called on every code sign-in, not just the first."""
        if not self.is_verified:
            self.is_verified = True
            self.save(update_fields=["is_verified"])
