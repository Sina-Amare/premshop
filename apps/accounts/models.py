"""The customer account.

This is the one model in the project that must be right the first time. Django
permits swapping AUTH_USER_MODEL only while the database has no tables; once
anything holds a foreign key to it, changing it means rebuilding the schema by
hand on a live system with real orders in it. Hence the ordering rule: this
migration is 0001 of the project's history, and no other app has models yet.
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager["User"]):
    """Creates users with a normalised email and no username concept."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """Lowercase the WHOLE address, not just the domain.

        Django's own normalize_email lowercases the domain only, because RFC 5321
        says the local part is case-sensitive. No mail provider a customer of this
        shop actually uses honours that, so `Ali@gmail.com` and `ali@gmail.com`
        would become two accounts — one holding the order, the other holding the
        person, and a support conversation nobody wins.
        """
        return super().normalize_email(email or "").lower().strip()

    def _create(self, email: str, password: str | None, **extra: object) -> User:
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra)
        # set_password hashes; set_unusable_password marks the account
        # OTP-only, which is the state a checkout-created account starts in.
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: object) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: object) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_verified", True)
        if not (extra["is_staff"] and extra["is_superuser"]):
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("ایمیل", max_length=254, unique=True)

    # Set by any flow that proves the address receives mail — today that is the
    # login OTP, which is why OTP login doubles as verification (ADR-0012) and why
    # there is no separate "click to verify" mail to be ignored.
    is_verified = models.BooleanField("ایمیل تأیید شده", default=False)

    # Collected at checkout, never at registration: asking for a phone number
    # before a purchase is friction on a base already unsure about the shop (D6).
    # blank string rather than NULL so "no phone" has exactly one representation.
    phone = models.CharField("شماره تماس", max_length=20, blank=True, default="")

    # Reserved for S8. Present in 0001 because adding a column later is a trivial
    # migration, but these are part of the identity shape and belong in the table
    # that must not be redesigned. Unused until the linking flow exists.
    telegram_id = models.BigIntegerField("شناسه تلگرام", null=True, blank=True, unique=True)
    telegram_username = models.CharField("نام کاربری تلگرام", max_length=64, blank=True, default="")
    telegram_linked_at = models.DateTimeField("زمان اتصال تلگرام", null=True, blank=True)

    is_active = models.BooleanField("فعال", default=True)
    is_staff = models.BooleanField("دسترسی پنل", default=False)
    date_joined = models.DateTimeField("تاریخ عضویت", default=timezone.now)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"
        constraints = [
            # unique=True on the field catches exact duplicates; this catches the
            # ones that matter — Ali@ vs ali@. The manager lowercases on the way
            # in, but the manager is not the only door: the admin, a shell, and a
            # future data import all reach the table directly. The guarantee has
            # to live in the database.
            models.UniqueConstraint(Lower("email"), name="user_email_case_insensitive_unique"),
        ]

    def __str__(self) -> str:
        return self.email

    def mark_verified(self) -> None:
        """Idempotent: called on every OTP login, not just the first."""
        if not self.is_verified:
            self.is_verified = True
            self.save(update_fields=["is_verified"])
