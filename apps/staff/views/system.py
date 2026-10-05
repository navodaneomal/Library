"""Administration: settings, staff accounts, audit log, backups, announcements."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from apps.accounts.models import STAFF_ROLES, Role, User
from apps.catalogue.models import BookCopy
from apps.circulation.models import Loan
from apps.core import audit, backup, config
from apps.core.config import library_settings
from apps.core.forms import LibrarySettingsForm
from apps.core.models import AuditLog
from apps.core.permissions import require_capability
from apps.fees.models import Fee
from apps.members.models import Member
from apps.notifications import notify
from apps.notifications.email import send_test_email
from apps.notifications.models import Announcement, EmailLog
from apps.reports.csv_export import csv_response
from apps.staff.forms import AnnouncementForm, StaffUserForm


@require_capability("settings")
def settings_view(request):
    current = library_settings()
    # Snapshot first: ModelForm validation writes the new values onto the instance.
    snapshot = {f.name: getattr(current, f.name) for f in current._meta.concrete_fields}
    form = LibrarySettingsForm(request.POST or None, instance=current)
    if request.method == "POST" and form.is_valid():
        before = {name: snapshot.get(name) for name in form.changed_data}
        updated = form.save(commit=False)
        updated.updated_by = request.user
        updated.save()
        config.invalidate()
        after = {name: getattr(updated, name) for name in form.changed_data}
        if form.changed_data:
            audit.record(
                request.user,
                "settings.updated",
                "Changed " + ", ".join(form.fields[n].label or n for n in form.changed_data)[:200],
                target=updated,
                changes=audit.diff(before, after),
                request=request,
            )
        messages.success(request, "Settings saved.")
        return redirect("staff:settings")
    email_status = {
        "backend": settings.EMAIL_BACKEND.rsplit(".", 1)[-1].replace("EmailBackend", "") or "SMTP",
        "host": settings.EMAIL_HOST if "smtp" in settings.EMAIL_BACKEND else "",
        "from": settings.DEFAULT_FROM_EMAIL,
        "recent_failures": EmailLog.objects.filter(status="failed").count(),
    }
    from datetime import date, timedelta

    from apps.fees.engine import quote_late_fee

    example_fee = quote_late_fee(
        date(2026, 1, 1),
        date(2026, 1, 1) + timedelta(days=4),
        per_day=current.late_fee_per_day,
        grace_days=current.grace_period_days,
        max_fee=current.max_late_fee,
    )
    return render(
        request, "staff/system/settings.html", {"form": form, "email_status": email_status, "example_fee": example_fee}
    )


@require_capability("settings")
@require_POST
def email_test(request):
    target = request.user.email
    ok, error = send_test_email(target)
    if ok:
        messages.success(request, f"Test email sent to {target}. Check your inbox.")
    else:
        messages.error(
            request, "The test email could not be sent. Check the EMAIL_* settings in your server's environment."
        )
        if request.user.is_admin:
            messages.info(request, f"Technical detail: {error[:200]}")
    return redirect("staff:settings")


@require_capability("staff.manage")
def staff_list(request):
    users = User.objects.filter(role__in=STAFF_ROLES).order_by("-is_active", "full_name")
    return render(request, "staff/system/staff_list.html", {"users": users})


def _invite(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    notify.set_password_invite(
        user, f"{settings.SITE_URL}{reverse('accounts:password_reset_confirm', args=[uid, token])}"
    )


@require_capability("staff.manage")
def staff_new(request):
    form = StaffUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        user = User.objects.create_user(email=data["email"], full_name=data["full_name"], role=data["role"])
        _invite(request, user)
        audit.record(
            request.user,
            "staff.created",
            f"Added {user.full_name} as {user.get_role_display()}",
            target=user,
            request=request,
        )
        messages.success(request, f"{user.full_name} was added. They've been emailed a link to set their password.")
        return redirect("staff:staff_users")
    return render(request, "staff/system/staff_form.html", {"form": form, "is_new": True})


@require_capability("staff.manage")
def staff_edit(request, pk):
    user = get_object_or_404(User, pk=pk, role__in=STAFF_ROLES)
    form = StaffUserForm(
        request.POST or None,
        user=user,
        initial={"full_name": user.full_name, "email": user.email, "role": user.role, "is_active": user.is_active},
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        other_admins = User.objects.filter(role=Role.ADMIN, is_active=True).exclude(pk=user.pk).exists()
        if user.role == Role.ADMIN and (data["role"] != Role.ADMIN or not data["is_active"]) and not other_admins:
            form.add_error(None, "The library needs at least one active administrator.")
        else:
            before = {"role": user.role, "is_active": user.is_active, "email": user.email}
            user.full_name, user.email, user.role, user.is_active = (
                data["full_name"],
                data["email"],
                data["role"],
                data["is_active"],
            )
            user.save()
            audit.record(
                request.user,
                "staff.updated",
                f"Updated staff account {user.full_name}",
                target=user,
                changes=audit.diff(before, {"role": user.role, "is_active": user.is_active, "email": user.email}),
                request=request,
            )
            messages.success(request, "Staff account saved.")
            return redirect("staff:staff_users")
    return render(request, "staff/system/staff_form.html", {"form": form, "is_new": False, "account": user})


@require_capability("staff.manage")
@require_POST
def staff_invite(request, pk):
    user = get_object_or_404(User, pk=pk, role__in=STAFF_ROLES)
    _invite(request, user)
    audit.record(
        request.user, "staff.password_link", f"Sent a password link to {user.full_name}", target=user, request=request
    )
    messages.success(request, f"Password link sent to {user.email}.")
    return redirect("staff:staff_users")


@require_capability("audit.view")
def audit_log(request):
    entries = AuditLog.objects.select_related("actor")
    query = request.GET.get("q", "").strip()
    if query:
        entries = entries.filter(
            Q(summary__icontains=query) | Q(actor_name__icontains=query) | Q(action__icontains=query)
        )
    page = Paginator(entries, 50).get_page(request.GET.get("page"))
    return render(request, "staff/system/audit.html", {"page": page, "query": query})


@require_capability("backup")
def backup_view(request):
    counts = {
        "Books": BookCopy.objects.values("book").distinct().count(),
        "Copies": BookCopy.objects.count(),
        "Members": Member.objects.count(),
        "Loans": Loan.objects.count(),
        "Fees": Fee.objects.count(),
    }
    return render(
        request,
        "staff/system/backup.html",
        {"counts": counts, "db_engine": settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]},
    )


@require_capability("backup")
@require_POST
def backup_download(request):
    data = backup.dump_json()
    audit.record(request.user, "backup.downloaded", "Downloaded a full data backup", request=request)
    response = HttpResponse(data, content_type="application/gzip")
    response["Content-Disposition"] = f'attachment; filename="{backup.backup_filename()}"'
    return response


@require_capability("backup")
def export_csv(request, dataset):
    if dataset == "books":
        columns = [
            "Title",
            "Author(s)",
            "ISBN",
            "Category",
            "Publisher",
            "Year",
            "Language",
            "Copy ID",
            "Barcode",
            "Shelf",
            "Status",
            "Condition",
        ]
        rows = (
            [
                c.book.title,
                c.book.author_names,
                c.book.isbn,
                c.book.category.name if c.book.category_id else "",
                c.book.publisher.name if c.book.publisher_id else "",
                c.book.publication_year or "",
                c.book.language,
                c.copy_code,
                c.barcode,
                c.shelf_location,
                c.get_status_display(),
                c.get_condition_display(),
            ]
            for c in BookCopy.objects.select_related("book__category", "book__publisher")
            .order_by("book__title", "copy_number")
            .iterator()
        )
    elif dataset == "members":
        columns = ["Member ID", "Name", "Email", "Phone", "Address", "Status", "Joined"]
        rows = (
            [
                m.member_id,
                m.user.full_name,
                m.user.email or "",
                m.phone,
                m.address,
                m.get_status_display(),
                m.created_at.date(),
            ]
            for m in Member.objects.select_related("user").order_by("member_id").iterator()
        )
    elif dataset == "loans":
        columns = ["Borrowed", "Due", "Returned", "Status", "Book", "Copy ID", "Member ID", "Member"]
        rows = (
            [
                l.borrowed_on,
                l.due_date,
                l.returned_on or "",
                l.get_status_display(),
                l.copy.book.title,
                l.copy.copy_code,
                l.member.member_id,
                l.member.user.full_name,
            ]
            for l in Loan.objects.select_related("copy__book", "member__user").order_by("-borrowed_at").iterator()
        )
    elif dataset == "fees":
        columns = ["Created", "Member ID", "Member", "Type", "Description", "Amount", "Paid", "Status"]
        rows = (
            [
                f.created_at.date(),
                f.member.member_id,
                f.member.user.full_name,
                f.get_kind_display(),
                f.description,
                f.amount,
                f.amount_paid,
                f.get_status_display(),
            ]
            for f in Fee.objects.select_related("member__user").order_by("-created_at").iterator()
        )
    else:
        return redirect("staff:backup")
    audit.record(request.user, "backup.export", f"Exported {dataset} as CSV", request=request)
    return csv_response(f"{dataset}.csv", columns, rows)


@require_capability("announcements")
def announcements(request):
    form = AnnouncementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        announcement = Announcement.objects.create(created_by=request.user, **form.cleaned_data)
        users = User.objects.filter(member__status="active", is_active=True)
        announcement.recipients = notify.announce(announcement, users)
        announcement.save(update_fields=["recipients"])
        audit.record(
            request.user,
            "announcement.sent",
            f"Announced '{announcement.title}' to {announcement.recipients} members",
            target=announcement,
        )
        messages.success(request, f"Announcement sent to {announcement.recipients} members.")
        return redirect("staff:announcements")
    return render(
        request,
        "staff/system/announcements.html",
        {"form": form, "items": Announcement.objects.select_related("created_by")[:20]},
    )
