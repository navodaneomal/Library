import re
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Role, User, normalize_email
from apps.barcodes.isbn import clean as clean_isbn
from apps.barcodes.isbn import to_isbn13
from apps.barcodes.scan import normalize_code
from apps.catalogue.covers import process_upload
from apps.catalogue.models import (
    Book,
    BookCopy,
    Category,
    CopyCondition,
    CopyStatus,
    Publisher,
)
from apps.catalogue.services import barcode_conflict
from apps.circulation.services import STAFF_SETTABLE_STATUSES
from apps.fees.models import FeeKind, PaymentMethod
from apps.inventory.models import InventorySession
from apps.members.models import Member, MemberStatus

TRAIT_PATTERN = re.compile(r"^\s*([^:]{1,40}?)\s*[:=]\s*(\d{1,2})\s*$")


class BookForm(forms.ModelForm):
    author_names = forms.CharField(
        label="Author(s)", max_length=500, required=False, help_text="Separate several authors with commas."
    )
    publisher_name = forms.CharField(label="Publisher", max_length=200, required=False)
    new_category = forms.CharField(
        label="…or new category", max_length=80, required=False, help_text="Creates the category if it doesn't exist."
    )
    cover_upload = forms.ImageField(label="Cover image", required=False, help_text="JPEG, PNG or WebP, up to 5 MB.")
    cover_url = forms.URLField(required=False, widget=forms.HiddenInput)
    remove_cover = forms.BooleanField(label="Remove current cover", required=False)
    dna = forms.CharField(
        label="Book DNA",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Adventure: 8\nFantasy: 9\nMystery: 4"}),
        help_text="Optional. One trait per line as 'Name: score' (1–10).",
    )

    class Meta:
        model = Book
        fields = [
            "title",
            "subtitle",
            "isbn",
            "publication_year",
            "category",
            "language",
            "description",
            "keywords",
            "loan_period_days",
            "reference_only",
            "replacement_cost",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["category"].empty_label = "— Choose a category —"
        # Accept ISBNs typed with hyphens/spaces; clean_isbn normalises to 13 digits.
        self.fields["isbn"] = forms.CharField(
            label="ISBN",
            max_length=24,
            required=False,
            widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
        )
        if self.instance.pk:
            self.fields["author_names"].initial = self.instance.author_names
            self.fields["publisher_name"].initial = self.instance.publisher.name if self.instance.publisher_id else ""
            self.fields["dna"].initial = "\n".join(f"{t.name}: {t.score}" for t in self.instance.traits.all())
        else:
            del self.fields["remove_cover"]

    def clean_isbn(self):
        raw = self.cleaned_data.get("isbn", "")
        if not raw:
            return ""
        isbn = to_isbn13(raw)
        if not isbn:
            raise ValidationError("That ISBN doesn't look right — please check the digits.")
        clash = Book.objects.filter(isbn=isbn).exclude(pk=self.instance.pk).first()
        if clash:
            raise ValidationError(f"This ISBN is already used by '{clash.title}'.")
        return isbn

    def clean_cover_upload(self):
        upload = self.cleaned_data.get("cover_upload")
        if upload:
            return process_upload(
                upload,
                name_hint=re.sub(r"[^a-z0-9]+", "-", (self.data.get("title") or "cover").lower())[:40] or "cover",
            )
        return None

    def clean_dna(self):
        traits = []
        for line in re.split(r"[\n,]", self.cleaned_data.get("dna", "")):
            if not line.strip():
                continue
            match = TRAIT_PATTERN.match(line)
            if not match:
                raise ValidationError(f"Couldn't read '{line.strip()}'. Use 'Name: score', e.g. 'Mystery: 6'.")
            name, score = match.group(1).strip().title(), int(match.group(2))
            if not 1 <= score <= 10:
                raise ValidationError(f"The score for {name} must be between 1 and 10.")
            traits.append((name, score))
        return traits[:8]

    def clean(self):
        cleaned = super().clean()
        new_category = (cleaned.get("new_category") or "").strip()
        if new_category:
            from django.utils.text import slugify

            category = Category.objects.filter(name__iexact=new_category).first()
            if category is None:
                slug, n = slugify(new_category) or "category", 2
                base = slug
                while Category.objects.filter(slug=slug).exists():
                    slug, n = f"{base}-{n}", n + 1
                category = Category(name=new_category, slug=slug)
            cleaned["category"] = category
        return cleaned

    def apply_relations(self, book: Book) -> Book:
        """Resolve publisher/category text fields onto the (unsaved) book."""
        category = self.cleaned_data.get("category")
        if category is not None and not category.pk:
            category.save()
        book.category = category
        name = (self.cleaned_data.get("publisher_name") or "").strip()
        if name:
            book.publisher = Publisher.objects.filter(name__iexact=name).first() or Publisher.objects.create(name=name)
        else:
            book.publisher = None
        if self.cleaned_data.get("remove_cover"):
            book.cover = None
        cover = self.cleaned_data.get("cover_upload")
        if cover:
            book.cover.save(cover.name, cover, save=False)
        return book

    def save_traits(self, book: Book):
        from apps.catalogue.models import BookTrait

        book.traits.all().delete()
        BookTrait.objects.bulk_create(
            [BookTrait(book=book, name=name, score=score) for name, score in self.cleaned_data["dna"]]
        )


def _validate_new_barcode(value: str, *, exclude_copy_pk=None) -> str:
    code = normalize_code(value)
    if not code:
        return ""
    existing = barcode_conflict(code, exclude_copy_pk=exclude_copy_pk)
    if existing:
        raise ValidationError(
            f"This barcode is already registered to '{existing.book.title}' ({existing.copy_code}, "
            f"{existing.get_status_display().lower()})."
        )
    if Member.objects.filter(member_id=code).exists():
        raise ValidationError("This code is a member's library card number.")
    return code


class CopyFieldsMixin(forms.Form):
    barcode = forms.CharField(
        max_length=64,
        required=False,
        help_text="Scan the copy's label. Leave empty to generate one you can print.",
        widget=forms.TextInput(attrs={"autocomplete": "off", "data-scan": "true"}),
    )
    shelf_location = forms.CharField(max_length=40, required=False, help_text="e.g. FIC-A-04")
    condition = forms.ChoiceField(
        choices=[c for c in CopyCondition.choices if c[0] not in (CopyCondition.LOST,)], initial=CopyCondition.NEW
    )
    extra_copies = forms.IntegerField(
        label="Additional copies",
        min_value=0,
        max_value=50,
        initial=0,
        required=False,
        help_text="Extra identical copies get generated barcodes (print labels afterwards).",
    )

    def clean_barcode(self):
        return _validate_new_barcode(self.cleaned_data.get("barcode", ""))

    def clean_shelf_location(self):
        return normalize_code(self.cleaned_data.get("shelf_location", ""))


class NewBookForm(BookForm, CopyFieldsMixin):
    pass


class AddCopyForm(CopyFieldsMixin):
    pass


class CopyForm(forms.ModelForm):
    class Meta:
        model = BookCopy
        fields = ["barcode", "shelf_location", "condition", "status", "acquired_on", "notes"]
        widgets = {"acquired_on": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = set(STAFF_SETTABLE_STATUSES) | {self.instance.status}
        self.fields["status"].choices = [c for c in CopyStatus.choices if c[0] in allowed]
        if self.instance.status in (CopyStatus.BORROWED, CopyStatus.RESERVED):
            self.fields["status"].disabled = True
            self.fields["status"].help_text = (
                "This copy is on loan — return it first."
                if self.instance.status == CopyStatus.BORROWED
                else "This copy is on hold for a member."
            )

    def clean_barcode(self):
        code = _validate_new_barcode(self.cleaned_data.get("barcode", ""), exclude_copy_pk=self.instance.pk)
        if not code:
            raise ValidationError("A copy must have a barcode.")
        return code

    def clean_shelf_location(self):
        return normalize_code(self.cleaned_data.get("shelf_location", ""))


class ScanForm(forms.Form):
    code = forms.CharField(max_length=64, required=False, widget=forms.TextInput(attrs={"autocomplete": "off"}))


class MemberForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False, help_text="Optional. Needed to sign in online and receive reminders.")
    phone = forms.CharField(max_length=30, required=False)
    address = forms.CharField(max_length=255, required=False)
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    email_reminders = forms.BooleanField(label="Send due-date reminders by email", required=False, initial=True)
    staff_notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), help_text="Only visible to staff."
    )
    send_invite = forms.BooleanField(
        label="Email a link to set a password",
        required=False,
        initial=True,
        help_text="Lets the member sign in online to reserve books.",
    )

    def __init__(self, *args, member: Member | None = None, **kwargs):
        self.member = member
        super().__init__(*args, **kwargs)
        if member is not None:
            del self.fields["send_invite"]

    def clean_email(self):
        email = normalize_email(self.cleaned_data.get("email"))
        if email:
            clash = User.objects.filter(email=email)
            if self.member is not None:
                clash = clash.exclude(pk=self.member.user_id)
            if clash.exists():
                raise ValidationError("Another account already uses this email.")
        return email

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob and dob > timezone.localdate():
            raise ValidationError("Date of birth can't be in the future.")
        return dob


