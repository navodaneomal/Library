"""Circulation desk: SCAN → CONFIRM → DONE.

Each view works as a normal form post (with a redirect) and, when called by
the page's fetch helper, returns just the desk fragment for instant updates.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.circulation.models import Loan
from apps.circulation.rules import member_standing, renewal_problems
from apps.circulation.services import CirculationError, mark_lost, renew
from apps.core.http import is_partial, safe_next
from apps.core.permissions import require_capability
from apps.core.templatetags.library import money, nice_date
from apps.members.models import Member
from apps.notifications.reminders import send_manual_overdue
from apps.staff import desk


def _borrow_context(state):
    from apps.catalogue.models import BookCopy

    member = state.member
    standing = member_standing(member) if member else None
    pending = None
    if state.data.get("pending_copy_id"):
        pending = BookCopy.objects.select_related("book").filter(pk=state.data["pending_copy_id"]).first()
    loans = []
    if standing:
        loans = [{"loan": loan, "renew_problems": renewal_problems(loan)} for loan in standing.active_loans]
    return {
        "state": state.data,
        "member": member,
        "standing": standing,
        "pending": pending,
        "loans": loans,
        "candidates": [],
    }


def _respond(request, full_template, partial_template, context, url_name):
    if is_partial(request):
        return render(request, partial_template, context)
    if request.method == "POST":
        return redirect(url_name)
    return render(request, full_template, context)


@require_capability("circulation")
def borrow_desk(request):
    state = desk.DeskState(request.session, "desk_borrow")
    candidates = []
    if request.method == "GET" and request.GET.get("member"):
        member = Member.objects.select_related("user").filter(pk=request.GET["member"]).first()
        if member:
            state.set_member(member)
            state.set_result(desk.member_loaded_result(member, member_standing(member)))
            state.save()
            return redirect("staff:borrow")
    if request.method == "POST":
        action = request.POST.get("action", "scan")
        code = request.POST.get("code", "")
        if action == "scan":
            if not code.strip() and state.data.get("pending_copy_id"):
                desk.confirm_borrow(state, state.data["pending_copy_id"], request.user)
            elif state.member is None:
                found = desk.lookup(code)
                if found.kind in ("member", "copy"):
                    desk.scan_for_borrow(state, found)
                else:
                    candidates = desk.find_members(code)
                    if len(candidates) == 1:
                        desk.scan_for_borrow(state, desk.lookup(candidates[0].member_id))
                        candidates = []
                    elif candidates:
                        state.set_result(
                            desk.result(
                                "info", f"{len(candidates)} members match '{code.strip()}'", hint="Choose one below."
                            )
                        )
                    else:
                        state.set_result(
                            desk.result(
                                "error",
                                "No member found",
                                subtitle=code.strip(),
                                hint="Scan the library card or search by name, email or phone.",
                            )
                        )
            else:
                desk.scan_for_borrow(state, desk.lookup(code))
        elif action == "choose":
            member = Member.objects.select_related("user").filter(pk=request.POST.get("member_pk")).first()
            if member:
                state.set_member(member)
                state.set_result(desk.member_loaded_result(member, member_standing(member)))
        elif action == "confirm":
            desk.confirm_borrow(state, request.POST.get("copy_pk"), request.user)
        elif action == "cancel":
            state.data["pending_copy_id"] = None
            state.set_result(desk.result("info", "Cancelled", hint="Scan the next book."))
        elif action == "renew":
            loan = get_object_or_404(Loan, pk=request.POST.get("loan_pk"), member_id=state.data.get("member_id"))
            try:
                loan = renew(loan, actor=request.user)
                state.set_result(
                    desk.result(
                        "success",
                        "Renewed",
                        subtitle=loan.copy.book.title,
                        details=[["New due date", nice_date(loan.due_date)]],
                    )
                )
            except CirculationError as error:
                state.set_result(
                    desk.result("error", "Can't renew", subtitle=loan.copy.book.title, problems=[error.message])
                )
        elif action == "finish":
            state.clear()
        state.save()
        if candidates and not is_partial(request):
            context = _borrow_context(state)
            context["candidates"] = candidates
            return render(request, "staff/circulation/borrow.html", context)
    context = _borrow_context(state)
    context["candidates"] = candidates
    return _respond(
        request, "staff/circulation/borrow.html", "staff/circulation/_borrow_desk.html", context, "staff:borrow"
    )


@require_capability("circulation")
def return_desk(request):
    state = desk.DeskState(request.session, "desk_return")
    if request.method == "POST":
        if request.POST.get("action") == "clear":
            state.clear()
        else:
            desk.process_return(state, desk.lookup(request.POST.get("code", "")), request.user)
        state.save()
    context = {"state": state.data}
    return _respond(
        request, "staff/circulation/return.html", "staff/circulation/_return_desk.html", context, "staff:return"
    )


@require_capability("circulation")
def quick_scan(request):
    """One input for everything: member cards load a borrower, borrowed copies
    are returned, other copies are lent after Enter. Built for USB scanners."""
    state = desk.DeskState(request.session, "desk_quick")
    if request.method == "POST":
        action = request.POST.get("action", "scan")
        code = request.POST.get("code", "")
        if action == "clear":
            state.clear()
            state.set_result(desk.result("info", "Ready", hint="Scan a library card or a book."))
        elif not code.strip():
            if state.data.get("pending_copy_id"):
                desk.confirm_borrow(state, state.data["pending_copy_id"], request.user)
            else:
                state.set_result(desk.result("info", "Ready", hint="Scan a library card or a book."))
        else:
            found = desk.lookup(code)
            if found.kind == "copy" and desk.active_loan_for(found.copy):
                state.data["pending_copy_id"] = None
                desk.process_return(state, found, request.user)
            elif found.kind == "copy" and state.member is None:
                state.data["pending_copy_id"] = None
                state.set_result(desk.not_on_loan_result(found.copy))
            else:
                desk.scan_for_borrow(state, found)
        state.save()
    context = {"state": state.data, "member": state.member}
    if context["member"]:
        context["standing"] = member_standing(context["member"])
    return _respond(request, "staff/quickscan.html", "staff/_quickscan_panel.html", context, "staff:quick_scan")


@require_capability("circulation")
@require_POST
def renew_loan(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("copy__book", "member__user"), pk=pk)
    try:
        loan = renew(loan, actor=request.user)
        messages.success(request, f"Renewed '{loan.copy.book.title}' — now due {nice_date(loan.due_date)}.")
    except CirculationError as error:
        messages.error(request, error.message)
    return redirect(safe_next(request, reverse("staff:member", args=[loan.member_id])))


@require_capability("fees.manage")
@require_POST
def lost_loan(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("copy__book", "member__user"), pk=pk)
    try:
        fees = mark_lost(loan, staff=request.user)
        total = sum(f.amount for f in fees)
        messages.success(
            request,
            f"'{loan.copy.book.title}' marked as lost. Fees of {money(total)} added and the member was notified.",
        )
    except CirculationError as error:
        messages.error(request, error.message)
    return redirect(reverse("staff:member", args=[loan.member_id]))


@require_capability("circulation")
@require_POST
def send_reminder(request, pk):
    loan = get_object_or_404(Loan.objects.select_related("copy__book", "member__user"), pk=pk)
    if not loan.is_overdue():
        messages.error(request, "This loan isn't overdue.")
    elif not loan.member.user.email:
        messages.error(
            request,
            f"{loan.member.user.full_name} has no email address — please phone them on {loan.member.phone or 'file'}.",
        )
    else:
        send_manual_overdue(loan)
        messages.success(request, f"Reminder sent to {loan.member.user.full_name}.")
    return redirect(safe_next(request, reverse("staff:overdue")))
