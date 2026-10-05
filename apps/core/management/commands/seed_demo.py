"""Realistic demo data: books, copies, members, loan history, overdue books,
reservations and fees — all created through the real services, so every
number on every screen is consistent.

    python manage.py seed_demo --password "choose-a-demo-password"
"""

import logging
import random
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import Role, User
from apps.catalogue.models import Book, BookCopy, BookTrait, Category, Publisher
from apps.catalogue.services import add_copy, save_book
from apps.circulation import reservations as reservation_service
from apps.circulation.services import CirculationError, borrow, mark_lost, return_copy
from apps.core import config
from apps.core.middleware import reset_setup_cache
from apps.core.models import LibrarySettings
from apps.fees import services as fee_service
from apps.fees.models import Fee, FeeStatus
from apps.members.models import MemberStatus
from apps.members.services import create_member
from apps.notifications import notify
from apps.notifications.models import Announcement

CATEGORIES = [
    ("Fiction", "FIC"),
    ("Fantasy", "FAN"),
    ("Science Fiction", "SCI"),
    ("Mystery & Crime", "MYS"),
    ("Classics", "CLA"),
    ("Children", "CHI"),
    ("History & Society", "HIS"),
    ("Science", "SCN"),
    ("Self-help", "SLF"),
    ("Technology", "TEC"),
    ("Biography", "BIO"),
    ("Sri Lankan Literature", "SLL"),
]

