from django.test import SimpleTestCase

from apps.barcodes import code128
from apps.barcodes.isbn import is_valid_isbn10, is_valid_isbn13, to_isbn10, to_isbn13
from apps.catalogue import search
from apps.catalogue.availability import State, for_book
from apps.catalogue.models import CopyStatus
from apps.catalogue.services import DuplicateBarcode, add_copy, lookup_code
from apps.catalogue.shelves import describe
from apps.circulation.services import borrow

from .factories import LibraryTestCase, book, member


class IsbnTests(SimpleTestCase):
    def test_validation(self):
        self.assertTrue(is_valid_isbn13("9780261102217"))
        self.assertFalse(is_valid_isbn13("9780261102218"))
        self.assertTrue(is_valid_isbn10("0261102214"))
        self.assertTrue(is_valid_isbn10("080442957X"))

    def test_conversion(self):
        self.assertEqual(to_isbn13("0-261-10221-4"), "9780261102217")
        self.assertEqual(to_isbn13("978-0-261-10221-7"), "9780261102217")
        self.assertEqual(to_isbn10("9780261102217"), "0261102214")
        self.assertIsNone(to_isbn13("hello"))


def decode(values):
    """Reference decoder for the symbol values our encoder produces."""
    out, mode = [], "B" if values[0] == code128.START_B else "C"
    checksum = values[0] + sum(i * v for i, v in enumerate(values[1:-2], start=1))
    assert checksum % 103 == values[-2], "bad checksum"
    for v in values[1:-2]:
        if v == code128.SWITCH_TO_C:
            mode = "C"
        elif v == code128.SWITCH_TO_B:
            mode = "B"
        else:
            out.append(f"{v:02d}" if mode == "C" else chr(v + 32))
    return "".join(out)


class Code128Tests(SimpleTestCase):
    def test_round_trip(self):
        for data in ["LIB-2026-00482", "200000010003", "A1", "HELLO world 12345", "9780261102217", "X1234Y"]:
            self.assertEqual(decode(code128.encode(data)), data)

    def test_patterns_have_correct_widths(self):
        for index, pattern in enumerate(code128.PATTERNS):
            self.assertEqual(sum(map(int, pattern)), 13 if index == 106 else 11)

    def test_svg_escapes_label(self):
        svg = code128.svg("AB1", label='<script>"x"</script>')
        self.assertNotIn("<script>", svg)

    def test_rejects_non_ascii(self):
        with self.assertRaises(code128.BarcodeError):
            code128.encode("ගම")


class ShelfFinderTests(LibraryTestCase):
    def test_describe_with_category_code(self):
        from apps.catalogue.models import Category

        Category.objects.create(name="Fiction", slug="fiction", shelf_code="FIC")
        self.assertEqual(describe("FIC-A-04"), ["Fiction", "Shelf A", "Row 04"])
        self.assertEqual(describe(""), [])


class CopyRegistrationTests(LibraryTestCase):
    def test_copies_get_unique_ids_and_numbers(self):
        b = book("The Hobbit", copies=3)
        copies = list(b.copies.order_by("copy_number"))
        self.assertEqual([c.copy_number for c in copies], [1, 2, 3])
        self.assertTrue(all(c.copy_code.startswith("LIB-HOB-") for c in copies))
        self.assertEqual(len({c.barcode for c in copies}), 3)

    def test_duplicate_barcode_is_never_created(self):
        b = book(copies=1)
        existing = b.copies.first()
        with self.assertRaises(DuplicateBarcode) as ctx:
            add_copy(book(copies=0), barcode=existing.barcode.lower())
        self.assertEqual(ctx.exception.existing, existing)
        self.assertEqual(type(existing).objects.filter(barcode=existing.barcode).count(), 1)

    def test_generated_barcode_when_blank(self):
        copy = add_copy(book(copies=0))
        self.assertTrue(copy.barcode.isdigit() and len(copy.barcode) == 12)

    def test_lookup_code(self):
        b = book("Lookup", copies=1, isbn="9780261102217")
        m = member()
        self.assertEqual(lookup_code(b.copies.first().barcode).kind, "copy")
        self.assertEqual(lookup_code(m.member_id.lower()).kind, "member")
        self.assertEqual(lookup_code("0261102214").kind, "book")
        self.assertEqual(lookup_code("9780141036144").kind, "new_isbn")
        self.assertEqual(lookup_code("ZZZ-UNKNOWN").kind, "unknown")


