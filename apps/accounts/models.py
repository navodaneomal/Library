from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    MEMBER = "member", "Member"
    ASSISTANT = "assistant", "Assistant"
    LIBRARIAN = "librarian", "Librarian"
    ADMIN = "admin", "Administrator"


STAFF_ROLES = frozenset({Role.ASSISTANT, Role.LIBRARIAN, Role.ADMIN})


def normalize_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    return email or None


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email=None, password=None, **extra):
        email = normalize_email(email)
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra):
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("full_name", "Administrator")
        return self.create_user(email, password, **extra)

    def get_by_natural_key(self, username):
        return self.get(email=normalize_email(username))


class User(AbstractBaseUser):
    """One account type for everyone; `role` decides what a person may do.

    Members without an email address (e.g. children registered at the desk)
    are allowed: they simply cannot sign in online or receive emails.
    """

    email = models.EmailField(unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER, db_index=True)
    is_active = models.BooleanField(default=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name or self.email or f"User #{self.pk}"

    def clean(self):
        super().clean()
        self.email = normalize_email(self.email)

    def save(self, *args, **kwargs):
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    @property
    def is_staff_member(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def first_name(self) -> str:
        return (self.full_name or "").split(" ")[0] or "there"

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.first_name


class RateLimitEvent(models.Model):
    """A counted event (e.g. a failed sign-in). Stored in the database so the
    limit is shared by every worker process."""

    key = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["key", "created_at"])]

    def __str__(self):
        return self.key
