from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.core.config import library_settings

from .models import Member


def next_member_id(prefix: str, year: int) -> str:
    stem = f"{prefix}-{year}-"
    last = (
        Member.objects.filter(member_id__startswith=stem)
        .order_by("-member_id")
        .values_list("member_id", flat=True)
        .first()
    )
    number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{stem}{number:05d}"


def create_member(
    *,
    full_name: str,
    email: str | None,
    password: str | None = None,
    phone: str = "",
    address: str = "",
    date_of_birth=None,
    created_by=None,
    email_verified: bool = False,
) -> Member:
    """Create the user account and member profile with a unique member ID
    such as LIB-2026-00482. Retries if two registrations race for an ID."""
    prefix = library_settings().member_id_prefix
    year = timezone.localdate().year
    for _attempt in range(6):
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    full_name=full_name.strip(),
                    role=Role.MEMBER,
                    email_verified_at=timezone.now() if email_verified else None,
                )
                return Member.objects.create(
                    user=user,
                    member_id=next_member_id(prefix, year),
                    phone=phone.strip(),
                    address=address.strip(),
                    date_of_birth=date_of_birth,
                    created_by=created_by,
                )
        except IntegrityError:
            if email and User.objects.filter(email=email.strip().lower()).exists():
                raise
            continue
    raise IntegrityError("Could not allocate a unique member ID.")


def find_member(code: str) -> Member | None:
    """Look up a member from a scanned library card / typed member ID."""
    code = (code or "").strip().upper()
    if not code:
        return None
    return Member.objects.select_related("user").filter(member_id=code).first()