# title, subtitle, authors, isbn, publisher, year, category, language, copies, description, keywords, traits
BOOKS = [
    (
        "The Hobbit",
        "or There and Back Again",
        "J.R.R. Tolkien",
        "9780261102217",
        "HarperCollins",
        1937,
        "Fantasy",
        "English",
        5,
        "A comfortable hobbit is swept into a quest with thirteen dwarves and a wizard, and discovers more courage than he knew he had.",
        "dragons, quest, adventure, middle-earth",
        {"Adventure": 9, "Fantasy": 10, "Humour": 6, "Mystery": 4},
    ),
    (
        "Harry Potter and the Philosopher's Stone",
        "",
        "J.K. Rowling",
        "9780747532699",
        "Bloomsbury",
        1997,
        "Fantasy",
        "English",
        4,
        "An orphan learns on his eleventh birthday that he is a wizard, and begins his first year at a school of magic.",
        "wizards, school, magic",
        {"Fantasy": 9, "Adventure": 8, "Friendship": 8, "Mystery": 6},
    ),
    (
        "Harry Potter and the Chamber of Secrets",
        "",
        "J.K. Rowling",
        "9780747538493",
        "Bloomsbury",
        1998,
        "Fantasy",
        "English",
        3,
        "A second year at school brings whispers in the walls, petrified students and a legendary hidden chamber.",
        "wizards, school, magic, mystery",
        {"Fantasy": 9, "Mystery": 8, "Adventure": 7},
    ),
    (
        "Harry Potter and the Prisoner of Azkaban",
        "",
        "J.K. Rowling",
        "9780747542155",
        "Bloomsbury",
        1999,
        "Fantasy",
        "English",
        3,
        "A dangerous prisoner escapes, and the truth about the past turns out to be stranger than anyone said.",
        "wizards, time, friendship",
        {"Fantasy": 9, "Mystery": 7, "Adventure": 8},
    ),
    (
        "Atomic Habits",
        "An Easy & Proven Way to Build Good Habits & Break Bad Ones",
        "James Clear",
        "9781847941831",
        "Random House Business",
        2018,
        "Self-help",
        "English",
        3,
        "A practical guide to how tiny, consistent changes compound into remarkable results.",
        "habits, productivity, behaviour",
        {"Practical": 10, "Motivation": 8},
    ),
    (
        "Nineteen Eighty-Four",
        "",
        "George Orwell",
        "9780141036144",
        "Penguin",
        1949,
        "Classics",
        "English",
        3,
        "In a state that watches everything and rewrites history daily, one man begins to keep a secret diary.",
        "dystopia, surveillance, politics",
        {"Dystopian": 10, "Politics": 9, "Romance": 3},
    ),
    (
        "To Kill a Mockingbird",
        "",
        "Harper Lee",
        "9780099549482",
        "Vintage",
        1960,
        "Classics",
        "English",
        2,
        "A child in a small Alabama town watches her father defend a man everyone else has already judged.",
        "justice, childhood, courage",
        {"Justice": 10, "Coming of age": 8},
    ),
    (
        "Pride and Prejudice",
        "",
        "Jane Austen",
        "9780141439518",
        "Penguin Classics",
        1813,
        "Classics",
        "English",
        2,
        "Wit, misunderstanding and first impressions in the courtship of Elizabeth Bennet and Mr Darcy.",
        "romance, society, wit",
        {"Romance": 9, "Humour": 7, "Society": 8},
    ),
    (
        "The Great Gatsby",
        "",
        "F. Scott Fitzgerald",
        "9780743273565",
        "Scribner",
        1925,
        "Classics",
        "English",
        2,
        "A mysterious millionaire throws dazzling parties across the bay from the life he lost.",
        "jazz age, wealth, longing",
        {"Romance": 6, "Tragedy": 8},
    ),
    (
        "Sapiens",
        "A Brief History of Humankind",
        "Yuval Noah Harari",
        "9780099590088",
        "Vintage",
        2014,
        "History & Society",
        "English",
        2,
        "A sweeping account of how one species of ape came to dominate the planet — and what it might do next.",
        "history, anthropology, civilisation",
        {"Ideas": 10, "History": 9},
    ),
    (
        "The Alchemist",
        "",
        "Paulo Coelho",
        "9780062315007",
        "HarperOne",
        1988,
        "Fiction",
        "English",
        3,
        "A shepherd boy follows a recurring dream across the desert in search of treasure — and of himself.",
        "journey, dreams, fable",
        {"Inspirational": 9, "Adventure": 6},
    ),
    (
        "The Little Prince",
        "",
        "Antoine de Saint-Exupéry",
        "9780156012195",
        "Harcourt",
        1943,
        "Children",
        "English",
        2,
        "A stranded pilot meets a small prince from another planet who sees the world with clear eyes.",
        "fable, friendship, illustrated",
        {"Whimsy": 10, "Friendship": 8},
    ),
    (
        "Charlotte's Web",
        "",
        "E.B. White",
        "9780064400558",
        "HarperCollins",
        1952,
        "Children",
        "English",
        2,
        "A pig named Wilbur and a clever spider named Charlotte form the most loyal of friendships.",
        "animals, friendship, farm",
        {"Friendship": 10, "Humour": 6},
    ),
    (
        "A Brief History of Time",
        "",
        "Stephen Hawking",
        "9780553380163",
        "Bantam",
        1988,
        "Science",
        "English",
        2,
        "From the big bang to black holes, the nature of time and the universe explained for curious readers.",
        "physics, cosmology, universe",
        {"Ideas": 10, "Science": 10},
    ),
    (
        "Thinking, Fast and Slow",
        "",
        "Daniel Kahneman",
        "9780141033570",
        "Penguin",
        2011,
        "Science",
        "English",
        2,
        "Two systems drive the way we think — one fast and intuitive, one slow and deliberate.",
        "psychology, decisions, behaviour",
        {"Ideas": 9, "Practical": 7},
    ),
    (
        "Murder on the Orient Express",
        "",
        "Agatha Christie",
        "9780007119318",
        "HarperCollins",
        1934,
        "Mystery & Crime",
        "English",
        2,
        "Snowbound on a luxury train, Hercule Poirot finds a passenger murdered and a carriage full of suspects.",
        "detective, poirot, train",
        {"Mystery": 10, "Suspense": 8},
    ),
    (
        "The Hound of the Baskervilles",
        "",
        "Arthur Conan Doyle",
        "",
        "Penguin",
        1902,
        "Mystery & Crime",
        "English",
        2,
        "A family curse, a lonely moor and a spectral hound — a case for Sherlock Holmes.",
        "sherlock holmes, detective, moor",
        {"Mystery": 10, "Suspense": 9},
    ),
    (
        "Dune",
        "",
        "Frank Herbert",
        "9780441172719",
        "Ace",
        1965,
        "Science Fiction",
        "English",
        2,
        "On a desert planet that produces the most valuable substance in the universe, a young heir meets his destiny.",
        "desert, politics, ecology",
        {"Adventure": 8, "Politics": 9, "Science fiction": 10},
    ),
    (
        "Clean Code",
        "A Handbook of Agile Software Craftsmanship",
        "Robert C. Martin",
        "9780132350884",
        "Prentice Hall",
        2008,
        "Technology",
        "English",
        1,
        "Principles and practices for writing code that other people can read, change and trust.",
        "programming, software, craftsmanship",
        {"Practical": 10},
    ),
    (
        "The Pragmatic Programmer",
        "Your Journey to Mastery",
        "David Thomas, Andrew Hunt",
        "9780135957059",
        "Addison-Wesley",
        2019,
        "Technology",
        "English",
        1,
        "Timeless advice on building software and a career, from tracer bullets to broken windows.",
        "programming, software, career",
        {"Practical": 9, "Ideas": 7},
    ),
    (
        "Wings of Fire",
        "An Autobiography",
        "A.P.J. Abdul Kalam, Arun Tiwari",
        "9788173711466",
        "Universities Press",
        1999,
        "Biography",
        "English",
        2,
        "From a boyhood in Rameswaram to leading India's space and missile programmes — a life of persistence.",
        "autobiography, science, inspiration",
        {"Inspirational": 10, "Science": 6},
    ),
    (
        "Anil's Ghost",
        "",
        "Michael Ondaatje",
        "9780375724374",
        "Vintage",
        2000,
        "Sri Lankan Literature",
        "English",
        2,
        "A forensic anthropologist returns to Sri Lanka and uncovers a skeleton that raises dangerous questions.",
        "sri lanka, forensics, memory",
        {"Mystery": 7, "History": 8},
    ),
    (
        "Madol Doova",
        "",
        "Martin Wickramasinghe",
        "",
        "Sarasa",
        1947,
        "Sri Lankan Literature",
        "Sinhala",
        3,
        "Two mischievous boys run away to a small island and make it their own — a beloved classic of Sinhala literature.",
        "childhood, island, adventure, sinhala",
        {"Adventure": 9, "Humour": 7, "Childhood": 10},
    ),
    (
        "Gamperaliya",
        "",
        "Martin Wickramasinghe",
        "",
        "Sarasa",
        1944,
        "Sri Lankan Literature",
        "Sinhala",
        2,
        "A village family's fortunes shift as the old social order gives way to the new.",
        "village, family, change, sinhala",
        {"Society": 9, "Romance": 6},
    ),
    (
        "The Seven Moons of Maali Almeida",
        "",
        "Shehan Karunatilaka",
        "",
        "Sort of Books",
        2022,
        "Sri Lankan Literature",
        "English",
        2,
        "A war photographer wakes up dead and has seven moons to find out who killed him.",
        "afterlife, war, satire, booker",
        {"Mystery": 8, "Humour": 7, "Politics": 8},
    ),
    (
        "Rich Dad Poor Dad",
        "",
        "Robert T. Kiyosaki",
        "9781612680194",
        "Plata Publishing",
        1997,
        "Self-help",
        "English",
        2,
        "Lessons about money, assets and financial independence, told through the story of two fathers.",
        "money, finance, investing",
        {"Practical": 7, "Motivation": 8},
    ),
    (
        "Becoming",
        "",
        "Michelle Obama",
        "9780241334140",
        "Viking",
        2018,
        "Biography",
        "English",
        1,
        "A memoir of growing up on the South Side of Chicago and a life lived in the public eye.",
        "memoir, politics, family",
        {"Inspirational": 8, "Politics": 6},
    ),
    (
        "The Very Hungry Caterpillar",
        "",
        "Eric Carle",
        "9780241003008",
        "Puffin",
        1969,
        "Children",
        "English",
        2,
        "A tiny caterpillar eats its way through a week of food before a beautiful surprise.",
        "picture book, counting, days of the week",
        {"Whimsy": 9},
    ),
]