class MemberStatusForm(forms.Form):
    status = forms.ChoiceField(choices=MemberStatus.choices)
    reason = forms.CharField(max_length=255, required=False)


class PaymentForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)
    method = forms.ChoiceField(choices=[c for c in PaymentMethod.choices if c[0] != PaymentMethod.ONLINE])
    paid_on = forms.DateField(
        label="Payment date", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"})
    )
    reference = forms.CharField(label="Receipt / reference number", max_length=100, required=False)


class SettleForm(forms.Form):
    method = forms.ChoiceField(choices=[c for c in PaymentMethod.choices if c[0] != PaymentMethod.ONLINE])
    paid_on = forms.DateField(
        label="Payment date", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"})
    )
    reference = forms.CharField(label="Receipt / reference number", max_length=100, required=False)


class WaiveForm(forms.Form):
    reason = forms.CharField(max_length=255)


class AddFeeForm(forms.Form):
    kind = forms.ChoiceField(choices=[c for c in FeeKind.choices if c[0] != FeeKind.LATE], initial=FeeKind.DAMAGE)
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)
    description = forms.CharField(max_length=255)


class StaffUserForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[c for c in Role.choices if c[0] != Role.MEMBER])
    is_active = forms.BooleanField(label="Account active", required=False, initial=True)

    def __init__(self, *args, user: User | None = None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user is None:
            del self.fields["is_active"]

    def clean_email(self):
        email = normalize_email(self.cleaned_data["email"])
        clash = User.objects.filter(email=email)
        if self.user is not None:
            clash = clash.exclude(pk=self.user.pk)
        if clash.exists():
            raise ValidationError("Another account already uses this email.")
        return email


class AnnouncementForm(forms.Form):
    title = forms.CharField(max_length=160)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))


class InventorySessionForm(forms.ModelForm):
    class Meta:
        model = InventorySession
        fields = ["name", "scope_prefix"]

    def clean_scope_prefix(self):
        return normalize_code(self.cleaned_data.get("scope_prefix", ""))


class DateRangeForm(forms.Form):
    date_from = forms.DateField(label="From", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="To", required=False, widget=forms.DateInput(attrs={"type": "date"}))


def isbn_or_none(value: str) -> str | None:
    return to_isbn13(clean_isbn(value))
