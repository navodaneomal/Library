from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse


class Author(models.Model):
    name = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True)
    description = models.CharField(max_length=255, blank=True)
    shelf_code = models.CharField(
        max_length=10,
        blank=True,
        help_text="Shelf prefix for this section, e.g. FIC — lets 'FIC-A-04' read as 'Fiction → Shelf A → Row 04'.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Publisher(models.Model):
    name = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Book(models.Model):
    """A title in the catalogue. Physical items are BookCopy rows."""

    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(max_length=280, unique=True)
    authors = models.ManyToManyField(Author, related_name="books", blank=True)
    author_names = models.CharField(
        "Author(s)", max_length=500, blank=True, help_text="Display form, in the order entered."
    )
    isbn = models.CharField("ISBN", max_length=13, blank=True, db_index=True)
    publisher = models.ForeignKey(Publisher, null=True, blank=True, on_delete=models.SET_NULL, related_name="books")
    publication_year = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1000), MaxValueValidator(2100)]
    )
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="books")
    language = models.CharField(max_length=40, default="English", db_index=True)
    description = models.TextField(blank=True)
    keywords = models.CharField(max_length=255, blank=True, help_text="Comma-separated, helps search.")
    cover = models.ImageField(upload_to="covers/", blank=True)
    loan_period_days = models.PositiveSmallIntegerField(
        "Loan period (days)",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Leave empty to use the library default.",
    )
    reference_only = models.BooleanField(
        "Reference only", default=False, help_text="For in-library use; cannot be borrowed or reserved."
    )
    replacement_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    times_borrowed = models.PositiveIntegerField(default=0)
    search_title = models.CharField(max_length=255, db_index=True, editable=False, default="")
    search_text = models.TextField(editable=False, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(fields=["isbn"], condition=~Q(isbn=""), name="unique_isbn_when_present"),
        ]
        indexes = [models.Index(fields=["is_archived", "created_at"])]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("public:book", args=[self.slug])

    @property
    def full_title(self) -> str:
        return f"{self.title}: {self.subtitle}" if self.subtitle else self.title


class CopyStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    BORROWED = "borrowed", "Borrowed"
    RESERVED = "reserved", "Reserved"
    MAINTENANCE = "maintenance", "Maintenance"
    DAMAGED = "damaged", "Damaged"
    LOST = "lost", "Lost"
    WITHDRAWN = "withdrawn", "Withdrawn"


class CopyCondition(models.TextChoices):
    NEW = "new", "New"
    GOOD = "good", "Good"
    WORN = "worn", "Worn"
    DAMAGED = "damaged", "Damaged"
    LOST = "lost", "Lost"


# Statuses a member could eventually borrow (used for "has loanable copies").
CIRCULATING_STATUSES = (CopyStatus.AVAILABLE, CopyStatus.BORROWED, CopyStatus.RESERVED)
UNUSABLE_CONDITIONS = (CopyCondition.DAMAGED, CopyCondition.LOST)


class BookCopy(models.Model):
    """One physical, independently trackable item with its own barcode."""

    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="copies")
    copy_number = models.PositiveIntegerField()
    copy_code = models.CharField("Copy ID", max_length=40, unique=True)
    barcode = models.CharField(max_length=64, unique=True)
    shelf_location = models.CharField(max_length=40, blank=True, db_index=True)
    condition = models.CharField(max_length=20, choices=CopyCondition.choices, default=CopyCondition.GOOD)
    status = models.CharField(max_length=20, choices=CopyStatus.choices, default=CopyStatus.AVAILABLE, db_index=True)
    acquired_on = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["book__title", "copy_number"]
        verbose_name_plural = "book copies"
        constraints = [
            models.UniqueConstraint(fields=["book", "copy_number"], name="unique_copy_number_per_book"),
            models.CheckConstraint(
                condition=Q(status__in=[choice for choice, _ in CopyStatus.choices]), name="copy_status_valid"
            ),
        ]
        indexes = [models.Index(fields=["book", "status"])]

    def __str__(self):
        return f"{self.book.title} — {self.label} ({self.copy_code})"

    @property
    def label(self) -> str:
        return f"Copy {self.copy_number:03d}"

    @property
    def is_usable(self) -> bool:
        return self.condition not in UNUSABLE_CONDITIONS


class BookTrait(models.Model):
    """'Book DNA' — optional, hand-curated flavour scores (1–10)."""

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="traits")
    name = models.CharField(max_length=40)
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])

    class Meta:
        ordering = ["-score", "name"]
        constraints = [models.UniqueConstraint(fields=["book", "name"], name="unique_trait_per_book")]

    def __str__(self):
        return f"{self.name}: {self.score}/10"

    @property
    def percent(self) -> int:
        return self.score * 10