MEMBERS = [
    ("Navoda Perera", "navoda@example.com", "+94 77 123 4567", "12 Lake Road, Colombo 05"),
    ("Kasun Jayawardena", "kasun@example.com", "+94 71 234 5678", "44 Temple Lane, Kandy"),
    ("Amaya Fernando", "amaya@example.com", "+94 76 345 6789", "8 Galle Road, Dehiwala"),
    ("Tharindu Silva", "tharindu@example.com", "+94 70 456 7890", "21 Station Road, Gampaha"),
    ("Dilini Wickramasinghe", "dilini@example.com", "+94 75 567 8901", "3 Flower Terrace, Colombo 07"),
    ("Ravi Kumar", "ravi@example.com", "+94 77 678 9012", "16 Hill Street, Nuwara Eliya"),
    ("Sanduni Herath", "sanduni@example.com", "+94 71 789 0123", "9 Marine Drive, Colombo 03"),
    ("Nimal Rajapaksha", None, "+94 11 234 5678", "55 Old Road, Moratuwa"),
    ("Ishara de Silva", "ishara@example.com", "+94 72 890 1234", "7 Park Avenue, Negombo"),
    ("Mihiri Gunasekara", "mihiri@example.com", "+94 78 901 2345", "30 Main Street, Matara"),
]