class AvailabilityTests(LibraryTestCase):
    def test_states(self):
        b = book(copies=2)
        c1, c2 = b.copies.all()
        self.assertEqual(for_book(b).state, State.AVAILABLE)
        borrow(member(), c1, staff=self.librarian)
        borrow(member(), c2, staff=self.librarian)
        self.assertEqual(for_book(b).state, State.BORROWED)
        empty = book(copies=1)
        copy = empty.copies.first()
        copy.status = CopyStatus.MAINTENANCE
        copy.save()
        self.assertEqual(for_book(empty).state, State.UNAVAILABLE)


class SearchTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.hp1 = book(
            "Harry Potter and the Philosopher's Stone", author="J.K. Rowling", category="Fantasy", isbn="9780747532699"
        )
        self.hp2 = book("Harry Potter and the Chamber of Secrets", author="J.K. Rowling", category="Fantasy")
        self.hp3 = book(
            "Harry Potter and the Prisoner of Azkaban", author="J.K. Rowling", category="Fantasy", language="English"
        )
        self.hobbit = book("The Hobbit", author="J.R.R. Tolkien", category="Fantasy", publication_year=1937)
        self.madol = book("Madol Doova", author="Martin Wickramasinghe", category="Sri Lankan", language="Sinhala")

    def run_search(self, **kw):
        return search.search(search.Filters(**kw))

    def test_partial_search_returns_all_harry_potter_books(self):
        titles = {b.title for b in self.run_search(q="harry").books}
        self.assertEqual(titles, {self.hp1.title, self.hp2.title, self.hp3.title})

    def test_search_by_author_and_partial_words(self):
        self.assertEqual({b.pk for b in self.run_search(q="rowl pris").books}, {self.hp3.pk})
        self.assertIn(self.hobbit.pk, {b.pk for b in self.run_search(q="tolkien").books})

    def test_search_ignores_punctuation_and_case(self):
        self.assertEqual({b.pk for b in self.run_search(q="PHILOSOPHERS stone").books}, {self.hp1.pk})

    def test_exact_isbn_and_barcode_find_book(self):
        self.assertEqual(self.run_search(q="0-7475-3269-9").direct, self.hp1)
        self.assertEqual(self.run_search(q=self.hobbit.copies.first().barcode).direct, self.hobbit)

    def test_typo_is_corrected(self):
        outcome = self.run_search(q="hobit")
        self.assertEqual(outcome.corrected, "hobbit")
        self.assertEqual([b.pk for b in outcome.books], [self.hobbit.pk])

    def test_filters(self):
        self.assertEqual({b.pk for b in self.run_search(language="Sinhala").books}, {self.madol.pk})
        self.assertEqual({b.pk for b in self.run_search(year_from=1930, year_to=1940).books}, {self.hobbit.pk})
        author = self.hobbit.authors.first()
        self.assertEqual({b.pk for b in self.run_search(author=author.pk).books}, {self.hobbit.pk})

    def test_available_filter(self):
        borrow(member(), self.hobbit.copies.first(), staff=self.librarian)
        self.assertNotIn(self.hobbit.pk, {b.pk for b in self.run_search(available=True).books})

    def test_archived_books_hidden(self):
        self.hobbit.is_archived = True
        self.hobbit.save()
        self.assertNotIn(self.hobbit.pk, {b.pk for b in self.run_search(q="hobbit").books})

    def test_suggest_endpoint(self):
        response = self.client.get("/api/suggest/?q=harr")
        data = response.json()
        self.assertEqual(len(data["books"]), 3)
        self.assertTrue(all("Harry" in b["title"] for b in data["books"]))
        self.assertEqual(data["authors"], [])

    def test_catalogue_page_search_and_redirect(self):
        response = self.client.get("/books/?q=harry")
        self.assertContains(response, "Harry Potter and the Chamber of Secrets")
        response = self.client.get("/books/?q=9780747532699")
        self.assertRedirects(response, self.hp1.get_absolute_url())
