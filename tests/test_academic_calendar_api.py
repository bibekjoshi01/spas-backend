"""The academic calendar: its grid, its weekend policy, and its marked dates."""

import datetime
from unittest.mock import patch

import nepali_datetime as nd
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status

from src.academics.constants import CalendarEntryKind, Weekday
from src.academics.models import (
    AcademicCalendarConfiguration,
    AcademicCalendarEntry,
    Batch,
    Department,
    Program,
)
from src.students.models import Student, StudentPortalConfiguration
from src.user.models import User, UserRole
from tests.base import INTERNAL, TenantAPITestCase

CALENDAR = f"{INTERNAL}/academics-mod/calendar"
ENTRIES = f"{INTERNAL}/academics-mod/calendar-entries"
CALENDAR_PORTAL = f"{INTERNAL}/academics-mod/student-portal/calendar"


class AcademicCalendarGridTests(TenantAPITestCase):
    """The year the frontend draws."""

    def setUp(self):
        super().setUp()
        self.authenticate_as_admin()

    def test_the_year_carries_the_palette_for_whoever_draws_it(self):
        response = self.client.get(CALENDAR + "/year", {"system": "BS", "year": 2082})
        theme = response.json()["theme"]
        assert theme["accentColor"] == "#334155"
        assert theme["downloadBandColor"] == "#bf0800"
        assert theme["showGregorianDates"] is True

    def test_bikram_sambat_year_matches_the_shipped_conversion_table(self):
        response = self.client.get(CALENDAR + "/year", {"system": "BS", "year": 2082})
        assert response.status_code == status.HTTP_200_OK, response.data

        months = response.json()["months"]
        assert len(months) == 12
        # A BS year is 365 or 366 days made of 29-32 day months; these are the
        # lengths the packaged table gives for 2082.
        assert [len(month["days"]) for month in months] == [
            31,
            31,
            32,
            31,
            31,
            31,
            30,
            29,
            30,
            29,
            30,
            30,
        ]
        assert months[0]["name"] == "Baishakh"
        assert months[0]["nameNepali"] == "वैशाख"
        # Baishakh 1 of 2082 is 14 April 2025, and every cell carries the
        # Gregorian date the entries are actually stored against.
        assert months[0]["days"][0]["date"] == "2025-04-14"
        assert months[0]["days"][0]["dayLabel"] == "१"

    def test_gregorian_year_is_a_gregorian_grid(self):
        response = self.client.get(CALENDAR + "/year", {"system": "AD", "year": 2024})
        assert response.status_code == status.HTTP_200_OK, response.data
        months = response.json()["months"]
        assert [len(month["days"]) for month in months] == [
            31,
            29,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ]
        assert months[1]["name"] == "February"

    def test_year_defaults_to_today_in_the_requested_system(self):
        today = timezone.localdate()
        response = self.client.get(CALENDAR + "/year", {"system": "AD"})
        assert response.json()["year"] == today.year

        response = self.client.get(CALENDAR + "/year")
        body = response.json()
        assert body["system"] == "BS"
        assert body["year"] == nd.date.from_datetime_date(today).year

    def test_unknown_system_and_unreachable_year_are_field_errors(self):
        response = self.client.get(CALENDAR + "/year", {"system": "MAYAN"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "system" in response.json()

        response = self.client.get(CALENDAR + "/year", {"system": "BS", "year": 1200})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "year" in response.json()

    def test_default_year_uses_the_configured_local_date(self):
        with patch(
            "src.academics.calendar.timezone.localdate", return_value=datetime.date(2027, 1, 1)
        ):
            response = self.client.get(CALENDAR + "/year", {"system": "AD"})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["year"] == 2027

    def test_inactive_entries_leave_the_grid(self):
        AcademicCalendarEntry.objects.create(
            date=datetime.date(2025, 4, 14),
            title="Cancelled",
            is_active=False,
            created_by=self.admin,
        )
        response = self.client.get(CALENDAR + "/year", {"system": "BS", "year": 2082})
        assert response.json()["months"][0]["days"][0]["entries"] == []

    def test_weekend_policy_marks_the_right_cells(self):
        AcademicCalendarConfiguration.objects.create(
            singleton_key=True,
            weekend_days=[Weekday.SATURDAY.value, Weekday.SUNDAY.value],
            created_by=self.admin,
        )
        response = self.client.get(CALENDAR + "/year", {"system": "AD", "year": 2026})
        january = response.json()["months"][0]["days"]
        for day in january:
            weekday = datetime.date.fromisoformat(day["date"]).isoweekday()
            assert day["isWeekend"] is (weekday in (6, 7)), day

    def test_entries_land_on_the_day_they_were_saved_against(self):
        AcademicCalendarEntry.objects.create(
            date=datetime.date(2025, 4, 14),
            kind=CalendarEntryKind.HOLIDAY.value,
            title="Nepali New Year",
            note="Campus closed.",
            created_by=self.admin,
        )
        response = self.client.get(CALENDAR + "/year", {"system": "BS", "year": 2082})
        first_day = response.json()["months"][0]["days"][0]
        assert len(first_day["entries"]) == 1
        assert first_day["entries"][0]["title"] == "Nepali New Year"
        assert first_day["entries"][0]["nepaliDate"] == "2082-01-01"

    def test_archived_entries_leave_the_grid(self):
        entry = AcademicCalendarEntry.objects.create(
            date=datetime.date(2025, 4, 14),
            title="Removed",
            created_by=self.admin,
        )
        entry.is_archived = True
        entry.save()
        response = self.client.get(CALENDAR + "/year", {"system": "BS", "year": 2082})
        assert response.json()["months"][0]["days"][0]["entries"] == []


class AcademicCalendarSettingsTests(TenantAPITestCase):
    """Who may say which days the college rests on."""

    def setUp(self):
        super().setUp()
        self.head = self.make_user("head", "DEPARTMENT-HEAD")

    def test_default_weekend_is_saturday_without_writing_a_row(self):
        self.authenticate_as_admin()
        response = self.client.get(CALENDAR + "/settings")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["weekendDays"] == [Weekday.SATURDAY.value]
        assert not AcademicCalendarConfiguration.objects.exists()

    def test_superuser_sets_the_weekend(self):
        self.authenticate_as_admin()
        response = self.client.put(
            CALENDAR + "/settings",
            {"weekendDays": [Weekday.SUNDAY.value, Weekday.SATURDAY.value]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.json()["weekendDays"] == [6, 7]
        configuration = AcademicCalendarConfiguration.objects.get()
        assert configuration.updated_by == self.admin

    def test_a_week_of_holidays_is_refused(self):
        self.authenticate_as_admin()
        response = self.client.put(
            CALENDAR + "/settings",
            {"weekendDays": [1, 2, 3, 4, 5, 6, 7]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "weekendDays" in response.json()

    def test_a_repeated_day_is_refused(self):
        self.authenticate_as_admin()
        response = self.client.put(CALENDAR + "/settings", {"weekendDays": [6, 6]}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_management_reads_the_policy_but_may_not_set_it(self):
        self.authenticate("head")
        assert self.client.get(CALENDAR + "/settings").status_code == status.HTTP_200_OK
        response = self.client.put(CALENDAR + "/settings", {"weekendDays": [7]}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_anonymous_callers_see_nothing(self):
        self.client.credentials()
        assert self.client.get(CALENDAR + "/settings").status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert self.client.get(CALENDAR + "/year").status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


class AcademicCalendarThemeTests(TenantAPITestCase):
    """The palette a college prints its calendar in."""

    def setUp(self):
        super().setUp()
        self.head = self.make_user("head", "DEPARTMENT-HEAD")

    def test_the_shipped_palette_is_returned_before_anything_is_saved(self):
        self.authenticate_as_admin()
        body = self.client.get(CALENDAR + "/settings").json()
        assert body["themeAccentColor"] == "#334155"
        assert body["themeHolidayColor"] == "#dc2626"
        assert body["themeEventColor"] == "#0064be"
        # The printed calendar keeps the red a wall chart has always worn.
        assert body["themeDownloadBandColor"] == "#bf0800"
        assert body["showGregorianDates"] is True
        assert not AcademicCalendarConfiguration.objects.exists()

    def test_a_superuser_sets_the_palette(self):
        self.authenticate_as_admin()
        response = self.client.put(
            CALENDAR + "/settings",
            {
                "weekendDays": [Weekday.SATURDAY.value],
                "themeAccentColor": "#7B1FA2",
                "themeHolidayColor": "#B71C1C",
                "themeEventColor": "#00695C",
                "themeDownloadBandColor": "#4A148C",
                "showGregorianDates": False,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        body = response.json()
        # Stored in one spelling, so a brand colour cannot end up as two rows.
        assert body["themeAccentColor"] == "#7b1fa2"
        assert body["showGregorianDates"] is False
        configuration = AcademicCalendarConfiguration.objects.get()
        assert configuration.theme_holiday_color == "#b71c1c"
        assert configuration.theme_download_band_color == "#4a148c"

    def test_saving_one_part_leaves_the_other_alone(self):
        # The weekend panel and the theme dialog are separate screens writing
        # to one row; neither may reset what the other set.
        self.authenticate_as_admin()
        self.client.put(
            CALENDAR + "/settings",
            {"themeAccentColor": "#7b1fa2", "showGregorianDates": False},
            format="json",
        )
        self.client.put(CALENDAR + "/settings", {"weekendDays": [6, 7]}, format="json")
        body = self.client.get(CALENDAR + "/settings").json()
        assert body["weekendDays"] == [6, 7]
        assert body["themeAccentColor"] == "#7b1fa2"
        assert body["showGregorianDates"] is False

    def test_a_colour_that_is_not_a_hex_value_is_a_field_error(self):
        self.authenticate_as_admin()
        for field in ("themeAccentColor", "themeDownloadBandColor"):
            for bad in ("red", "#fff", "1d4ed8", "#12345g"):
                response = self.client.put(
                    CALENDAR + "/settings",
                    {"weekendDays": [6], field: bad},
                    format="json",
                )
                assert response.status_code == status.HTTP_400_BAD_REQUEST, (field, bad)
                assert field in response.json(), (field, bad)

    def test_management_reads_the_palette_but_may_not_set_it(self):
        self.authenticate("head")
        assert self.client.get(CALENDAR + "/settings").status_code == status.HTTP_200_OK
        response = self.client.put(
            CALENDAR + "/settings",
            {"weekendDays": [6], "themeAccentColor": "#7b1fa2"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


class AcademicCalendarEntryTests(TenantAPITestCase):
    """Marking a date, and who may do it."""

    def setUp(self):
        super().setUp()
        self.head = self.make_user("head", "DEPARTMENT-HEAD")
        self.teacher = self.make_user("teacher", "TEACHER")

    def test_superuser_marks_a_holiday(self):
        self.authenticate_as_admin()
        response = self.client.post(
            ENTRIES,
            {
                "date": "2025-10-02",
                "kind": CalendarEntryKind.HOLIDAY.value,
                "title": "Ghatasthapana",
                "note": "Dashain begins.",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        entry = AcademicCalendarEntry.objects.get(pk=response.data["id"])
        assert entry.created_by == self.admin
        assert entry.kind == CalendarEntryKind.HOLIDAY.value

    def test_one_date_may_carry_several_entries(self):
        self.authenticate_as_admin()
        for title in ("Ghatasthapana", "Result publication"):
            response = self.client.post(
                ENTRIES, {"date": "2025-10-02", "title": title}, format="json"
            )
            assert response.status_code == status.HTTP_201_CREATED, response.data
        assert AcademicCalendarEntry.objects.filter(date="2025-10-02").count() == 2

    def test_the_same_title_twice_on_one_date_is_refused(self):
        self.authenticate_as_admin()
        payload = {"date": "2025-10-02", "title": "Ghatasthapana"}
        assert self.client.post(ENTRIES, payload, format="json").status_code == (
            status.HTTP_201_CREATED
        )
        response = self.client.post(ENTRIES, payload, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_blank_title_is_refused(self):
        self.authenticate_as_admin()
        response = self.client.post(ENTRIES, {"date": "2025-10-02", "title": "   "}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "title" in response.json()

    def test_a_date_the_nepali_table_cannot_reach_is_a_field_error(self):
        self.authenticate_as_admin()
        response = self.client.post(
            ENTRIES, {"date": "1800-01-01", "title": "Too early"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "date" in response.json()

    def test_removing_an_entry_archives_rather_than_deletes(self):
        self.authenticate_as_admin()
        created = self.client.post(
            ENTRIES, {"date": "2025-10-02", "title": "Ghatasthapana"}, format="json"
        )
        response = self.client.delete(f"{ENTRIES}/{created.data['id']}")
        assert response.status_code == status.HTTP_200_OK, response.data
        entry = AcademicCalendarEntry.objects.get(pk=created.data["id"])
        assert entry.is_archived is True
        assert entry.updated_by == self.admin

    def test_management_and_teachers_read_the_calendar(self):
        AcademicCalendarEntry.objects.create(
            date=datetime.date(2025, 10, 2), title="Ghatasthapana", created_by=self.admin
        )
        for persona in ("head", "teacher"):
            self.authenticate(persona)
            response = self.client.get(ENTRIES)
            assert response.status_code == status.HTTP_200_OK, persona
            assert response.json()["count"] == 1

    def test_management_may_not_mark_dates(self):
        self.authenticate("head")
        response = self.client.post(
            ENTRIES, {"date": "2025-10-02", "title": "Not mine to set"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not AcademicCalendarEntry.objects.exists()

    def test_teachers_may_not_change_or_remove_entries(self):
        entry = AcademicCalendarEntry.objects.create(
            date=datetime.date(2025, 10, 2), title="Ghatasthapana", created_by=self.admin
        )
        self.authenticate("teacher")
        assert (
            self.client.patch(
                f"{ENTRIES}/{entry.pk}", {"title": "Changed"}, format="json"
            ).status_code
            == status.HTTP_403_FORBIDDEN
        )
        assert self.client.delete(f"{ENTRIES}/{entry.pk}").status_code == status.HTTP_403_FORBIDDEN
        entry.refresh_from_db()
        assert entry.title == "Ghatasthapana"
        assert entry.is_archived is False

    def test_a_date_range_filter_narrows_the_list(self):
        self.authenticate_as_admin()
        for day, title in ((2, "October"), (2, "November")):
            month = 10 if title == "October" else 11
            AcademicCalendarEntry.objects.create(
                date=datetime.date(2025, month, day), title=title, created_by=self.admin
            )
        response = self.client.get(ENTRIES, {"date_from": "2025-11-01", "date_to": "2025-11-30"})
        assert response.json()["count"] == 1
        assert response.json()["results"][0]["title"] == "November"

    def test_invalid_date_filters_are_field_errors(self):
        self.authenticate_as_admin()
        for params, field in (
            ({"date_from": "not-a-date"}, "dateFrom"),
            ({"date_to": "2025-02-30"}, "dateTo"),
            ({"date_from": "2025-11-01", "date_to": "2025-10-01"}, "dateTo"),
        ):
            response = self.client.get(ENTRIES, params)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
            assert field in response.json(), response.json()

    def test_model_refuses_dates_that_cannot_be_rendered(self):
        with self.assertRaises(ValidationError) as error:
            AcademicCalendarEntry.objects.create(
                date=datetime.date(1800, 1, 1), title="Unreachable", created_by=self.admin
            )
        assert "date" in error.exception.message_dict

    def test_correction_preserves_creator_and_records_actor(self):
        entry = AcademicCalendarEntry.objects.create(
            date=datetime.date(2025, 10, 2), title="Original", created_by=self.head
        )
        self.authenticate_as_admin()
        response = self.client.patch(f"{ENTRIES}/{entry.pk}", {"title": "Corrected"}, format="json")
        assert response.status_code == status.HTTP_200_OK, response.data
        entry.refresh_from_db()
        assert entry.created_by == self.head
        assert entry.updated_by == self.admin
        assert entry.history.first().history_user == self.admin


class StudentPortalCalendarTests(TenantAPITestCase):
    """A student sees the same calendar, through the portal's own conditions."""

    def setUp(self):
        super().setUp()
        department = Department.objects.create(name="Science", code="SCI", created_by=self.admin)
        program = Program.objects.create(
            department=department, name="BSc CSIT", code="CSIT", created_by=self.admin
        )
        batch = Batch.objects.create(program=program, year=2080, created_by=self.admin)
        self.student_user = User.objects.create_user(
            username="student-1",
            email="student-1@college.edu",
            password=self.password,
            include_system_role=False,
        )
        self.student_user.roles.add(UserRole.objects.get(codename="STUDENT"))
        self.student_user.must_change_password = False
        self.student_user.save(update_fields=["must_change_password"])
        Student.objects.create(
            user=self.student_user,
            batch=batch,
            roll_number="01",
            first_name="Sita",
            last_name="Sharma",
            created_by=self.admin,
        )
        StudentPortalConfiguration.objects.create(login_enabled=True, created_by=self.admin)
        AcademicCalendarEntry.objects.create(
            date=datetime.date(2025, 4, 14),
            kind=CalendarEntryKind.HOLIDAY.value,
            title="Nepali New Year",
            created_by=self.admin,
        )

    def test_a_student_reads_the_same_year(self):
        self.authenticate("student-1")
        response = self.client.get(f"{CALENDAR_PORTAL}/year", {"system": "BS", "year": 2082})
        assert response.status_code == status.HTTP_200_OK, response.data
        body = response.json()
        assert len(body["months"]) == 12
        assert body["months"][0]["days"][0]["entries"][0]["title"] == "Nepali New Year"

    def test_a_student_is_refused_the_staff_calendar(self):
        self.authenticate("student-1")
        for url in (f"{CALENDAR}/year", f"{CALENDAR}/settings", ENTRIES):
            assert self.client.get(url).status_code == status.HTTP_403_FORBIDDEN, url

    def test_a_student_may_not_mark_dates(self):
        self.authenticate("student-1")
        response = self.client.post(
            ENTRIES, {"date": "2025-10-02", "title": "Not mine"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_portal_calendar_closes_with_the_portal(self):
        StudentPortalConfiguration.objects.update(login_enabled=False)
        self.authenticate_as_admin()
        # The admin still reaches the staff calendar; the portal one is a
        # student surface and refuses even a superuser, who has no profile.
        assert self.client.get(f"{CALENDAR}/year").status_code == status.HTTP_200_OK
        assert self.client.get(f"{CALENDAR_PORTAL}/year").status_code == status.HTTP_403_FORBIDDEN

    def test_existing_student_session_is_refused_when_portal_closes(self):
        self.authenticate("student-1")
        configuration = StudentPortalConfiguration.current()
        configuration.login_enabled = False
        configuration.save()
        assert self.client.get(f"{CALENDAR_PORTAL}/year").status_code == status.HTTP_403_FORBIDDEN

    def test_existing_student_session_requires_password_change(self):
        self.authenticate("student-1")
        self.student_user.must_change_password = True
        self.student_user.save(update_fields=["must_change_password"])
        assert self.client.get(f"{CALENDAR_PORTAL}/year").status_code == status.HTTP_403_FORBIDDEN

    def test_staff_without_the_permission_are_refused(self):
        self.make_user("plain")
        self.authenticate("plain")
        assert self.client.get(f"{CALENDAR}/year").status_code == status.HTTP_403_FORBIDDEN