@dataclass
class Event:
    when: datetime
    kind: str  # borrow | return
    member: object
    copy: object
    plan: dict


class Command(BaseCommand):
    help = "Fill the database with realistic demo data (books, members, loans, reservations, fees)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for every demo account (default: random, printed at the end).")
        parser.add_argument("--reset", action="store_true", help="Erase ALL existing data first.")
        parser.add_argument("--seed", type=int, default=2026, help="Random seed for repeatable data.")

    def handle(self, *args, **options):
        if options["reset"]:
            call_command("flush", interactive=False, verbosity=0)
        elif Book.objects.exists() or User.objects.exists():
            raise CommandError(
                "The database already has data. Use --reset to erase it first (this deletes everything)."
            )
        password = options.get("password") or secrets.token_urlsafe(9)
        logging.getLogger("apps.audit").setLevel(logging.WARNING)
        self.rng = random.Random(options["seed"])
        reset_setup_cache()
        # Demo emails go nowhere — they are still logged so the screens look real.
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            self.build(password)
        reset_setup_cache()
        self.stdout.write(self.style.SUCCESS("\nDemo library ready.\n"))
        self.stdout.write(f"  Administrator  admin@library.local      / {password}")
        self.stdout.write(f"  Librarian      librarian@library.local  / {password}")
        self.stdout.write(f"  Assistant      assistant@library.local  / {password}")
        self.stdout.write(f"  Member         navoda@example.com       / {password}  ({self.navoda.member_id})")
        hobbit = BookCopy.objects.filter(book__title="The Hobbit", status="available").first()
        if hobbit:
            self.stdout.write(
                f"\n  Try Quick Scan with member card {self.navoda.member_id} and book barcode {hobbit.barcode}."
            )

    # ------------------------------------------------------------------
    def build(self, password: str):
        now = timezone.now()
        with transaction.atomic():
            s = LibrarySettings.load()
            s.library_name = "City Library"
            s.library_email = "hello@citylibrary.example"
            s.library_phone = "+94 11 234 5000"
            s.library_address = "100 Library Square, Colombo 07"
            s.currency = "LKR"
            s.save()
            config.invalidate()

            self.admin = User.objects.create_user(
                email="admin@library.local", password=password, full_name="Anusha Dissanayake", role=Role.ADMIN
            )
            self.librarian = User.objects.create_user(
                email="librarian@library.local", password=password, full_name="Chamara Bandara", role=Role.LIBRARIAN
            )
            self.assistant = User.objects.create_user(
                email="assistant@library.local", password=password, full_name="Pavithra Senanayake", role=Role.ASSISTANT
            )

            categories = {}
            for name, code in CATEGORIES:
                categories[name] = Category.objects.create(name=name, slug=slugify(name), shelf_code=code)

            created_at = now - timedelta(days=120)
            self.copies = []
            for index, (
                title,
                subtitle,
                authors,
                isbn,
                publisher,
                year,
                category,
                language,
                n_copies,
                description,
                keywords,
                traits,
            ) in enumerate(BOOKS, start=1):
                book = Book(
                    title=title,
                    subtitle=subtitle,
                    isbn=isbn,
                    publisher=Publisher.objects.get_or_create(name=publisher)[0],
                    publication_year=year,
                    category=categories[category],
                    language=language,
                    description=description,
                    keywords=keywords,
                    reference_only=False,
                )
                save_book(book, author_names=authors, actor=self.librarian)
                BookTrait.objects.bulk_create([BookTrait(book=book, name=k, score=v) for k, v in traits.items()])
                surname = authors.split(",")[0].split()[-1]
                shelf = f"{categories[category].shelf_code}-{surname[0].upper()}-{self.rng.randint(1, 12):02d}"
                for copy_n in range(1, n_copies + 1):
                    copy = add_copy(
                        book,
                        barcode=f"2000{index:04d}{copy_n:04d}",
                        shelf_location=shelf,
                        condition=self.rng.choice(["new", "good", "good", "good", "worn"]),
                        actor=self.librarian,
                        allocate=False,
                    )
                    self.copies.append(copy)
                # Stagger "date added" so New arrivals has a meaningful order.
                Book.objects.filter(pk=book.pk).update(created_at=created_at + timedelta(days=index * 4))
            # A reference-only title
            ref = Book(
                title="Oxford English Dictionary",
                subtitle="Compact edition",
                isbn="",
                category=categories["History & Society"],
                language="English",
                description="The reference dictionary — for use in the reading room.",
                reference_only=True,
            )
            save_book(ref, author_names="Oxford University Press", actor=self.librarian)
            add_copy(
                ref,
                barcode="200099990001",
                shelf_location="REF-A-01",
                condition="good",
                actor=self.librarian,
                allocate=False,
            )

            self.members = []
            for full_name, email, phone, address in MEMBERS:
                member = create_member(
                    full_name=full_name,
                    email=email,
                    password=password if email else None,
                    phone=phone,
                    address=address,
                    created_by=self.librarian,
                    email_verified=bool(email),
                )
                self.members.append(member)
            self.navoda = self.members[0]
            from apps.members.models import Member

            Member.objects.filter(pk__in=[m.pk for m in self.members]).update(created_at=now - timedelta(days=150))

        self.simulate_history(now)
        self.current_state(now)

        with transaction.atomic():
            announcement = Announcement.objects.create(
                title="Extended opening hours during exam season",
                body="From next Monday we stay open until 20:00 on weekdays. Happy studying!",
                created_by=self.librarian,
            )
            announcement.recipients = notify.announce(announcement, User.objects.filter(member__status="active"))
            announcement.save()
            suspended = self.members[8]
            suspended.status = MemberStatus.SUSPENDED
            suspended.status_reason = "Repeated late returns — please speak to a librarian"
            suspended.save()

    def simulate_history(self, now):
        """Returned loans over the last ~2 months (some returned late)."""
        events: list[Event] = []
        busy_until: dict[int, datetime] = {}
        start = now - timedelta(days=75)
        regulars = self.members[:8]
        popular_bias = [
            c for c in self.copies if c.book.title in ("Atomic Habits", "The Hobbit", "Sapiens", "The Alchemist")
        ] * 3
        pool = self.copies + popular_bias
        for day in range(0, 55):
            for _ in range(self.rng.randint(1, 3)):
                when = start + timedelta(days=day, hours=self.rng.randint(1, 8))
                copy = self.rng.choice(pool)
                if busy_until.get(copy.pk, start) > when:
                    continue
                keep = self.rng.choice([6, 9, 11, 13, 14, 14, 15, 17, 19, 22])
                back = when + timedelta(days=keep, hours=self.rng.randint(0, 6))
                if back > now - timedelta(days=12):
                    continue
                busy_until[copy.pk] = back + timedelta(hours=1)
                member = self.rng.choice(regulars)
                plan = {}
                events.append(Event(when, "borrow", member, copy, plan))
                events.append(Event(back, "return", member, copy, plan))
        events.sort(key=lambda e: (e.when, e.kind == "borrow"))
        staff = [self.librarian, self.assistant]
        for event in events:
            try:
                if event.kind == "borrow":
                    event.plan["loan"] = borrow(event.member, event.copy, staff=self.rng.choice(staff), now=event.when)
                elif event.plan.get("loan"):
                    result = return_copy(event.copy, staff=self.rng.choice(staff), now=event.when)
                    if result.fee and self.rng.random() < 0.75:
                        fee_service.record_payment(
                            result.fee,
                            amount=result.fee.amount,
                            method=self.rng.choice(["cash", "cash", "card"]),
                            paid_on=timezone.localdate(event.when),
                            staff=self.librarian,
                            reference=f"R{self.rng.randint(10000, 99999)}",
                        )
            except CirculationError:
                continue

    def _available(self, title):
        return BookCopy.objects.filter(book__title=title, status="available").order_by("copy_number").first()

    def _lend(self, member, title, days_ago, staff=None):
        copy = self._available(title)
        if copy is None:
            return None
        when = timezone.make_aware(datetime.combine(timezone.localdate() - timedelta(days=days_ago), time(10, 30)))
        try:
            return borrow(member, copy, staff=staff or self.assistant, now=when)
        except CirculationError:
            return None

    def current_state(self, now):
        m = self.members
        # Members paid their historical late fees at the desk.
        for member in m[:7]:
            if Fee.objects.filter(member=member, status=FeeStatus.PENDING).exists():
                fee_service.settle_all(
                    member, method="cash", staff=self.assistant, reference=f"R{self.rng.randint(10000, 99999)}"
                )
        # Navoda: due in 4 days, due tomorrow, and a reservation ready to collect
        self._lend(m[0], "Atomic Habits", 10)
        self._lend(m[0], "Sapiens", 13)
        # Due today / overdue
        self._lend(m[1], "Atomic Habits", 14)
        self._lend(m[1], "Dune", 18)  # 4 days overdue → estimated fee
        self._lend(m[2], "Atomic Habits", 6)
        self._lend(m[2], "Nineteen Eighty-Four", 25)  # 11 days overdue
        self._lend(m[3], "Harry Potter and the Philosopher's Stone", 3)
        self._lend(m[3], "Charlotte's Web", 12)
        self._lend(m[4], "Pride and Prejudice", 2)
        self._lend(m[5], "Murder on the Orient Express", 16)  # 2 days overdue
        self._lend(m[6], "The Alchemist", 8)
        self._lend(m[7], "Madol Doova", 5)
        self._lend(m[9], "The Hobbit", 1)
        self._lend(m[9], "Clean Code", 9)

        # Queue for Atomic Habits (all copies out) — FIFO
        atomic = Book.objects.get(title="Atomic Habits")
        for member in (m[4], m[6], m[3]):
            try:
                reservation_service.reserve(member, atomic, actor=member.user)
            except reservation_service.ReservationError:
                pass
        # Navoda reserved The Hobbit from home → ready for collection
        hobbit = Book.objects.get(title="The Hobbit")
        reservation_service.reserve(m[0], hobbit, actor=m[0].user)
        # Someone waiting for Clean Code (single copy on loan)
        reservation_service.reserve(m[5], Book.objects.get(title="Clean Code"), actor=m[5].user)

        # A lost book with replacement fee
        lost_loan = self._lend(m[5], "Rich Dad Poor Dad", 30)
        if lost_loan:
            mark_lost(lost_loan, staff=self.librarian)

        # A pending damage fee and a waived fee
        fee_service.add_fee(
            m[2],
            amount=Decimal("350.00"),
            kind="damage",
            description="Water damage to 'The Great Gatsby'",
            staff=self.librarian,
        )
        pending = Fee.objects.filter(status=FeeStatus.PENDING, kind="late").exclude(member=m[2]).first()
        if pending:
            fee_service.waive(pending, reason="First late return — goodwill", staff=self.librarian)

        # A book in maintenance
        repair = BookCopy.objects.filter(book__title="Pride and Prejudice", status="available").first()
        if repair:
            repair.status = "maintenance"
            repair.notes = "Spine being re-glued"
            repair.save()
