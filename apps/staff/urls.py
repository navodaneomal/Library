from django.urls import path

from .views import (
    auth,
    books,
    circulation,
    dashboard,
    fees,
    inventory,
    members,
    overdue,
    reports,
    reservations,
    system,
)

app_name = "staff"

urlpatterns = [
    path("login/", auth.staff_login, name="login"),
    path("", dashboard.dashboard, name="dashboard"),
    path("today/", dashboard.today_view, name="today"),
    # Circulation
    path("circulation/", circulation.borrow_desk, name="borrow"),
    path("circulation/return/", circulation.return_desk, name="return"),
    path("quick-scan/", circulation.quick_scan, name="quick_scan"),
    path("loans/<int:pk>/renew/", circulation.renew_loan, name="loan_renew"),
    path("loans/<int:pk>/lost/", circulation.lost_loan, name="loan_lost"),
    path("loans/<int:pk>/remind/", circulation.send_reminder, name="loan_remind"),
    # Books
    path("books/", books.book_list, name="books"),
    path("books/add/", books.add_book, name="book_add"),
    path("books/new/", books.book_new, name="book_new"),
    path("books/isbn-lookup/", books.isbn_lookup_json, name="isbn_lookup"),
    path("books/labels/", books.labels, name="labels"),
    path("books/<int:pk>/", books.book_detail, name="book"),
    path("books/<int:pk>/edit/", books.book_edit, name="book_edit"),
    path("books/<int:pk>/archive/", books.book_archive, name="book_archive"),
    path("books/<int:pk>/delete/", books.book_delete, name="book_delete"),
    path("books/<int:pk>/copies/add/", books.copy_add, name="copy_add"),
    path("copies/<int:pk>/", books.copy_detail, name="copy"),
    path("copies/<int:pk>/edit/", books.copy_edit, name="copy_edit"),
    # Members
    path("members/", members.member_list, name="members"),
    path("members/new/", members.member_new, name="member_new"),
    path("members/<int:pk>/", members.member_detail, name="member"),
    path("members/<int:pk>/edit/", members.member_edit, name="member_edit"),
    path("members/<int:pk>/status/", members.member_status, name="member_status"),
    path("members/<int:pk>/password-link/", members.member_send_password, name="member_password"),
    path("members/<int:pk>/card/", members.member_card, name="member_card"),
    path("members/<int:pk>/settle/", fees.member_settle, name="member_settle"),
    path("members/<int:pk>/add-fee/", fees.member_add_fee, name="member_add_fee"),
    # Reservations & overdue
    path("reservations/", reservations.reservation_list, name="reservations"),
    path("reservations/<int:pk>/picked/", reservations.reservation_picked, name="reservation_picked"),
    path("reservations/<int:pk>/cancel/", reservations.reservation_cancel, name="reservation_cancel"),
    path("overdue/", overdue.overdue_list, name="overdue"),
    path("overdue/run-reminders/", overdue.run_tasks, name="run_tasks"),
    # Fees
    path("fees/", fees.fee_list, name="fees"),
    path("fees/<int:pk>/", fees.fee_detail, name="fee"),
    path("fees/<int:pk>/pay/", fees.fee_pay, name="fee_pay"),
    path("fees/<int:pk>/waive/", fees.fee_waive, name="fee_waive"),
    # Inventory
    path("inventory/", inventory.inventory_list, name="inventory"),
    path("inventory/<int:pk>/", inventory.inventory_session, name="inventory_session"),
    path("inventory/<int:pk>/complete/", inventory.inventory_complete, name="inventory_complete"),
    path("inventory/<int:pk>/report/", inventory.inventory_report, name="inventory_report"),
    path("inventory/<int:pk>/mark-lost/", inventory.inventory_mark_lost, name="inventory_mark_lost"),
    # Reports
    path("reports/", reports.report_index, name="reports"),
    path("reports/<slug:slug>/", reports.report_view, name="report"),
    # Administration
    path("settings/", system.settings_view, name="settings"),
    path("settings/test-email/", system.email_test, name="email_test"),
    path("staff/", system.staff_list, name="staff_users"),
    path("staff/new/", system.staff_new, name="staff_new"),
    path("staff/<int:pk>/", system.staff_edit, name="staff_edit"),
    path("staff/<int:pk>/invite/", system.staff_invite, name="staff_invite"),
    path("audit/", system.audit_log, name="audit"),
    path("backup/", system.backup_view, name="backup"),
    path("backup/download/", system.backup_download, name="backup_download"),
    path("backup/export/<slug:dataset>.csv", system.export_csv, name="export_csv"),
    path("announcements/", system.announcements, name="announcements"),
]
